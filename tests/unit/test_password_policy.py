"""
utils/password_policy.py 单元测试

覆盖：
- 用户名校验（长度、字符集、纯数字、空值）
- 密码强度校验（长度、复杂度、弱密码黑名单）
- password_strength_score 评分逻辑
"""
import pytest
from src.utils.password_policy import (
    validate_username,
    validate_password,
    password_strength_score,
)


class TestValidateUsername:
    def test_valid_simple(self):
        ok, msg = validate_username("alice")
        assert ok is True
        assert msg is None

    def test_valid_with_underscore_and_digits(self):
        ok, _ = validate_username("user_001")
        assert ok is True

    def test_valid_chinese(self):
        ok, _ = validate_username("张三丰")
        assert ok is True

    def test_too_short(self):
        ok, msg = validate_username("ab")
        assert ok is False
        assert "至少 3" in msg

    def test_too_long(self):
        ok, msg = validate_username("a" * 33)
        assert ok is False
        assert "最多 32" in msg

    def test_pure_digits_rejected(self):
        ok, msg = validate_username("123456")
        assert ok is False
        assert "纯数字" in msg

    def test_invalid_chars_rejected(self):
        ok, msg = validate_username("user@name")
        assert ok is False

    def test_empty(self):
        ok, msg = validate_username("")
        assert ok is False
        assert "不能为空" in msg

    def test_none(self):
        ok, msg = validate_username(None)
        assert ok is False

    def test_strips_whitespace(self):
        ok, _ = validate_username("  alice  ")
        assert ok is True


class TestValidatePassword:
    def test_valid_strong(self):
        ok, msg = validate_password("Strong1234")
        assert ok is True
        assert msg is None

    def test_too_short(self):
        ok, msg = validate_password("Aa1")
        assert ok is False
        assert "至少 8" in msg

    def test_too_long(self):
        ok, msg = validate_password("A" * 65 + "1")
        assert ok is False
        assert "最多 64" in msg

    def test_only_letters(self):
        ok, msg = validate_password("abcdefgh")
        assert ok is False
        assert "字母和数字" in msg

    def test_only_digits(self):
        # 使用不在黑名单的纯数字密码
        ok, msg = validate_password("987654321")
        assert ok is False
        assert "字母和数字" in msg

    def test_weak_password_blacklist(self):
        ok, msg = validate_password("password")
        assert ok is False
        assert "简单" in msg

    def test_weak_password_blacklist_case_insensitive(self):
        ok, _ = validate_password("PASSWORD")
        # PASSWORD 不在黑名单（password 是小写），但只含字母，应失败
        assert ok is False

    def test_blacklisted_password_with_digit(self):
        ok, msg = validate_password("password1")
        assert ok is False
        assert "简单" in msg

    def test_empty(self):
        ok, msg = validate_password("")
        assert ok is False
        assert "不能为空" in msg

    def test_none(self):
        ok, _ = validate_password(None)
        assert ok is False


class TestPasswordStrengthScore:
    def test_empty_returns_zero(self):
        assert password_strength_score("") == 0

    def test_short_only_letters(self):
        # 长度 < 8, 只有小写字母 → 0
        assert password_strength_score("abc") == 0

    def test_eight_chars_lowercase_only(self):
        # 长度>=8 (+1), 无大小写混合, 无数字, 无符号 → 1
        assert password_strength_score("abcdefgh") == 1

    def test_eight_chars_with_digit(self):
        # 长度>=8 (+1), 有数字 (+1) → 2
        assert password_strength_score("abcdef12") == 2

    def test_twelve_chars(self):
        # 长度>=8 (+1), 长度>=12 (+1), 有数字 (+1) → 3
        assert password_strength_score("abcdef123456") == 3

    def test_full_strength(self):
        # 长度>=12, 大小写, 数字, 符号 → 4 (capped)
        assert password_strength_score("Abcdef123!@#x") == 4

    def test_short_but_complex(self):
        # 长度<8 但有大小写+数字+符号 → 3
        assert password_strength_score("Ab1!") == 3

    def test_score_capped_at_4(self):
        # 即使长度很长，最多 4
        long_pw = "Aa1!" + "x" * 50
        assert password_strength_score(long_pw) == 4
