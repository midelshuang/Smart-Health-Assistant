"""
密码与用户名策略校验

规则：
- 密码：8~64 字符，至少包含字母+数字（推荐混合大小写+符号）
- 用户名：3~32 字符，仅允许字母/数字/下划线/中文，且非纯数字
"""
import re
from typing import Optional, Tuple


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fff]{3,32}$")

# 弱密码黑名单（大小写不敏感）
WEAK_PASSWORDS = {
    "12345678", "password", "password1", "123456789", "1234567890",
    "11111111", "00000000", "qwerty12", "abc12345", "admin123",
    "iloveyou", "letmein1", "welcome1", "monkey123",
}


def validate_username(username: str) -> Tuple[bool, Optional[str]]:
    """校验用户名格式

    Returns:
        (is_valid, error_message)
    """
    if not username or not isinstance(username, str):
        return False, "用户名不能为空"
    username = username.strip()
    if len(username) < 3:
        return False, "用户名至少 3 个字符"
    if len(username) > 32:
        return False, "用户名最多 32 个字符"
    if username.isdigit():
        return False, "用户名不能为纯数字"
    if not USERNAME_PATTERN.match(username):
        return False, "用户名仅允许字母、数字、下划线、中文"
    return True, None


def validate_password(password: str) -> Tuple[bool, Optional[str]]:
    """校验密码强度

    Returns:
        (is_valid, error_message)
    """
    if not password or not isinstance(password, str):
        return False, "密码不能为空"
    if len(password) < 8:
        return False, "密码至少 8 个字符"
    if len(password) > 64:
        return False, "密码最多 64 个字符"
    if password.lower() in WEAK_PASSWORDS:
        return False, "密码过于简单，请使用更复杂的组合"
    has_letter = bool(re.search(r"[A-Za-z]", password))
    has_digit = bool(re.search(r"\d", password))
    if not (has_letter and has_digit):
        return False, "密码必须同时包含字母和数字"
    return True, None


def password_strength_score(password: str) -> int:
    """评估密码强度，返回 0~4 分

    评分维度：
    - 长度 >= 8: +1
    - 长度 >= 12: +1
    - 包含大小写字母: +1
    - 包含数字: +1
    - 包含特殊符号: +1（最高 +4）
    """
    if not password:
        return 0
    score = 0
    if len(password) >= 8:
        score += 1
    if len(password) >= 12:
        score += 1
    if re.search(r"[a-z]", password) and re.search(r"[A-Z]", password):
        score += 1
    if re.search(r"\d", password):
        score += 1
    if re.search(r"[!@#$%^&*()\-_=+\[\]{};:'\",.<>/?\\|`~]", password):
        score += 1
    return min(score, 4)
