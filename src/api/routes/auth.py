# api/routes/auth.py
"""
认证 API 路由

提供端点：
1. POST /auth/register              —— 用户名+密码注册（兼容旧版）
2. POST /auth/register-with-email   —— 邮箱+验证码注册（推荐）
3. POST /auth/send-code             —— 发送邮箱验证码
4. POST /auth/login                 —— 登录（用户名或邮箱）
5. POST /auth/refresh               —— 刷新 Access Token
6. POST /auth/logout                —— 登出（撤销当前 access + 传入的 refresh token）
7. POST /auth/change-password       —— 修改密码（需登录，改密后撤销当前 token）
8. POST /auth/forgot-password       —— 通过安全问题重置密码
9. GET  /auth/security-question     —— 查询某账号的安全问题
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status, Header
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel, Field
from datetime import timedelta, datetime
from typing import Optional
import re

from src.repositories.session import get_db
from src.models.db_models import User, UserProfile, FitnessGoal
from src.services.auth_service import (
    pwd_context, hash_password, oauth2_scheme,
    create_access_token, create_refresh_token,
    verify_refresh_token, authenticate_user,
    change_password as _change_password,
    reset_password_via_security as _reset_password,
    get_current_user_id, get_current_user_optional,
    decode_token, revoke_token, is_token_revoked,
    _calc_token_ttl_seconds,
    ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS,
    KEY_TTL_ACCESS_BLACKLIST, KEY_TTL_REFRESH_BLACKLIST,
)
from src.services.user_service import get_or_create_user
from src.services.login_limiter import (
    check_login_allowed, record_login_failure,
    record_login_success, get_remaining_attempts,
)
from src.services.email_service import send_verification_code, verify_code
from src.utils.password_policy import validate_username, validate_password
from src.utils.logger import setup_logger
from src.config.settings import (
    ACCESS_TOKEN_EXPIRE_MINUTES as AC,
    REFRESH_TOKEN_EXPIRE_DAYS as RTD,
)

logger = setup_logger(name="AuthRouter")

router = APIRouter(prefix="/auth", tags=["认证"])

ACCESS_TOKEN_EXPIRE_MINUTES = AC


# ============================================================
# 数据模型
# ============================================================
class LoginRequest(BaseModel):
    """登录请求体（兼容用户名或邮箱）"""
    username: str = Field(..., description="用户名或邮箱")
    password: str


class RegisterRequest(BaseModel):
    """用户名+密码注册"""
    username: str
    password: str
    security_question: Optional[str] = Field(default=None, description="安全问题（可选，用于忘记密码重置）")
    security_answer: Optional[str] = Field(default=None, description="安全问题答案")
    role: Optional[str] = Field(default="user", description="注册角色: user/expert（管理员需在数据库直接设置）")


class EmailRegisterRequest(BaseModel):
    """邮箱+验证码注册"""
    email: str
    password: str
    code: str = Field(..., description="邮箱验证码")
    security_question: Optional[str] = None
    security_answer: Optional[str] = None
    role: Optional[str] = Field(default="user", description="注册角色: user/expert（管理员需在数据库直接设置）")


class SendCodeRequest(BaseModel):
    email: str


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _validate_email(email: str) -> Optional[str]:
    """邮箱格式校验，返回 None 表示合法，否则返回错误信息"""
    if not email or not _EMAIL_RE.match(email):
        return "邮箱格式不正确"
    return None


class TokenResponse(BaseModel):
    """Token 响应体"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str
    # 可选：客户端传当前 refresh_token，改密成功后一并撤销
    refresh_token: Optional[str] = Field(
        default=None,
        description="可选：当前 refresh_token，改密成功后会一并撤销",
    )


class LogoutRequest(BaseModel):
    """登出请求体

    refresh_token 可选；若客户端只传 access token 头，可不传 body
    """
    refresh_token: Optional[str] = Field(
        default=None,
        description="可选：refresh_token，传入则一并撤销",
    )


class ForgotPasswordRequest(BaseModel):
    username: str = Field(..., description="用户名或邮箱")
    security_answer: str
    new_password: str


