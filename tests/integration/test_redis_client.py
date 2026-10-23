"""
utils/redis_client.py 单元测试

覆盖：
- build_key 构造
- 单例模式
- is_available 优雅降级
- get_json / set_json / delete / exists / incr 在 Redis 不可用时降级
- clear_user_cache
"""
import pytest
from unittest.mock import patch, MagicMock

from src.utils.redis_client import (
    RedisClient,
    build_key,
    get_redis_client,
    clear_user_cache,
    get_cache_stats,
    KEY_PREFIX_QUERY,
    KEY_PREFIX_TDEE,
    KEY_PREFIX_PLAN,
)


@pytest.fixture(autouse=True)
def _restore_singleton_state():
    """每个测试前后快照/恢复 RedisClient 单例内部状态。

    背景：单例是全局共享的，部分测试会把 _client 换成 MagicMock
    或强制 _available，若不还原会污染后续测试（如 auth 黑名单检查）。
    """
    client = get_redis_client()
    saved = (
        getattr(client, "_client", None),
        getattr(client, "_available", None),
        getattr(client, "_last_check_ts", None),
    )
    yield
    client._client, client._available, client._last_check_ts = saved


class TestBuildKey:
    """build_key 构造"""

    def test_single_part(self):
        assert build_key("prefix", "part1") == "prefix:part1"

    def test_multiple_parts(self):
        assert build_key("prefix", "a", "b", "c") == "prefix:a:b:c"

    def test_none_parts_filtered(self):
        # None 部分应被过滤
        assert build_key("prefix", None, "a", None) == "prefix:a"

    def test_non_string_parts_converted(self):
        assert build_key("prefix", 123, 45.6) == "prefix:123:45.6"

    def test_empty_parts_kept(self):
        # 空字符串不是 None，应保留
        assert build_key("prefix", "", "a") == "prefix::a"


class TestSingleton:
    """单例模式"""

    def test_same_instance(self):
        a = RedisClient()
        b = RedisClient()
        assert a is b

    def test_get_redis_client_returns_singleton(self):
        assert get_redis_client() is RedisClient()


class TestGracefulDegradation:
    """Redis 不可用时的优雅降级"""

    def test_is_available_returns_false_when_no_redis(self):
        """Redis 不可用时 is_available 返回 False"""
        client = get_redis_client()
        # 强制标记为不可用
        client._available = False
        # 设置最近检查时间，避免重试
        import time
        client._last_check_ts = time.time()
        assert client.is_available() is False

    def test_get_json_returns_none_when_unavailable(self):
        client = get_redis_client()
        client._available = False
        import time
        client._last_check_ts = time.time()
        assert client.get_json("any_key") is None

    def test_set_json_returns_false_when_unavailable(self):
        client = get_redis_client()
        client._available = False
        import time
        client._last_check_ts = time.time()
        assert client.set_json("key", {"a": 1}, 60) is False

    def test_delete_returns_zero_when_unavailable(self):
        client = get_redis_client()
        client._available = False
        import time
        client._last_check_ts = time.time()
        assert client.delete("k1", "k2") == 0

    def test_delete_pattern_returns_zero_when_unavailable(self):
        client = get_redis_client()
        client._available = False
        import time
        client._last_check_ts = time.time()
        assert client.delete_pattern("pattern:*") == 0

    def test_exists_returns_false_when_unavailable(self):
        client = get_redis_client()
        client._available = False
        import time
        client._last_check_ts = time.time()
        assert client.exists("k") is False

    def test_incr_returns_zero_when_unavailable(self):
        client = get_redis_client()
        client._available = False
        import time
        client._last_check_ts = time.time()
        assert client.incr("counter") == 0


class TestClearUserCache:
    """clear_user_cache"""

    def test_returns_zero_when_unavailable(self):
        client = get_redis_client()
        client._available = False
        import time
        client._last_check_ts = time.time()
        assert clear_user_cache("user_001") == 0

    def test_patterns_cover_all_user_scopes(self):
        """至少覆盖 query/tdee/plan 三类 key"""
        # 这里仅验证函数不抛异常
        # 真正的删除行为需要 Redis 可用，由 require_redis fixture 跑
        client = get_redis_client()
        client._available = False
        import time
        client._last_check_ts = time.time()
        result = clear_user_cache("user_001")
        assert isinstance(result, int)
        assert result >= 0


class TestGetCacheStats:
    """get_cache_stats"""

    def test_returns_unavailable_dict_when_no_redis(self):
        client = get_redis_client()
        client._available = False
        import time
        client._last_check_ts = time.time()
        stats = get_cache_stats()
        assert stats["available"] is False


class TestRedisOperationsWithMock:
    """使用 Mock Redis 测试实际操作路径"""

    def test_get_json_parses_stored_json(self):
        client = RedisClient()
        # 用 mock 强制 is_available = True
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_redis.get.return_value = '{"key": "value", "num": 42}'
        client._client = mock_redis
        client._available = True

        result = client.get_json("test_key")
        assert result == {"key": "value", "num": 42}
        mock_redis.get.assert_called_once_with("test_key")

    def test_get_json_returns_none_for_missing_key(self):
        client = RedisClient()
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_redis.get.return_value = None
        client._client = mock_redis
        client._available = True

        assert client.get_json("missing") is None

    def test_set_json_serializes_and_sets_ttl(self):
        client = RedisClient()
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        client._client = mock_redis
        client._available = True

        ok = client.set_json("k", {"a": 1}, ttl=60)
        assert ok is True
        # 验证调用了 set 并传了 ex=60
        args, kwargs = mock_redis.set.call_args
        assert args[0] == "k"
        assert kwargs.get("ex") == 60
        # 内容应该是 JSON 字符串
        import json
        assert json.loads(args[1]) == {"a": 1}

    def test_delete_returns_count(self):
        client = RedisClient()
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_redis.delete.return_value = 2
        client._client = mock_redis
        client._available = True

        assert client.delete("k1", "k2") == 2

    def test_exists_returns_bool(self):
        client = RedisClient()
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_redis.exists.return_value = 1
        client._client = mock_redis
        client._available = True

        assert client.exists("k") is True

    def test_incr_first_call_sets_ttl(self):
        client = RedisClient()
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_redis.incr.return_value = 1
        client._client = mock_redis
        client._available = True

        result = client.incr("counter", ttl=60)
        assert result == 1
        mock_redis.expire.assert_called_once_with("counter", 60)

    def test_incr_non_first_call_no_ttl(self):
        client = RedisClient()
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_redis.incr.return_value = 5  # 不是第一次
        client._client = mock_redis
        client._available = True

        client.incr("counter", ttl=60)
        mock_redis.expire.assert_not_called()
