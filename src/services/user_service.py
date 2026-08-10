"""
用户服务层：负责用户身份管理、档案初始化及事务一致性
"""
from sqlalchemy.orm import Session
from src.models.db_models import User, UserProfile, FitnessGoal
from src.services.auth_service import pwd_context
from src.utils.logger import setup_logger
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta
from src.config.settings import SECRET_KEY
from src.config.settings import ACCESS_TOKEN_EXPIRE_MINUTES as AC


logger = setup_logger(name="UserService")

#注册
def get_or_create_user(
    db: Session,
    external_id: str,
    source: str = "wechat",
    password: str = None,
    nickname: str = None
) -> User:
    """
    用户幂等创建/查询（含关联档案初始化）

    核心逻辑：
    1. 查：根据 external_id + source 查找用户
    2. 补：若不存在，在事务中同时创建 User + UserProfile + FitnessGoal
    3. 返：返回完整的 User 对象（含 profile 关系）

    Args:
        db: 数据库会话
        external_id: 外部唯一标识 (微信OpenID/手机号等)
        source: 来源渠道 (wechat/phone/email)
        password: 明文密码（注册时传入，自动哈希）
        nickname: 用户昵称 (可选)

    Returns:
        User: 用户对象
    """
    # ========== 第一步：查 ==========
    user = db.query(User).filter(
        User.external_id == external_id,
        User.source == source
    ).first()

    if user:
        # 老用户：如果传入了密码且当前无密码，则设置密码
        if password and not user.password_hash:
            user.password_hash = pwd_context.hash(password)
            db.commit()
        return user

    # ========== 第二步：补 (新用户) ==========
    try:
        # print(f"DEBUG: 准备加密的密码类型是 {type(password)}, 内容是: {password}")
        # 2.1 创建用户身份
        if password:
            hashed_password = pwd_context.hash(password)
        else:
            hashed_password = None

        new_user = User(
            external_id=external_id,
            source=source,
            password_hash=hashed_password,
            is_active=True,
            created_at=datetime.now()
        )
        db.add(new_user)
        db.flush()  # ⚠️ 关键：flush 生成 user.id，供后续关联使用

        # 2.2 创建用户健康档案
        new_profile = UserProfile(
            user_id=new_user.external_id,  # ✅ 对应 schema 中 user_id = String(50)
            nickname=nickname
        )
        db.add(new_profile)

        # 2.3 创建默认健康目标
        default_goal = FitnessGoal(
            user_id=new_user.external_id,  # ✅ 对应 schema 中 user_id = String(50)
            goal_type="maintenance",
            status="active"
        )
        db.add(default_goal)

        # 2.4 统一提交
        db.commit()
        db.refresh(new_user)

        logger.info(f"新用户创建成功: {external_id} (source={source})")
        return new_user

    except Exception as e:
        db.rollback()
        logger.error(f"用户创建失败: {external_id}, 错误: {e}")
        raise e

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET_KEY = SECRET_KEY # 建议放在环境变量中
ALGORITHM = "HS256"
#登录
def authenticate_user(db:Session,username: str, password: str):
    """
    登录接口的核心逻辑：验证密码 + 生成 Token
    """
    # 1、从数据库查询用户(根据 external_id 或 username)
    user = db.query(User).filter(User.external_id == username).first()
    # 2、如果找不到用户，返回 None
    if not user:
        return None
        # logger.warning(f"用户不存在: {username}")

    # 3、验证密码
    if not pwd_context.verify(password, user.password_hash):
        return None

    # 4、验证通过，返回用户对象
    return user

def create_access_token(data: dict, expires_delta: timedelta = None):
    """
    生成 JWT Access Token

    Args:
        data: 要编码进 Token 的数据 (通常包含用户ID)
        expires_delta: Token 有效期时长，默认为 ACCESS_TOKEN_EXPIRE_MINUTES

    Returns:
        str: 编码后的 JWT Token 字符串
    """
    to_encode = data.copy()

    # 计算过期时间
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=AC)

    # 将过期时间加入 payload
    to_encode.update({"exp": expire})

    # 生成并返回 Token
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt
