"""
Prometheus 监控指标模块

提供以下指标：
1. HTTP 请求计数（按方法/路径/状态码）
2. HTTP 请求耗时直方图
3. RAG 查询计数（按意图）
4. RAG 查询耗时
5. 缓存命中率
6. 数据库连接池状态
7. Redis 连接状态
8. 应用信息（版本/启动时间）

依赖：prometheus_client（已在 requirements.txt 中）
"""
import time
import logging
from typing import Dict, Any
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    Info,
    generate_latest,
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    REGISTRY,
)

logger = logging.getLogger(__name__)

# ============================================================
# 指标定义
# ============================================================

# HTTP 请求计数器
http_requests_total = Counter(
    "http_requests_total",
    "HTTP 请求总数",
    ["method", "endpoint", "status"],
)

# HTTP 请求耗时直方图（单位：秒）
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# HTTP 在途请求（当前正在处理的请求数）
http_requests_in_progress = Gauge(
    "http_requests_in_progress",
    "当前正在处理的 HTTP 请求数",
)

# RAG 查询计数（按意图）
rag_queries_total = Counter(
    "rag_queries_total",
    "RAG 查询总数",
    ["intent", "status"],  # status: success / failure
)

# RAG 查询耗时
rag_query_duration_seconds = Histogram(
    "rag_query_duration_seconds",
    "RAG 查询耗时（秒）",
    ["intent"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
)

# 缓存命中率
cache_hits_total = Counter(
    "cache_hits_total",
    "缓存命中总数",
    ["cache_type"],  # cache_type: query / embedding / food / tdee
)

cache_misses_total = Counter(
    "cache_misses_total",
    "缓存未命中总数",
    ["cache_type"],
)

# 缓存命中率指标（Gauge，方便直接观察）
cache_hit_rate = Gauge(
    "cache_hit_rate",
    "缓存命中率（0.0-1.0）",
    ["cache_type"],
)

# Redis 连接状态
redis_available = Gauge(
    "redis_available",
    "Redis 连接状态（1=可用，0=不可用）",
)

# Redis 缓存键总数
redis_keys_total = Gauge(
    "redis_keys_total",
    "Redis 缓存键总数",
)

# 应用信息
app_info = Info(
    "healthai_app",
    "HealthAI Platform 应用信息",
)

# 应用启动时间戳
app_start_time_seconds = Gauge(
    "app_start_time_seconds",
    "应用启动时间（Unix 时间戳）",
)

# ============================================================
# 流式响应指标
# ============================================================

# 流式连接活跃数（当前正在进行的流式响应）
stream_active_connections = Gauge(
    "stream_active_connections",
    "当前正在进行的流式响应数",
)

# 流式首字延迟（Time To First Byte，秒）
stream_ttfb_seconds = Histogram(
    "stream_ttfb_seconds",
    "流式响应首字延迟（秒）",
    ["endpoint"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)

# 流式响应总耗时
stream_duration_seconds = Histogram(
    "stream_duration_seconds",
    "流式响应总耗时（秒）",
    ["endpoint"],
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

# 流式 token 输出计数
stream_tokens_total = Counter(
    "stream_tokens_total",
    "流式响应输出的 token 总数",
    ["endpoint"],
)

# 流式错误计数
stream_errors_total = Counter(
    "stream_errors_total",
    "流式响应错误总数",
    ["endpoint", "error_type"],
)

# 流式客户端断开计数
stream_client_disconnects_total = Counter(
    "stream_client_disconnects_total",
    "流式响应客户端主动断开次数",
    ["endpoint"],
)

# 记录应用启动时间
app_start_time_seconds.set(time.time())
app_info.info({
    "version": "1.4.0",
    "env": "production",
    "service": "healthai-platform",
})


# ============================================================
# 工具函数
# ============================================================
def record_http_request(method: str, endpoint: str, status_code: int, duration: float):
    """记录一次 HTTP 请求"""
    http_requests_total.labels(
        method=method,
        endpoint=endpoint,
        status=str(status_code),
    ).inc()
    http_request_duration_seconds.labels(
        method=method,
        endpoint=endpoint,
    ).observe(duration)


def record_rag_query(intent: str, success: bool, duration: float):
    """记录一次 RAG 查询"""
    status = "success" if success else "failure"
    rag_queries_total.labels(intent=intent, status=status).inc()
    rag_query_duration_seconds.labels(intent=intent).observe(duration)


def record_cache_hit(cache_type: str, hit: bool):
    """记录缓存命中/未命中"""
    if hit:
        cache_hits_total.labels(cache_type=cache_type).inc()
    else:
        cache_misses_total.labels(cache_type=cache_type).inc()


def update_cache_hit_rate(cache_type: str, hits: int, total: int):
    """更新缓存命中率"""
    if total > 0:
        cache_hit_rate.labels(cache_type=cache_type).set(hits / total)


def update_redis_status(available: bool, keys_count: int = 0):
    """更新 Redis 状态指标"""
    redis_available.set(1 if available else 0)
    redis_keys_total.set(keys_count)


def record_stream_ttfb(endpoint: str, ttfb: float):
    """记录流式首字延迟"""
    stream_ttfb_seconds.labels(endpoint=endpoint).observe(ttfb)


def record_stream_duration(endpoint: str, duration: float):
    """记录流式响应总耗时"""
    stream_duration_seconds.labels(endpoint=endpoint).observe(duration)


def record_stream_tokens(endpoint: str, count: int = 1):
    """记录流式 token 输出数"""
    stream_tokens_total.labels(endpoint=endpoint).inc(count)


def record_stream_error(endpoint: str, error_type: str):
    """记录流式响应错误"""
    stream_errors_total.labels(endpoint=endpoint, error_type=error_type).inc()


def record_stream_disconnect(endpoint: str):
    """记录客户端主动断开"""
    stream_client_disconnects_total.labels(endpoint=endpoint).inc()


def stream_connection_start():
    """流式连接开始（在途 +1）"""
    stream_active_connections.inc()


def stream_connection_end():
    """流式连接结束（在途 -1）"""
    stream_active_connections.dec()


def get_metrics() -> str:
    """获取 Prometheus 格式的指标数据"""
    return generate_latest(REGISTRY).decode("utf-8")


def get_metrics_content_type() -> str:
    """获取指标响应的 Content-Type"""
    return CONTENT_TYPE_LATEST
