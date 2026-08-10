"""
登录失败限流（防暴力破解）

双轨实现：
- Redis 可用：分布式计数（生产/多实例）
- Redis 不可用：进程内字典降级（单实例开发）

策略：
- 同一账号 5 次失败 / 5 分钟窗口 → 锁定 15 分钟
- 同一 IP 20 次失败 / 5 分钟窗口 → 锁定 15 分钟
"""
import time
from typing import Optional, Tuple
from src.utils.logger import setup_logger
from src.utils.redis_client import get_redis_client

logger = setup_logger(name="LoginLimiter")

# 限流策略
MAX_FAIL_BY_ACCOUNT = 5       # 同一账号最大失败次数
MAX_FAIL_BY_IP = 20            # 同一 IP 最大失败次数
WINDOW_SECONDS = 5 * 60        # 失败计数窗口（5 分钟）
LOCK_SECONDS = 15 * 60         # 锁定时长（15 分钟）

# Redis Key 前缀
KEY_FAIL_ACCOUNT = "login:fail:account:"   # + username
KEY_FAIL_IP = "login:fail:ip:"             # + ip
KEY_LOCK_ACCOUNT = "login:lock:account:"   # + username
KEY_LOCK_IP = "login:lock:ip:"             # + ip


# ============================================================
# 进程内降级存储（Redis 不可用时使用）
# ============================================================
class _MemoryStore:
    """简单滑动窗口 + 锁定标记"""

    def __init__(self):
        self._fails: dict = {}   # key -> list[timestamp]
        self._locks: dict = {}  # key -> expire_timestamp

    def _gc_fails(self, key: str, now: float):
        if key in self._fails:
            self._fails[key] = [t for t in self._fails[key] if now - t < WINDOW_SECONDS]
            if not self._fails[key]:
                del self._fails[key]

    def add_fail(self, key: str):
        now = time.time()
        self._gc_fails(key, now)
        self._fails.setdefault(key, []).append(now)

    def count_fails(self, key: str) -> int:
        now = time.time()
        self._gc_fails(key, now)
        return len(self._fails.get(key, []))

    def set_lock(self, key: str, ttl: int):
        self._locks[key] = time.time() + ttl

    def is_locked(self, key: str) -> Tuple[bool, int]:
        if key not in self._locks:
            return False, 0
        remain = int(self._locks[key] - time.time())
        if remain <= 0:
            del self._locks[key]
            return False, 0
        return True, remain

    def clear(self, key: str):
        self._fails.pop(key, None)
        self._locks.pop(key, None)


_memory_store = _MemoryStore()


def _get_redis():
    try:
        client = get_redis_client()
        if client.is_available():
            return client
    except Exception as e:
        logger.debug(f"Redis 不可用，降级到内存限流: {e}")
    return None


# ============================================================
# 对外 API
# ============================================================
def check_login_allowed(username: str, ip: str) -> Tuple[bool, Optional[str]]:
    """检查当前是否允许登录（账号或 IP 是否被锁定）

    Returns:
        (allowed, error_message)
    """
    redis = _get_redis()
    if redis:
        for key, who in [(KEY_LOCK_ACCOUNT + username, "账号"),
                         (KEY_LOCK_IP + ip, "IP")]:
            ttl = redis.ttl(key)
            if ttl is not None and ttl > 0:
                mins = ttl // 60
                secs = ttl % 60
                return False, f"{who}已被锁定，请 {mins} 分 {secs} 秒后重试"
        return True, None

    # 内存降级
    for key, who in [(KEY_LOCK_ACCOUNT + username, "账号"),
                     (KEY_LOCK_IP + ip, "IP")]:
        locked, remain = _memory_store.is_locked(key)
        if locked:
            mins = remain // 60
            secs = remain % 60
            return False, f"{who}已被锁定，请 {mins} 分 {secs} 秒后重试"
    return True, None


def record_login_failure(username: str, ip: str):
    """记录一次登录失败，若达阈值则自动锁定"""
    redis = _get_redis()

    # 账号维度
    if redis:
        acc_count = redis.incr(KEY_FAIL_ACCOUNT + username, ttl=WINDOW_SECONDS)
        ip_count = redis.incr(KEY_FAIL_IP + ip, ttl=WINDOW_SECONDS)

        if acc_count >= MAX_FAIL_BY_ACCOUNT:
            redis.setex(KEY_LOCK_ACCOUNT + username, "1", LOCK_SECONDS)
            logger.warning(f"账号 {username} 因登录失败 {acc_count} 次被锁定 {LOCK_SECONDS}s")
        if ip_count >= MAX_FAIL_BY_IP:
            redis.setex(KEY_LOCK_IP + ip, "1", LOCK_SECONDS)
            logger.warning(f"IP {ip} 因登录失败 {ip_count} 次被锁定 {LOCK_SECONDS}s")
        return

    _record_failure_memory(username, ip)


def _record_failure_memory(username: str, ip: str):
    _memory_store.add_fail(KEY_FAIL_ACCOUNT + username)
    _memory_store.add_fail(KEY_FAIL_IP + ip)

    acc_count = _memory_store.count_fails(KEY_FAIL_ACCOUNT + username)
    ip_count = _memory_store.count_fails(KEY_FAIL_IP + ip)

    if acc_count >= MAX_FAIL_BY_ACCOUNT:
        _memory_store.set_lock(KEY_LOCK_ACCOUNT + username, LOCK_SECONDS)
        logger.warning(f"账号 {username} 因登录失败 {acc_count} 次被锁定")
    if ip_count >= MAX_FAIL_BY_IP:
        _memory_store.set_lock(KEY_LOCK_IP + ip, LOCK_SECONDS)
        logger.warning(f"IP {ip} 因登录失败 {ip_count} 次被锁定")


def record_login_success(username: str, ip: str):
    """登录成功后清除该账号的失败记录（IP 记录保留以防扫描）"""
    redis = _get_redis()
    if redis:
        redis.delete(KEY_FAIL_ACCOUNT + username)
        return
    _memory_store.clear(KEY_FAIL_ACCOUNT + username)


def get_remaining_attempts(username: str) -> int:
    """获取当前账号还能尝试几次登录（未锁定时）"""
    redis = _get_redis()
    if redis:
        raw = redis.get(KEY_FAIL_ACCOUNT + username)
        count = int(raw) if raw else 0
        return max(0, MAX_FAIL_BY_ACCOUNT - count)
    count = _memory_store.count_fails(KEY_FAIL_ACCOUNT + username)
    return max(0, MAX_FAIL_BY_ACCOUNT - count)
