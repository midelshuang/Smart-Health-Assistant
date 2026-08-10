# service/auth_service.py
"""
认证服务层

提供：
- 密码哈希与校验（bcrypt）
- Access Token / Refresh Token 生成与解析
- 用户认证（支持用户名或邮箱登录）
- 当前用户解析（FastAPI 依赖）
- 可选认证（游客模式）
- RBAC 角色权限控制
- 修改密码 / 重置密码（安全问题）
"""
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Optional
from src.repositories.session import get_db
from src.models.db_models import User
from passlib.context import CryptContext
from src.config.settings import SECRET_KEY, ALGORITHM
from src.utils.logger import setup_logger

logger = setup_logger(name="AuthService")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"

VALID_ROLES = {"user", "admin", "expert"}
ROLE_HIERARCHY = {"user": 0, "expert": 1, "admin": 2}


class GuestUser:
    """哨兵类：表示未登录的游客用户（单例）"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.is_guest = True
            cls._instance.external_id = "guest"
            cls._instance.role = "guest"
            cls._instance.username = None
            cls._instance.is_active = False
            cls._instance.id = None
        return cls._instance

    def __bool__(self):
        return False


guest = GuestUser()


def is_guest(user) -> bool:
    """判断当前用户是否为游客"""
    return isinstance(user, GuestUser)


# ============================================================
# 密码哈希
# ============================================================
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    if not hashed_password:
        return False
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception as e:
        logger.warning(f"密码验证异常: {e}")
        return False


def hash_password(plain_password: str) -> str:
    """生成密码哈希"""
    return pwd_context.hash(plain_password)


# ============================================================
# Token 生成
# ============================================================
def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    """生成 Access Token（短期，用于 API 鉴权）"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({
        "exp": expire,
        "type": TOKEN_TYPE_ACCESS,
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict, expires_delta: timedelta = None) -> str:
    """生成 Refresh Token（长期，仅用于换取新 Access Token）

    与 Access Token 区别：
    - 包含 type=refresh 标识
    - 有效期更长（默认 7 天）
    - 不能直接用于业务接口鉴权
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    to_encode.update({
        "exp": expire,
        "type": TOKEN_TYPE_REFRESH,
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """解码 Token，返回 payload

    Raises:
        JWTError: Token 无效或过期
    """
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def verify_refresh_token(token: str) -> str:
    """校验 Refresh Token 并返回用户标识 (sub)

    Raises:
        HTTPException 401: Token 无效/过期/类型不匹配
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的 Refresh Token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        if payload.get("type") != TOKEN_TYPE_REFRESH:
            raise credentials_exception
        sub = payload.get("sub")
        if not sub:
            raise credentials_exception
        return sub
    except JWTError:
        raise credentials_exception


# ============================================================
# 用户认证
# ============================================================
def authenticate_user(db: Session, login_key: str, password: str):
    """认证用户：支持用户名或邮箱登录

    Args:
        db: 数据库会话
        login_key: 用户名 或 邮箱
        password: 明文密码

    Returns:
        User 对象或 None
    """
    user = db.query(User).filter(
        or_(
            User.username == login_key,
            User.email == login_key,
        )
    ).first()

    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_current_user_id(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> str:
    """解析 Access Token 获取当前用户 ID（FastAPI 依赖）

    Raises:
        HTTPException 401: Token 无效 / 用户不存在 / Token 类型错误
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        # 拒绝 Refresh Token 用于业务接口
        if payload.get("type") not in (TOKEN_TYPE_ACCESS, None):
            # 兼容旧版未带 type 的 Token（视为 Access）
            raise credentials_exception
        external_id: str = payload.get("sub")
        if external_id is None or not external_id:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(
        User.external_id == external_id,
        User.is_active == True
    ).first()

    if not user:
        raise credentials_exception

    return user.external_id


def get_current_user_optional(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> User:
    """可选认证：有 Token 返回 User，无 Token 返回 Guest 哨兵

    用于游客可访问的端点（如 RAG 问答、TDEE 计算）。
    游客可以查询但无法使用用户专属功能（如计划生成、档案保存等）。
    """
    if not authorization or not authorization.startswith("Bearer "):
        return guest

    token = authorization[7:]
    try:
        payload = decode_token(token)
        if payload.get("type") not in (TOKEN_TYPE_ACCESS, None):
            return guest
        external_id: str = payload.get("sub")
        if external_id is None or not external_id:
            return guest
    except JWTError:
        return guest

    user = db.query(User).filter(
        User.external_id == external_id,
        User.is_active == True
    ).first()

    if not user:
        return guest

    return user


def require_role(*allowed_roles: str):
    """角色权限装饰器工厂

    用法：
        @router.get("/admin-only")
        def admin_endpoint(current_user = Depends(require_role("admin"))):
            ...

    支持的角色：user, expert, admin
    权限：admin > expert > user
    """
    if not allowed_roles:
        allowed_roles = ("user",)

    min_level = min(ROLE_HIERARCHY.get(r, 0) for r in allowed_roles)

    def dependency(
        authorization: Optional[str] = Header(None),
        db: Session = Depends(get_db),
    ) -> User:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="请先登录",
            )

        token = authorization[7:]
        try:
            payload = decode_token(token)
            if payload.get("type") not in (TOKEN_TYPE_ACCESS, None):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="登录状态无效，请重新登录",
                )
            external_id: str = payload.get("sub")
            if external_id is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="登录状态无效，请重新登录",
                )
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="登录状态已过期，请重新登录",
            )

        user = db.query(User).filter(
            User.external_id == external_id,
            User.is_active == True
        ).first()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户不存在",
            )

        user_level = ROLE_HIERARCHY.get(user.role, 0)
        if user_level < min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足，需要 {'/'.join(allowed_roles)} 角色",
            )

        return user

    return dependency


# ============================================================
# 修改密码 / 重置密码
# ============================================================
def change_password(db: Session, user: User, old_password: str, new_password: str) -> bool:
    """修改密码（需要校验旧密码）

    Args:
        db: 数据库会话
        user: 当前 User 对象
        old_password: 旧密码
        new_password: 新密码

    Returns:
        True 修改成功，False 旧密码错误

    Raises:
        ValueError: 新密码与旧密码相同
    """
    if not verify_password(old_password, user.password_hash):
        return False
    if old_password == new_password:
        raise ValueError("新密码不能与旧密码相同")
    user.password_hash = hash_password(new_password)
    db.commit()
    logger.info(f"用户 {user.external_id} 修改密码成功")
    return True


def reset_password_via_security(
    db: Session,
    login_key: str,
    security_answer: str,
    new_password: str,
) -> bool:
    """通过安全问题重置密码

    Args:
        db: 数据库会话
        login_key: 用户名 或 邮箱
        security_answer: 安全问题答案
        new_password: 新密码

    Returns:
        True 重置成功，False 用户不存在/答案错误/未设置安全问题
    """
    user = db.query(User).filter(
        or_(
            User.username == login_key,
            User.email == login_key,
        )
    ).first()

    if not user:
        return False
    if not user.security_answer_hash:
        return False
    if not verify_password(security_answer, user.security_answer_hash):
        return False

    user.password_hash = hash_password(new_password)
    db.commit()
    logger.info(f"用户 {user.external_id} 通过安全问题重置密码成功")
    return True
