"""
邮箱验证码服务

职责：
1. 生成 6 位数字验证码
2. 通过 SMTP 发送验证码邮件
3. 在 Redis 中存储验证码（带 TTL）并校验
4. 频率限制：60 秒重发间隔 + 每日上限 10 次

降级策略：
- SMTP 未配置时，验证码写入日志，便于开发测试
- Redis 不可用时，使用进程内字典存储
"""
import random
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Tuple, Optional

from src.config.settings import (
    SMTP_ENABLED, SMTP_HOST, SMTP_PORT, SMTP_USE_SSL,
    SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_EMAIL, SMTP_FROM_NAME,
    EMAIL_CODE_LENGTH, EMAIL_CODE_TTL_SECONDS,
    EMAIL_CODE_RESEND_INTERVAL, EMAIL_CODE_MAX_SEND_PER_DAY,
)
from src.utils.logger import setup_logger
from src.utils.redis_client import get_redis_client

logger = setup_logger(name="EmailService")

# Redis Key 前缀
KEY_CODE = "email:code:"          # + email
KEY_RESEND = "email:resend:"      # + email（重发间隔）
KEY_DAILY = "email:daily:"        # + email:YYYYMMDD（每日计数）

# 进程内降级存储（Redis 不可用时）
_memory_store: dict = {}  # email -> (code, expire_ts)
_memory_resend: dict = {}  # email -> resend_expire_ts
_memory_daily: dict = {}  # email:date -> count


# ============================================================
# 内部工具
# ============================================================
def _get_redis():
    try:
        client = get_redis_client()
        if client.is_available():
            return client
    except Exception as e:
        logger.debug(f"Redis 不可用，降级到内存: {e}")
    return None


def _today_str() -> str:
    return datetime.utcnow().strftime("%Y%m%d")


def _generate_code() -> str:
    """生成 6 位数字验证码"""
    return "".join(random.choices("0123456789", k=EMAIL_CODE_LENGTH))


def _is_valid_email(email: str) -> bool:
    """简单邮箱格式校验"""
    import re
    return bool(re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", email or ""))


# ============================================================
# 频率限制
# ============================================================
def _check_rate_limit(email: str, redis) -> Tuple[bool, Optional[str]]:
    """检查是否允许发送验证码"""
    today_key = f"{KEY_DAILY}{email}:{_today_str()}"

    if redis:
        # 重发间隔
        if redis.exists(f"{KEY_RESEND}{email}"):
            ttl = redis.ttl(f"{KEY_RESEND}{email}")
            return False, f"请 {ttl} 秒后再试"
        # 每日上限
        daily = int(redis.get(today_key) or 0)
        if daily >= EMAIL_CODE_MAX_SEND_PER_DAY:
            return False, "今日验证码发送次数已达上限"
        return True, None

    # 内存降级
    now = time.time()
    resend_key = email
    if resend_key in _memory_resend and _memory_resend[resend_key] > now:
        remain = int(_memory_resend[resend_key] - now)
        return False, f"请 {remain} 秒后再试"
    daily = _memory_daily.get(today_key, 0)
    if daily >= EMAIL_CODE_MAX_SEND_PER_DAY:
        return False, "今日验证码发送次数已达上限"
    return True, None


def _record_send(email: str, redis):
    """记录一次发送，更新频率计数"""
    today_key = f"{KEY_DAILY}{email}:{_today_str()}"
    if redis:
        redis.setex(f"{KEY_RESEND}{email}", "1", EMAIL_CODE_RESEND_INTERVAL)
        redis.incr(today_key, ttl=86400)
        return
    _memory_resend[email] = time.time() + EMAIL_CODE_RESEND_INTERVAL
    _memory_daily[today_key] = _memory_daily.get(today_key, 0) + 1


# ============================================================
# 邮件发送
# ============================================================
def _send_email_smtp(to_email: str, code: str) -> bool:
    """通过 SMTP 发送验证码邮件"""
    if not SMTP_ENABLED:
        logger.info(f"[SMTP 未启用] 模拟发送验证码至 {to_email}: code={code}")
        return True  # 视为成功（开发模式）

    if not SMTP_USERNAME or not SMTP_PASSWORD or not SMTP_FROM_EMAIL:
        logger.error("SMTP 配置不完整，无法发送邮件")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = "【HealthAI】邮箱验证码"

    html = f"""
    <div style="font-family: 'Microsoft YaHei', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px;">
        <h2 style="color: #2c7a7b;">HealthAI 平台</h2>
        <p>您好！</p>
        <p>您正在注册 HealthAI 平台账号，验证码为：</p>
        <div style="font-size: 32px; font-weight: bold; color: #2c7a7b;
                    background: #f0f9f9; padding: 16px; text-align: center;
                    border-radius: 8px; letter-spacing: 8px; margin: 16px 0;">
            {code}
        </div>
        <p>验证码 5 分钟内有效，请勿向他人泄露。</p>
        <p style="color: #999; font-size: 12px; margin-top: 24px;">
            若非本人操作，请忽略此邮件。此邮件由系统自动发送，请勿回复。
        </p>
    </div>
    """
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
            server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM_EMAIL, [to_email], msg.as_string())
        server.quit()
        logger.info(f"验证码邮件已发送至 {to_email}")
        return True
    except Exception as e:
        logger.error(f"SMTP 发送失败: {e}")
        return False


# ============================================================
# 对外 API
# ============================================================
def send_verification_code(email: str) -> Tuple[bool, Optional[str]]:
    """发送验证码

    Returns:
        (success, error_message)
    """
    if not _is_valid_email(email):
        return False, "邮箱格式不正确"

    redis = _get_redis()

    # 频率限制
    allowed, msg = _check_rate_limit(email, redis)
    if not allowed:
        return False, msg

    # 生成并存储验证码
    code = _generate_code()

    if redis:
        redis.setex(f"{KEY_CODE}{email}", code, EMAIL_CODE_TTL_SECONDS)
    else:
        _memory_store[email] = (code, time.time() + EMAIL_CODE_TTL_SECONDS)

    # 发送邮件
    if not _send_email_smtp(email, code):
        return False, "邮件发送失败，请稍后重试或联系管理员"

    # 记录发送
    _record_send(email, redis)
    logger.info(f"验证码已发送: email={email}")
    return True, None


def verify_code(email: str, code: str) -> Tuple[bool, Optional[str]]:
    """校验验证码（一次性使用，校验成功后删除）

    Returns:
        (is_valid, error_message)
    """
    redis = _get_redis()

    if redis:
        stored = redis.get(f"{KEY_CODE}{email}")
        if not stored:
            return False, "验证码已过期或未发送"
        if stored != code:
            return False, "验证码错误"
        redis.delete(f"{KEY_CODE}{email}")
        return True, None

    # 内存降级
    record = _memory_store.get(email)
    if not record:
        return False, "验证码已过期或未发送"
    stored_code, expire_ts = record
    if time.time() > expire_ts:
        del _memory_store[email]
        return False, "验证码已过期"
    if stored_code != code:
        return False, "验证码错误"
    del _memory_store[email]
    return True, None