# ============================================================
# 工具函数
# ============================================================
def _get_client_ip(request: Request) -> str:
    """获取客户端真实 IP（支持反向代理）"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _set_security_question(user: User, question: Optional[str], answer: Optional[str]):
    """若提供安全问题和答案，则哈希保存"""
    if question and answer:
        user.security_question = question
        user.security_answer_hash = hash_password(answer.strip().lower())


# ============================================================
# 1. 用户名注册（兼容旧版）
# ============================================================
@router.post("/register")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """用户名+密码注册"""
    # 校验用户名和密码强度
    ok, msg = validate_username(body.username)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    ok, msg = validate_password(body.password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    # 角色校验
    role = (body.role or "user").lower()
    if role not in ("user", "expert"):
        raise HTTPException(status_code=400, detail="仅支持注册普通用户或专家，管理员需在数据库直接设置")

    # 检查用户是否已存在
    existing = db.query(User).filter(
        or_(User.username == body.username, User.external_id == body.username)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="该账号已注册")

    user = get_or_create_user(
        db=db,
        external_id=body.username,
        source="username",
        password=body.password,
        nickname=body.username,
    )
    if not user.username:
        user.username = body.username

    # 设置角色 + 审批状态：专家默认需审批
    user.role = role
    user.approved = (role != "expert")  # 专家默认待审批

    _set_security_question(user, body.security_question, body.security_answer)
    db.commit()

    logger.info(f"新用户注册成功: {body.username}, 角色={role}, 审批通过={user.approved}")
    result = {"message": "注册成功", "external_id": user.external_id, "role": role}
    if role == "expert":
        result["message"] = "注册成功，专家账号需等待管理员审批后方可登录"
        result["need_approval"] = True
    return result


# ============================================================
# 2. 发送邮箱验证码
# ============================================================
@router.post("/send-code")
def send_code(body: SendCodeRequest):
    """向邮箱发送 6 位数字验证码"""
    err = _validate_email(body.email)
    if err:
        raise HTTPException(status_code=400, detail=err)
    success, error = send_verification_code(body.email)
    if not success:
        raise HTTPException(status_code=429, detail=error)
    return {"message": "验证码已发送，请注意查收"}


# ============================================================
# 3. 邮箱注册（推荐）
# ============================================================
@router.post("/register-with-email")
def register_with_email(body: EmailRegisterRequest, db: Session = Depends(get_db)):
    """邮箱+验证码注册"""
    # 校验邮箱格式
    err = _validate_email(body.email)
    if err:
        raise HTTPException(status_code=400, detail=err)

    # 校验密码强度
    ok, msg = validate_password(body.password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    # 角色校验
    role = (body.role or "user").lower()
    if role not in ("user", "expert"):
        raise HTTPException(status_code=400, detail="仅支持注册普通用户或专家，管理员需在数据库直接设置")

    # 校验验证码
    valid, err = verify_code(body.email, body.code)
    if not valid:
        raise HTTPException(status_code=400, detail=err)

    # 检查邮箱是否已注册
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="该邮箱已注册")

    # 创建用户
    external_id = f"email:{body.email}"  # 用前缀避免与用户名冲突
    user = get_or_create_user(
        db=db,
        external_id=external_id,
        source="email",
        password=body.password,
    )
    user.email = body.email
    user.email_verified = True

    # 设置角色 + 审批状态：专家默认需审批
    user.role = role
    user.approved = (role != "expert")  # 专家默认待审批

    _set_security_question(user, body.security_question, body.security_answer)
    db.commit()

    logger.info(f"邮箱注册成功: {body.email}, 角色={role}, 审批通过={user.approved}")
    result = {"message": "注册成功", "email": body.email, "role": role}
    if role == "expert":
        result["message"] = "注册成功，专家账号需等待管理员审批后方可登录"
        result["need_approval"] = True
    return result


# ============================================================
# 4. 登录
# ============================================================
@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """登录接口（支持用户名或邮箱）

    - 限流：账号 5 次失败 / 5 分钟，IP 20 次 / 5 分钟，锁定 15 分钟
    - 登录成功后清除该账号的失败计数
    """
    ip = _get_client_ip(request)

    # 1. 限流检查
    allowed, lock_msg = check_login_allowed(body.username, ip)
    if not allowed:
        raise HTTPException(status_code=429, detail=lock_msg)

    # 2. 认证
    user = authenticate_user(db, body.username, body.password)
    if not user:
        record_login_failure(body.username, ip)
        remaining = get_remaining_attempts(body.username)

        existing_user = db.query(User).filter(
            or_(User.username == body.username, User.email == body.username)
        ).first()

        if not existing_user:
            detail = f"未查询到该用户信息，请先注册"
        else:
            detail = f"账号或密码错误，剩余 {remaining} 次尝试机会"

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 2.5 审批检查：专家必须审批通过
    if user.role == "expert" and not user.approved:
        record_login_failure(body.username, ip)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="专家账号尚未通过管理员审批，请耐心等待",
        )

    # 3. 登录成功
    record_login_success(body.username, ip)
    user.last_login_at = datetime.utcnow()
    user.last_login_ip = ip
    db.commit()

    access_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_expires = timedelta(days=RTD)

    access_token = create_access_token(
        data={"sub": user.external_id},
        expires_delta=access_expires,
    )
    refresh_token = create_refresh_token(
        data={"sub": user.external_id},
        expires_delta=refresh_expires,
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": int(access_expires.total_seconds()),
    }


# ============================================================
# 5. 刷新 Token
# ============================================================
@router.post("/refresh", response_model=TokenResponse)
def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    """使用 Refresh Token 换取新的 Access Token

    - Refresh Token 有效期 7 天
    - 不能直接用于业务接口
    - 换取新 Access Token 时会同时签发新 Refresh Token（滑动过期）
    """
    sub = verify_refresh_token(body.refresh_token)

    # 校验用户仍然存在且激活
    user = db.query(User).filter(
        User.external_id == sub, User.is_active == True
    ).first()
    if not user:
        raise HTTPException(
            status_code=401,
            detail="用户不存在或已被禁用",
        )

    access_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_expires = timedelta(days=RTD)

    new_access = create_access_token(
        data={"sub": user.external_id},
        expires_delta=access_expires,
    )
    new_refresh = create_refresh_token(
        data={"sub": user.external_id},
        expires_delta=refresh_expires,
    )

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
        "expires_in": int(access_expires.total_seconds()),
    }


# ============================================================
# 6. 修改密码（需登录）
# ============================================================
@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """修改密码（需要登录）

    - 需要校验旧密码
    - 新密码不能与旧密码相同
    - 修改成功后会撤销当前 access token 与（若传入）refresh token，
      客户端应引导用户重新登录
    """
    user = db.query(User).filter(User.external_id == current_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 校验新密码强度
    ok, msg = validate_password(body.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    try:
        success = _change_password(db, user, body.old_password, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not success:
        raise HTTPException(status_code=400, detail="旧密码错误")

    # 撤销当前 access token
    _revoke_access_token_from_header(token, label="change_password access")
    # 若客户端传了 refresh_token，一并撤销
    if body.refresh_token:
        _revoke_refresh_token_if_valid(body.refresh_token, label="change_password refresh")

    return {"message": "密码修改成功，请重新登录"}


# ============================================================
# 6.5 登出（撤销 token）
# ============================================================
@router.post("/logout")
def logout(
    token: str = Depends(oauth2_scheme),
    body: Optional[LogoutRequest] = None,
    current_user_id: str = Depends(get_current_user_id),
):
    """登出

    - 撤销当前 access token（写入 Redis 黑名单，TTL 与原 token 对齐）
    - 若 body 中带 refresh_token，则一并撤销
    - Redis 不可用时 fail-open：接口返回 200，但 token 实际未撤销，仅靠自然过期

    客户端应在调用后清除本地存储的 access / refresh token。
    """
    revoked_access = _revoke_access_token_from_header(token, label="logout access")

    revoked_refresh = False
    if body and body.refresh_token:
        revoked_refresh = _revoke_refresh_token_if_valid(
            body.refresh_token, label="logout refresh"
        )

    return {
        "message": "已登出",
        "access_token_revoked": revoked_access,
        "refresh_token_revoked": revoked_refresh,
    }


# ============================================================
# Token 撤销辅助函数
# ============================================================
def _revoke_access_token_from_header(token: str, label: str) -> bool:
    """撤销 access token：解码获取 jti 与 exp，写入黑名单"""
    try:
        payload = decode_token(token)
    except Exception as e:
        # token 无效或已过期：本来就不能用了，无需撤销，仅记日志
        logger.info(f"[{label}] token 已无效或过期，跳过撤销: {e}")
        return False

    jti = payload.get("jti")
    if not jti:
        # 旧 token 无 jti，无法撤销；依赖自然过期
        logger.info(f"[{label}] token 无 jti 字段，跳过撤销（依赖自然过期）")
        return False

    ttl = _calc_token_ttl_seconds(payload, KEY_TTL_ACCESS_BLACKLIST)
    return revoke_token(jti, ttl, token_type="access")


def _revoke_refresh_token_if_valid(refresh_token: str, label: str) -> bool:
    """撤销 refresh token：先验证有效，再写黑名单

    失败原因（无效/过期/已撤销）均记日志但不抛异常，避免登出失败
    """
    try:
        sub = verify_refresh_token(refresh_token)
        # verify_refresh_token 内部已通过黑名单检查，到这里说明未撤销
    except HTTPException as e:
        logger.info(f"[{label}] refresh token 无法撤销（已无效或已撤销）: {e.detail}")
        return False

    try:
        payload = decode_token(refresh_token)
        jti = payload.get("jti")
        if not jti:
            logger.info(f"[{label}] refresh token 无 jti 字段，跳过撤销")
            return False
        ttl = _calc_token_ttl_seconds(payload, KEY_TTL_REFRESH_BLACKLIST)
        return revoke_token(jti, ttl, token_type="refresh")
    except Exception as e:
        logger.warning(f"[{label}] 撤销 refresh token 失败: {e}")
        return False


# ============================================================
# 7. 查询安全问题（用于忘记密码引导）
# ============================================================
@router.get("/security-question")
def get_security_question(username: str, db: Session = Depends(get_db)):
    """根据用户名或邮箱查询其设置的安全问题"""
    user = db.query(User).filter(
        or_(User.username == username, User.email == username)
    ).first()

    if not user:
        # 不暴露用户是否存在
        raise HTTPException(status_code=404, detail="用户不存在或未设置安全问题")
    if not user.security_question:
        raise HTTPException(status_code=404, detail="用户未设置安全问题")

    return {"security_question": user.security_question}


# ============================================================
# 8. 忘记密码 - 通过安全问题重置
# ============================================================
@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """通过安全问题答案重置密码

    - 不需要登录
    - 答案匹配后即可设置新密码
    """
    ok, msg = validate_password(body.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    try:
        success = _reset_password(
            db=db,
            login_key=body.username,
            security_answer=body.security_answer,
            new_password=body.new_password,
        )
    except Exception as e:
        logger.error(f"重置密码异常: {e}")
        raise HTTPException(status_code=500, detail="重置失败")

    if not success:
        raise HTTPException(
            status_code=400,
            detail="账号不存在、未设置安全问题或答案错误",
        )

    return {"message": "密码重置成功，请使用新密码登录"}


@router.get("/identity")
def get_identity(current_user=Depends(get_current_user_optional)):
    """获取当前用户身份信息（含角色），无需登录时返回游客状态"""
    from src.services.auth_service import is_guest
    if is_guest(current_user):
        return {
            "is_authenticated": False,
            "is_guest": True,
            "role": None,
            "username": None,
            "message": "未登录",
        }
    return {
        "is_authenticated": True,
        "is_guest": False,
        "role": current_user.role,
        "approved": current_user.approved if hasattr(current_user, "approved") else True,
        "username": current_user.username,
        "external_id": current_user.external_id,
    }
