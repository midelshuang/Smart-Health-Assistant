"""
P99 延迟性能验证测试

在 pytest 框架内执行 P99 性能验证，便于 CI/CD 集成。
不依赖 locust 命令，使用 httpx 直接发起并发请求采集延迟。

性能基线（中等规模场景）：
    - food_search 路径 P99 < 3000ms
    - safety_blocked 路径 P99 < 500ms
    - 健康检查 P99 < 100ms
    - 错误率 < 1%

运行方式：
    # 默认针对本地服务
    pytest tests/perf/test_p99_latency.py -v -s

    # 跳过性能测试（默认跳过，需显式启用）
    pytest tests/perf/test_p99_latency.py -v -s --run-perf

    # 自定义目标地址
    HEALTHAI_HOST=http://staging:8000 pytest tests/perf/test_p99_latency.py -v -s --run-perf
"""

import os
import time
import statistics
import pytest
from concurrent.futures import ThreadPoolExecutor, as_completed


# 默认跳过：性能测试需要后端服务运行，不适合在 PR 检查中默认启用
pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(
        os.getenv("RUN_PERF_TESTS", "0") != "1",
        reason="性能测试默认跳过，设置 RUN_PERF_TESTS=1 启用",
    ),
]

# 目标地址（默认指向本地开发服务）
TARGET_HOST = os.getenv("HEALTHAI_HOST", "http://localhost:8000")
# 测试用 JWT token
TEST_JWT_TOKEN = os.getenv("TEST_JWT_TOKEN", "test-token-for-perf")
# 是否跳过认证（开发环境可设置 TEST_BYPASS_AUTH=1）
BYPASS_AUTH = os.getenv("TEST_BYPASS_AUTH", "0") == "1"

# 性能阈值（毫秒）
P99_THRESHOLD_RAG = 3000       # RAG 查询 P99 < 3s
P99_THRESHOLD_SAFETY = 500     # 安全拦截 P99 < 500ms
P99_THRESHOLD_HEALTH = 100    # 健康检查 P99 < 100ms
ERROR_RATE_THRESHOLD = 0.01   # 错误率 < 1%

# 并发参数
CONCURRENT_USERS = 10
REQUESTS_PER_USER = 5


def _get_headers():
    headers = {"Content-Type": "application/json"}
    if not BYPASS_AUTH:
        headers["Authorization"] = f"Bearer {TEST_JWT_TOKEN}"
    return headers


def _percentile(data, p):
    """计算分位数（p 取 0-100）"""
    if not data:
        return float("inf")
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    if f == c:
        return sorted_data[f]
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


def _run_concurrent_requests(make_request_fn, users=CONCURRENT_USERS, per_user=REQUESTS_PER_USER):
    """并发执行请求，返回 (latencies_ms, errors)"""
    latencies = []
    errors = []

    def worker(_user_idx):
        local_lat = []
        local_err = []
        try:
            import httpx
        except ImportError:
            pytest.skip("httpx 未安装，跳过性能测试")

        client = httpx.Client(timeout=30.0)
        try:
            for _ in range(per_user):
                t0 = time.perf_counter()
                try:
                    response = make_request_fn(client)
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    local_lat.append(elapsed_ms)
                    if response.status_code >= 400:
                        local_err.append(f"status={response.status_code}")
                except Exception as e:
                    local_err.append(str(e)[:100])
        finally:
            client.close()
        return local_lat, local_err

    with ThreadPoolExecutor(max_workers=users) as pool:
        futures = [pool.submit(worker, i) for i in range(users)]
        for fut in as_completed(futures):
            lat, err = fut.result()
            latencies.extend(lat)
            errors.extend(err)

    return latencies, errors


# ============================================================
# 测试 1：健康检查 P99
# ============================================================

def test_health_check_p99():
    """健康检查端点 P99 < 100ms"""
    def make_request(client):
        return client.get(f"{TARGET_HOST}/api/v1/health")

    latencies, errors = _run_concurrent_requests(make_request, users=20, per_user=10)

    assert len(latencies) > 0, "无成功响应"
    p99 = _percentile(latencies, 99)
    p50 = _percentile(latencies, 50)
    error_rate = len(errors) / max(len(latencies) + len(errors), 1)

    print(f"\n健康检查: P50={p50:.0f}ms, P99={p99:.0f}ms, errors={len(errors)}, rate={error_rate*100:.2f}%")

    assert p99 < P99_THRESHOLD_HEALTH, f"P99 {p99:.0f}ms > {P99_THRESHOLD_HEALTH}ms"
    assert error_rate < ERROR_RATE_THRESHOLD, f"错误率 {error_rate*100:.2f}% > 1%"


# ============================================================
# 测试 2：安全拦截 P99
# ============================================================

def test_safety_blocked_p99():
    """安全护栏拦截路径 P99 < 500ms（应快速返回）"""
    safety_queries = [
        "帮我开点降糖药",
        "我是不是得了糖尿病",
        "帮我进行辟谷21天",
        "孕妇能吃什么药",
    ]

    def make_request(client):
        import json as _json
        body = {"query": safety_queries[0], "stream": False}
        return client.post(
            f"{TARGET_HOST}/api/v1/rag/query",
            content=_json.dumps(body),
            headers=_get_headers(),
        )

    latencies, errors = _run_concurrent_requests(make_request, users=10, per_user=5)

    assert len(latencies) > 0, "无成功响应"
    p99 = _percentile(latencies, 99)
    p50 = _percentile(latencies, 50)
    error_rate = len(errors) / max(len(latencies) + len(errors), 1)

    print(f"\n安全拦截: P50={p50:.0f}ms, P99={p99:.0f}ms, errors={len(errors)}, rate={error_rate*100:.2f}%")

    assert p99 < P99_THRESHOLD_SAFETY, f"P99 {p99:.0f}ms > {P99_THRESHOLD_SAFETY}ms"


# ============================================================
# 测试 3：RAG 查询 P99
# ============================================================

def test_rag_query_p99():
    """RAG 查询 P99 < 3000ms（含检索+生成）"""
    food_queries = [
        "一碗米饭有多少热量",
        "苹果的营养成分",
        "鸡胸肉蛋白质含量",
        "100克牛肉的热量",
        "豆腐有多少蛋白质",
    ]

    def make_request(client):
        import json as _json
        body = {"query": food_queries[0], "stream": False}
        return client.post(
            f"{TARGET_HOST}/api/v1/rag/query",
            content=_json.dumps(body),
            headers=_get_headers(),
        )

    latencies, errors = _run_concurrent_requests(make_request, users=CONCURRENT_USERS, per_user=REQUESTS_PER_USER)

    assert len(latencies) > 0, "无成功响应"
    p99 = _percentile(latencies, 99)
    p95 = _percentile(latencies, 95)
    p50 = _percentile(latencies, 50)
    error_rate = len(errors) / max(len(latencies) + len(errors), 1)

    print(f"\nRAG 查询: P50={p50:.0f}ms, P95={p95:.0f}ms, P99={p99:.0f}ms, errors={len(errors)}, rate={error_rate*100:.2f}%")

    assert p99 < P99_THRESHOLD_RAG, f"P99 {p99:.0f}ms > {P99_THRESHOLD_RAG}ms"
    assert error_rate < ERROR_RATE_THRESHOLD, f"错误率 {error_rate*100:.2f}% > 1%"
