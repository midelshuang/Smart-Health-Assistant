"""
Service/email_service.py 单元测试

覆盖：
- 邮箱格式校验 _is_valid_email
- 验证码生成长度 _generate_code
- 频率限制 _check_rate_limit / _record_send（内存降级）
- 验证码发送 send_verification_code（SMTP 已禁用走日志模式）
- 验证码校验 verify_code（成功/失败/过期/一次性）

说明：
  所有测试强制走内存降级路径（Redis 不可用），
  并禁用 SMTP（SMTP_ENABLED=false），验证码仅写入日志。
"""
import time
from unittest.mock import patch

import pytest

from src.services import email_service
from src.services.email_service import (
    send_verification_code,
    verify_code,
    _is_valid_email,
    _generate_code,
    _check_rate_limit,
    _record_send,
    EMAIL_CODE_LENGTH,
    EMAIL_CODE_TTL_SECONDS,
    EMAIL_CODE_RESEND_INTERVAL,
    EMAIL_CODE_MAX_SEND_PER_DAY,
)
from src.config import settings as setting


@pytest.fixture(autouse=True)
def reset_memory_store():
    email_service._memory_store.clear()
    email_service._memory_resend.clear()
    email_service._memory_daily.clear()
    yield
    email_service._memory_store.clear()
    email_service._memory_resend.clear()
    email_service._memory_daily.clear()


@pytest.fixture(autouse=True)
def force_memory_mode():
    """禁用 Redis，强制走内存降级"""
    with patch("src.services.email_service._get_redis", return_value=None):
        yield


@pytest.fixture(autouse=True)
def force_smtp_disabled():
    """禁用 SMTP，验证码仅写日志"""
    old = setting.SMTP_ENABLED
    setting.SMTP_ENABLED = False
    yield
    setting.SMTP_ENABLED = old


class TestIsValidEmail:
    def test_valid_emails(self):
        for e in ["a@b.com", "user.name+tag@example.co", "x_y@sub.domain.org"]:
            assert _is_valid_email(e) is True, f"应通过: {e}"

    def test_invalid_emails(self):
        for e in ["", None, "plainaddress", "@no-local.com", "no-tld@domain",
                  "spaces in@local.com", "missing@dot"]:
            assert _is_valid_email(e) is False, f"应拒绝: {e}"


class TestGenerateCode:
    def test_length(self):
        code = _generate_code()
        assert len(code) == EMAIL_CODE_LENGTH

    def test_all_digits(self):
        code = _generate_code()
        assert code.isdigit()

    def test_different_each_call(self):
        codes = {_generate_code() for _ in range(20)}
        assert len(codes) > 1  # 几乎不可能完全相同


class TestCheckRateLimit:
    def test_first_send_allowed(self):
        ok, msg = _check_rate_limit("a@b.com", None)
        assert ok is True
        assert msg is None

    def test_resend_interval_blocked(self):
        # 模拟刚发送过
        email_service._memory_resend["a@b.com"] = time.time() + 30
        ok, msg = _check_rate_limit("a@b.com", None)
        assert ok is False
        assert "秒" in msg

    def test_daily_limit_blocked(self):
        today_key = list(email_service._memory_daily.keys())
        # 手动塞入今日计数到上限
        from src.services.email_service import _today_str, KEY_DAILY
        key = f"{KEY_DAILY}a@b.com:{_today_str()}"
        email_service._memory_daily[key] = EMAIL_CODE_MAX_SEND_PER_DAY
        ok, msg = _check_rate_limit("a@b.com", None)
        assert ok is False
        assert "上限" in msg


class TestSendVerificationCode:
    def test_invalid_email_returns_error(self):
        ok, msg = send_verification_code("not-an-email")
        assert ok is False
        assert "邮箱格式" in msg

    def test_valid_email_returns_success(self):
        ok, msg = send_verification_code("user@example.com")
        assert ok is True
        assert msg is None

    def test_code_stored_in_memory(self):
        send_verification_code("user@example.com")
        assert "user@example.com" in email_service._memory_store

    def test_resend_interval_enforced(self):
        ok1, _ = send_verification_code("user@example.com")
        assert ok1 is True
        ok2, msg2 = send_verification_code("user@example.com")
        assert ok2 is False
        assert "秒" in msg2


class TestVerifyCode:
    def test_correct_code_returns_true(self):
        # 先发送
        send_verification_code("user@example.com")
        # 从内存读出验证码
        stored_code = email_service._memory_store["user@example.com"][0]
        ok, msg = verify_code("user@example.com", stored_code)
        assert ok is True
        assert msg is None

    def test_wrong_code_returns_false(self):
        send_verification_code("user@example.com")
        ok, msg = verify_code("user@example.com", "000000")
        assert ok is False
        assert "验证码错误" in msg

    def test_nonexistent_code_returns_false(self):
        ok, msg = verify_code("never@sent.com", "123456")
        assert ok is False
        assert "过期" in msg or "未发送" in msg

    def test_code_one_time_use(self):
        """验证码校验成功后应删除"""
        send_verification_code("user@example.com")
        stored_code = email_service._memory_store["user@example.com"][0]
        ok1, _ = verify_code("user@example.com", stored_code)
        assert ok1 is True
        # 二次校验应失败
        ok2, msg2 = verify_code("user@example.com", stored_code)
        assert ok2 is False

    def test_expired_code_returns_false(self):
        """过期验证码应被清理并返回失败"""
        send_verification_code("user@example.com")
        # 手动把过期时间设为过去
        code, _ = email_service._memory_store["user@example.com"]
        email_service._memory_store["user@example.com"] = (code, time.time() - 1)
        ok, msg = verify_code("user@example.com", code)
        assert ok is False
        assert "过期" in msg
        # 应被清理
        assert "user@example.com" not in email_service._memory_store
