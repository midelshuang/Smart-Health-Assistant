"""
utils/metrics.py 单元测试

覆盖：
- 指标定义存在性
- record_http_request / record_rag_query / record_cache_hit
- update_cache_hit_rate / update_redis_status
- get_metrics / get_metrics_content_type
"""
import pytest
from src.utils.metrics import (
    http_requests_total,
    http_request_duration_seconds,
    http_requests_in_progress,
    rag_queries_total,
    rag_query_duration_seconds,
    cache_hits_total,
    cache_misses_total,
    cache_hit_rate,
    redis_available,
    redis_keys_total,
    app_info,
    app_start_time_seconds,
    record_http_request,
    record_rag_query,
    record_cache_hit,
    update_cache_hit_rate,
    update_redis_status,
    get_metrics,
    get_metrics_content_type,
)


class TestMetricsDefinitions:
    """指标对象存在性"""

    def test_http_counters_defined(self):
        assert http_requests_total is not None
        assert http_request_duration_seconds is not None
        assert http_requests_in_progress is not None

    def test_rag_counters_defined(self):
        assert rag_queries_total is not None
        assert rag_query_duration_seconds is not None

    def test_cache_counters_defined(self):
        assert cache_hits_total is not None
        assert cache_misses_total is not None
        assert cache_hit_rate is not None

    def test_redis_gauges_defined(self):
        assert redis_available is not None
        assert redis_keys_total is not None

    def test_app_info_defined(self):
        assert app_info is not None
        assert app_start_time_seconds is not None


class TestRecordHttpRequest:
    """record_http_request"""

    def test_increments_counter(self):
        # 取之前值
        before = http_requests_total.labels(
            method="GET", endpoint="/test", status="200"
        )._value.get()
        record_http_request("GET", "/test", 200, 0.05)
        after = http_requests_total.labels(
            method="GET", endpoint="/test", status="200"
        )._value.get()
        assert after == before + 1

    def test_records_duration(self):
        # 不抛异常即视为通过
        record_http_request("POST", "/api/v1/test", 201, 0.123)


class TestRecordRagQuery:
    """record_rag_query"""

    def test_success_status(self):
        before = rag_queries_total.labels(intent="qa", status="success")._value.get()
        record_rag_query(intent="qa", success=True, duration=0.5)
        after = rag_queries_total.labels(intent="qa", status="success")._value.get()
        assert after == before + 1

    def test_failure_status(self):
        before = rag_queries_total.labels(intent="qa", status="failure")._value.get()
        record_rag_query(intent="qa", success=False, duration=0.5)
        after = rag_queries_total.labels(intent="qa", status="failure")._value.get()
        assert after == before + 1


class TestRecordCacheHit:
    """record_cache_hit"""

    def test_hit_increments_hits_counter(self):
        before = cache_hits_total.labels(cache_type="query")._value.get()
        record_cache_hit("query", hit=True)
        after = cache_hits_total.labels(cache_type="query")._value.get()
        assert after == before + 1

    def test_miss_increments_misses_counter(self):
        before = cache_misses_total.labels(cache_type="query")._value.get()
        record_cache_hit("query", hit=False)
        after = cache_misses_total.labels(cache_type="query")._value.get()
        assert after == before + 1


class TestUpdateCacheHitRate:
    """update_cache_hit_rate"""

    def test_sets_correct_ratio(self):
        update_cache_hit_rate("query", hits=3, total=10)
        # 通过 _value 取值
        val = cache_hit_rate.labels(cache_type="query")._value.get()
        assert abs(val - 0.3) < 0.001

    def test_zero_total_no_change(self):
        # total=0 不应抛除零异常
        update_cache_hit_rate("query", hits=0, total=0)


class TestUpdateRedisStatus:
    """update_redis_status"""

    def test_available_true(self):
        update_redis_status(available=True, keys_count=42)
        assert redis_available._value.get() == 1
        assert redis_keys_total._value.get() == 42

    def test_available_false(self):
        update_redis_status(available=False, keys_count=0)
        assert redis_available._value.get() == 0


class TestGetMetrics:
    """get_metrics / get_metrics_content_type"""

    def test_get_metrics_returns_string(self):
        result = get_metrics()
        assert isinstance(result, str)
        # 应包含已定义的指标名
        assert "http_requests_total" in result
        assert "rag_queries_total" in result

    def test_get_metrics_content_type(self):
        ct = get_metrics_content_type()
        assert "text/plain" in ct or "application/openmetrics" in ct
        assert "version" in ct  # 0.0.1 之类
