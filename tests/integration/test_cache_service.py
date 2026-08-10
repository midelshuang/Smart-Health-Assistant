"""
Service/cache_service.py 单元测试

覆盖：
- _hash_text / _normalize_query 工具函数
- 各类缓存读写（query/embedding/food/tdee/plan/safety）
- Redis 不可用时的优雅降级
- invalidate_user_cache
- get_cache_info
"""
import pytest
from unittest.mock import patch, MagicMock

from src.services.cache_service import (
    _hash_text,
    _normalize_query,
    get_cached_query,
    set_cached_query,
    get_cached_embedding,
    set_cached_embedding,
    get_cached_food_search,
    set_cached_food_search,
    get_cached_tdee,
    set_cached_tdee,
    get_cached_plan,
    set_cached_plan,
    get_cached_safety,
    set_cached_safety,
    invalidate_user_cache,
    get_cache_info,
)


# ============================================================
# 工具函数
# ============================================================
class TestHashText:
    def test_returns_hex_string(self):
        h = _hash_text("hello")
        assert isinstance(h, str)
        assert len(h) == 16  # hexdigest[:16]

    def test_same_input_same_hash(self):
        assert _hash_text("hello") == _hash_text("hello")

    def test_different_input_different_hash(self):
        assert _hash_text("hello") != _hash_text("world")

    def test_unicode_input(self):
        # 中文输入不应抛异常
        h = _hash_text("鸡蛋")
        assert isinstance(h, str)


class TestNormalizeQuery:
    def test_strips_whitespace(self):
        assert _normalize_query("  hello  ") == "hello"

    def test_lowercases(self):
        assert _normalize_query("HELLO") == "hello"

    def test_collapses_multiple_spaces(self):
        assert _normalize_query("hello    world") == "hello world"

    def test_translates_chinese_punctuation(self):
        result = _normalize_query("你好？")
        assert "?" in result
        assert "？" not in result

    def test_empty_returns_empty(self):
        assert _normalize_query("") == ""
        assert _normalize_query(None) == ""


# ============================================================
# 缓存读写（Redis 不可用降级）
# ============================================================
class TestQueryCache:
    def test_get_returns_none_when_redis_unavailable(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = False
            mock_get.return_value = client
            assert get_cached_query("any query") is None

    def test_set_returns_false_when_redis_unavailable(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = False
            mock_get.return_value = client
            assert set_cached_query("q", {"answer": "a"}) is False

    def test_set_does_not_cache_error_result(self):
        """失败结果不缓存"""
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = True
            mock_get.return_value = client
            # error result
            result = set_cached_query("q", {"success": False, "error": "fail"})
            assert result is False
            client.set_json.assert_not_called()

    def test_set_does_not_cache_short_answer(self):
        """过短答案不缓存"""
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = True
            mock_get.return_value = client
            # 答案过短
            result = set_cached_query("q", {"answer": "短", "success": True})
            assert result is False

    def test_set_caches_valid_result(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = True
            client.set_json.return_value = True
            mock_get.return_value = client
            # 答案长度需 >= 20 字符才会被缓存
            result = set_cached_query("q", {"answer": "这是一个比较长的答案，用于通过长度检查并验证缓存逻辑。", "success": True})
            assert result is True
            client.set_json.assert_called_once()

    def test_get_returns_cached_value(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = True
            client.get_json.return_value = {"answer": "cached answer", "sources": []}
            mock_get.return_value = client
            result = get_cached_query("q")
            assert result["answer"] == "cached answer"
            assert result.get("_from_cache") is True


class TestEmbeddingCache:
    def test_get_returns_none_when_unavailable(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = False
            mock_get.return_value = client
            assert get_cached_embedding("text") is None

    def test_set_returns_false_when_unavailable(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = False
            mock_get.return_value = client
            assert set_cached_embedding("text", [0.1, 0.2]) is False

    def test_get_returns_vector(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = True
            client.get_json.return_value = [0.1, 0.2, 0.3]
            mock_get.return_value = client
            assert get_cached_embedding("text") == [0.1, 0.2, 0.3]


class TestFoodSearchCache:
    def test_get_returns_none_when_unavailable(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = False
            mock_get.return_value = client
            assert get_cached_food_search("鸡蛋") is None

    def test_set_returns_false_when_unavailable(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = False
            mock_get.return_value = client
            assert set_cached_food_search("鸡蛋", [{"food_name": "鸡蛋"}]) is False

    def test_set_caches_list(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = True
            client.set_json.return_value = True
            mock_get.return_value = client
            result = set_cached_food_search("鸡蛋", [{"food_name": "鸡蛋"}])
            assert result is True


class TestTDEECache:
    def test_get_returns_none_when_unavailable(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = False
            mock_get.return_value = client
            result = get_cached_tdee(
                user_id="u1", weight_kg=70, height_cm=175, age=30,
                gender="male", activity_level="moderate", goal_type="maintenance",
            )
            assert result is None

    def test_set_returns_false_when_unavailable(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = False
            mock_get.return_value = client
            result = set_cached_tdee(
                user_id="u1", weight_kg=70, height_cm=175, age=30,
                gender="male", activity_level="moderate", goal_type="maintenance",
                result={"tdee": 2000},
            )
            assert result is False


class TestPlanCache:
    def test_get_returns_none_when_unavailable(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = False
            mock_get.return_value = client
            assert get_cached_plan("u1", "weight_loss", 1800) is None

    def test_set_returns_false_when_unavailable(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = False
            mock_get.return_value = client
            assert set_cached_plan("u1", "weight_loss", 1800, {"plan": "x"}) is False


class TestSafetyCache:
    def test_get_returns_none_when_unavailable(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = False
            mock_get.return_value = client
            assert get_cached_safety("query") is None

    def test_set_returns_false_when_unavailable(self):
        with patch("src.services.cache_service.get_redis_client") as mock_get:
            client = MagicMock()
            client.is_available.return_value = False
            mock_get.return_value = client
            assert set_cached_safety("query", {"is_safe": True}) is False


# ============================================================
# 缓存管理
# ============================================================
class TestInvalidateUserCache:
    def test_returns_zero_when_unavailable(self):
        with patch("src.services.cache_service.clear_user_cache", return_value=0):
            assert invalidate_user_cache("user_001") == 0


class TestGetCacheInfo:
    def test_returns_dict(self):
        with patch("src.services.cache_service.get_cache_info",
                   return_value={"available": False}):
            info = get_cache_info()
            assert isinstance(info, dict)
