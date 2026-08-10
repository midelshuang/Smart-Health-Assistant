"""
Service/login_limiter.py 单元测试

覆盖：
- 进程内内存降级路径（_MemoryStore 直接测试）
- check_login_allowed / record_login_failure / record_login_success
- get_remaining_attempts
- 阈值触发锁定（账号 / IP）

说明：
  由于本机 Redis 不可用，所有用例都走内存降级路径。
  通过 reset_memory_store 夹具清空全局 _memory_store 状态保证隔离。
"""
import time
from unittest.mock import patch

import pytest

from src.services import login_limiter
from src.services.login_limiter import (
    _memory_store,
    check_login_allowed,
    record_login_failure,
    record_login_success,
    get_remaining_attempts,
    MAX_FAIL_BY_ACCOUNT,
    MAX_FAIL_BY_IP,
    WINDOW_SECONDS,
    LOCK_SECONDS,
)


@pytest.fixture(autouse=True)
def reset_memory_store():
    """每个测试前清空内存存储"""
    _memory_store._fails.clear()
    _memory_store._locks.clear()
    yield
    _memory_store._fails.clear()
    _memory_store._locks.clear()


@pytest.fixture(autouse=True)
def force_memory_mode():
    """禁用 Redis，强制走内存降级"""
    with patch("src.services.login_limiter._get_redis", return_value=None):
        yield


class TestCheckLoginAllowed:
    def test_no_lock_returns_true(self):
        ok, msg = check_login_allowed("alice", "1.2.3.4")
        assert ok is True
        assert msg is None

    def test_account_locked_returns_false(self):
        _memory_store.set_lock(
            login_limiter.KEY_LOCK_ACCOUNT + "alice", LOCK_SECONDS)
        ok, msg = check_login_allowed("alice", "1.2.3.4")
        assert ok is False
        assert "账号" in msg
        assert "锁定" in msg

    def test_ip_locked_returns_false(self):
        _memory_store.set_lock(
            login_limiter.KEY_LOCK_IP + "1.2.3.4", LOCK_SECONDS)
        ok, msg = check_login_allowed("alice", "1.2.3.4")
        assert ok is False
        assert "IP" in msg


class TestRecordLoginFailure:
    def test_single_failure_increments_remaining(self):
        record_login_failure("alice", "1.2.3.4")
        assert get_remaining_attempts("alice") == MAX_FAIL_BY_ACCOUNT - 1

    def test_multiple_failures_until_lock(self):
        for _ in range(MAX_FAIL_BY_ACCOUNT):
            record_login_failure("alice", "1.2.3.4")
        # 第 5 次失败应触发锁定
        ok, msg = check_login_allowed("alice", "1.2.3.4")
        assert ok is False
        assert "账号" in msg

    def test_ip_threshold_separate_from_account(self):
        # 5 次失败（账号阈值）但 IP 还差很远
        for _ in range(MAX_FAIL_BY_ACCOUNT):
            record_login_failure("alice", "1.2.3.4")
        # IP 不应被锁定
        ok, msg = check_login_allowed("bob", "1.2.3.4")
        # 此时 IP 还在累加，但未达 MAX_FAIL_BY_IP
        assert ok is True

    def test_ip_locked_after_threshold(self):
        # 用不同账号但同一 IP，触发 IP 维度锁定
        for i in range(MAX_FAIL_BY_IP):
            record_login_failure(f"user{i}", "9.9.9.9")
        ok, msg = check_login_allowed("new_user", "9.9.9.9")
        assert ok is False
        assert "IP" in msg


class TestRecordLoginSuccess:
    def test_clears_account_failures(self):
        for _ in range(3):
            record_login_failure("alice", "1.2.3.4")
        assert get_remaining_attempts("alice") == MAX_FAIL_BY_ACCOUNT - 3

        record_login_success("alice", "1.2.3.4")
        assert get_remaining_attempts("alice") == MAX_FAIL_BY_ACCOUNT

    def test_does_not_clear_ip_failures(self):
        """登录成功不应清除 IP 失败记录，以防扫描"""
        for _ in range(3):
            record_login_failure("alice", "1.2.3.4")
        record_login_success("alice", "1.2.3.4")
        # IP 计数应保留
        ip_count = _memory_store.count_fails(
            login_limiter.KEY_FAIL_IP + "1.2.3.4")
        assert ip_count == 3


class TestGetRemainingAttempts:
    def test_no_failures_returns_max(self):
        assert get_remaining_attempts("alice") == MAX_FAIL_BY_ACCOUNT

    def test_cannot_go_negative(self):
        """即使失败超过阈值，返回值也不为负"""
        for _ in range(MAX_FAIL_BY_ACCOUNT + 5):
            record_login_failure("alice", "1.2.3.4")
        assert get_remaining_attempts("alice") >= 0


class TestMemoryStoreDirect:
    def test_lock_expires(self):
        """锁定到期后自动失效"""
        _memory_store.set_lock("test_key", 1)
        locked, remain = _memory_store.is_locked("test_key")
        assert locked is True
        assert 0 < remain <= 1

        time.sleep(1.1)
        locked, _ = _memory_store.is_locked("test_key")
        assert locked is False

    def test_fail_window_gc(self):
        """超过窗口的失败记录应被清理"""
        _memory_store.add_fail("test_key")
        # 手动把时间戳设为窗口外
        _memory_store._fails["test_key"] = [time.time() - WINDOW_SECONDS - 1]
        assert _memory_store.count_fails("test_key") == 0
