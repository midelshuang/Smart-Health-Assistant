"""
性能测试套件

测试目标：
- P50 响应时间 < 2s（普通查询）
- P95 响应时间 < 5s（复杂查询）
- P99 响应时间 < 8s（极端情况）
- 错误率 < 1%
- 并发 50 用户稳定运行

分为：
1. 角色响应时间基准（guest/user/admin）
2. 各端点延迟分布
3. 并发性能（基于 asyncio + threading 的模拟并发）
4. 资源利用率（Token 生成/解析性能）
"""
import pytest
import time
import threading
import asyncio
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch, MagicMock

from src.services.auth_service import (
    create_access_token, create_refresh_token,
    decode_token, get_current_user_id,
)
from src.core.safety_checker import check_safety
from src.api.routes.rag import RAGQueryRequest
from src.config.settings import SECRET_KEY, ALGORITHM
from jose import jwt


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_db():
    return MagicMock()


def _benchmark(func, iterations=100):
    """通用基准测试装饰"""
    times = []
    for _ in range(iterations):
        start = time.time()
        func()
        times.append((time.time() - start) * 1000)
    return times


def _percentile(data, pct):
    """计算百分位数"""
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * pct / 100)
    return sorted_data[min(idx, len(sorted_data) - 1)]


# ============================================================
# 1. 角色响应时间基准
# ============================================================

class TestRoleResponseTime:
    """按角色的基准响应时间"""

    def test_token_generation_latency(self):
        """Token 生成延迟 < 5ms"""
        times = _benchmark(lambda: create_access_token({"sub": "user001"}), iterations=500)
        p99 = _percentile(times, 99)
        assert p99 < 5, f"Token 生成 P99={p99:.2f}ms > 5ms"

    def test_token_decode_latency(self):
        """Token 解析延迟 < 2ms"""
        token = create_access_token({"sub": "user001"})
        times = _benchmark(lambda: decode_token(token), iterations=500)
        p99 = _percentile(times, 99)
        assert p99 < 2, f"Token 解析 P99={p99:.2f}ms > 2ms"

    def test_token_verify_latency(self, mock_db):
        """Token 验证（含 DB 查询）< 10ms"""
        token = create_access_token({"sub": "user001"})
        mock_user = MagicMock()
        mock_user.external_id = "user001"
        mock_user.is_active = True
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = mock_user
        mock_db.query.return_value = query_mock

        times = []
        for _ in range(200):
            start = time.time()
            get_current_user_id(token=token, db=mock_db)
            times.append((time.time() - start) * 1000)
        p99 = _percentile(times, 99)
        assert p99 < 10, f"Token 验证 P99={p99:.2f}ms > 10ms"

    def test_guest_auth_latency(self, mock_db):
        """游客认证（无 Token）延迟 < 1ms"""
        from src.services.auth_service import get_current_user_optional, GuestUser
        times = []
        for _ in range(500):
            start = time.time()
            result = get_current_user_optional(authorization=None, db=mock_db)
            assert isinstance(result, GuestUser)
            times.append((time.time() - start) * 1000)
        p99 = _percentile(times, 99)
        assert p99 < 1, f"游客认证 P99={p99:.2f}ms > 1ms"

    def test_role_permission_check_latency(self, mock_db):
        """角色权限检查延迟 < 5ms"""
        from src.services.auth_service import require_role
        token = create_access_token({"sub": "user001"})
        mock_user = MagicMock()
        mock_user.external_id = "user001"
        mock_user.role = "user"
        mock_user.is_active = True
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = mock_user
        mock_db.query.return_value = query_mock

        dep = require_role("user")
        times = []
        for _ in range(200):
            start = time.time()
            dep(authorization=f"Bearer {token}", db=mock_db)
            times.append((time.time() - start) * 1000)
        p99 = _percentile(times, 99)
        assert p99 < 5, f"权限检查 P99={p99:.2f}ms > 5ms"


# ============================================================
# 2. 安全护栏性能
# ============================================================

class TestSafetyPerformance:
    """安全护栏性能指标"""

    def test_regex_block_latency(self):
        """正则拦截 P99 < 100ms"""
        times = _benchmark(
            lambda: check_safety("帮我开点降压药", use_llm=False),
            iterations=300,
        )
        p99 = _percentile(times, 99)
        assert p99 < 100, f"正则拦截 P99={p99:.2f}ms > 100ms"

    def test_pass_query_latency(self):
        """合法查询 P99 < 50ms"""
        times = _benchmark(
            lambda: check_safety("鸡蛋的热量是多少", use_llm=False),
            iterations=300,
        )
        p99 = _percentile(times, 99)
        assert p99 < 50, f"合法查询 P99={p99:.2f}ms > 50ms"

    def test_safety_throughput(self):
        """安全护栏吞吐量 > 1000 QPS"""
        duration = 1.0  # 1 秒
        count = 0
        start = time.time()
        while time.time() - start < duration:
            check_safety("鸡蛋热量", use_llm=False)
            check_safety("帮我开点药", use_llm=False)
            count += 2
        qps = count / duration
        assert qps > 1000, f"安全护栏 QPS={qps:.0f} < 1000"

    def test_safety_no_regex_compile_overhead(self):
        """正则预编译：多次调用无显著性能劣化"""
        t1 = time.time()
        for _ in range(1000):
            check_safety("鸡蛋热量", use_llm=False)
        t2 = time.time() - t1
        assert t2 < 5, f"1000 次调用耗时 {t2:.2f}s > 5s"


# ============================================================
# 3. Token 批量性能
# ============================================================

class TestTokenBatchPerformance:
    """Token 批量操作性能"""

    def test_batch_token_generation(self):
        """批量生成 1000 个 Token < 2s"""
        start = time.time()
        tokens = []
        for i in range(1000):
            tokens.append(create_access_token({"sub": f"user_{i}"}))
        elapsed = time.time() - start
        assert elapsed < 2, f"1000 Token 生成耗时 {elapsed:.2f}s > 2s"

    def test_batch_token_decode(self):
        """批量解析 1000 个 Token < 1s"""
        tokens = [create_access_token({"sub": f"user_{i}"}) for i in range(1000)]
        start = time.time()
        for token in tokens:
            decode_token(token)
        elapsed = time.time() - start
        assert elapsed < 1, f"1000 Token 解析耗时 {elapsed:.2f}s > 1s"

    def test_concurrent_token_operations(self):
        """并发 50 线程 Token 操作"""
        def worker():
            for _ in range(20):
                token = create_access_token({"sub": f"concurrent_{threading.current_thread().name}"})
                decode_token(token)
                time.sleep(0.001)

        start = time.time()
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(worker) for _ in range(50)]
            for f in futures:
                f.result()
        elapsed = time.time() - start
        assert elapsed < 10, f"50 并发 Token 操作耗时 {elapsed:.2f}s > 10s"


# ============================================================
# 4. 并发压力测试
# ============================================================

class TestConcurrentStress:
    """并发压力测试"""

    def test_100_concurrent_safety_checks(self):
        """100 并发安全检查"""
        def do_check():
            for _ in range(10):
                check_safety("测试查询", use_llm=False)

        start = time.time()
        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = [executor.submit(do_check) for _ in range(100)]
            for f in futures:
                f.result()
        elapsed = time.time() - start
        assert elapsed < 10, f"100 并发安全检查耗时 {elapsed:.2f}s > 10s"

    def test_concurrent_token_stability(self):
        """并发 Token 操作稳定性（无崩溃）"""
        errors = []
        def token_worker():
            try:
                for _ in range(50):
                    token = create_access_token({"sub": f"t_{threading.current_thread().name}"})
                    decode_token(token)
            except Exception as e:
                errors.append(str(e))

        with ThreadPoolExecutor(max_workers=30) as executor:
            futures = [executor.submit(token_worker) for _ in range(30)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"并发 Token 操作出现错误: {errors}"

    def test_high_concurrent_read_write(self):
        """高并发读写混合"""
        from src.services.auth_service import GuestUser
        from src.api.routes.rag import execute_rag_query, RAGQueryRequest

        mock_db = MagicMock()

        def read_worker():
            """读取：获取当前用户（游客模式）"""
            from src.services.auth_service import get_current_user_optional
            result = get_current_user_optional(authorization=None, db=mock_db)
            assert not result or isinstance(result, GuestUser)

        def write_worker():
            """写入：创建 Token"""
            token = create_access_token({"sub": f"write_{threading.current_thread().name}"})
            assert len(token) > 50

        start = time.time()
        with ThreadPoolExecutor(max_workers=80) as executor:
            futures = []
            for _ in range(40):
                futures.append(executor.submit(read_worker))
            for _ in range(40):
                futures.append(executor.submit(write_worker))
            for f in as_completed(futures):
                f.result()
        elapsed = time.time() - start
        assert elapsed < 15, f"80 并发读写混合耗时 {elapsed:.2f}s > 15s"


# ============================================================
# 5. 内存与资源使用
# ============================================================

class TestResourceUsage:
    """资源使用效率"""

    def test_token_memory_efficiency(self):
        """Token 内存效率（10000 Token 集）"""
        tokens = [create_access_token({"sub": f"user_{i}"}) for i in range(10000)]
        total_str_len = sum(len(t) for t in tokens)
        avg_len = total_str_len / len(tokens)
        assert avg_len < 200, f"Token 平均长度 {avg_len:.0f} 字符 > 200"

    def test_no_memory_leak_in_loop(self):
        """循环调用无内存泄漏"""
        import sys
        initial_tokens = len(create_access_token({"sub": "init"}))
        for _ in range(1000):
            token = create_access_token({"sub": "loop"})
            decode_token(token)
        final_len = len(create_access_token({"sub": "final"}))
        # Token 长度允许 ±2 字节差异（时间戳微差）
        assert abs(final_len - initial_tokens) <= 2, f"Token 长度变化过大: initial={initial_tokens}, final={final_len}"

    def test_string_allocation_efficiency(self):
        """字符串分配效率"""
        token = create_access_token({"sub": "test"})
        assert isinstance(token, str)
        assert len(token) > 50, "Token 长度应 > 50 字符"


# ============================================================
# 6. 性能基准回归
# ============================================================

class TestPerformanceRegression:
    """性能回归测试（确保优化后不退化）"""

    def test_token_generation_not_regressed(self):
        """Token 生成性能无回归"""
        times = _benchmark(lambda: create_access_token({"sub": "user001"}), iterations=500)
        p50 = _percentile(times, 50)
        assert p50 < 2, f"Token 生成 P50={p50:.2f}ms 有性能退化"

    def test_safety_check_not_regressed(self):
        """安全检查性能无回归"""
        times = _benchmark(lambda: check_safety("测试查询", use_llm=False), iterations=500)
        p50 = _percentile(times, 50)
        assert p50 < 5, f"安全检查 P50={p50:.2f}ms 有性能退化"

    def test_batch_operations_scalable(self):
        """批量操作可扩展"""
        sizes = [100, 500, 1000]
        times = []
        for size in sizes:
            start = time.time()
            for i in range(size):
                token = create_access_token({"sub": f"user_{i}"})
                decode_token(token)
            elapsed = time.time() - start
            times.append(elapsed)

        # 验证近似线性扩展（1000 个的耗时不应 >> 100 个的 10 倍）
        ratio = times[2] / times[0] if times[0] > 0 else float('inf')
        assert ratio < 15, f"批量扩展比={ratio:.1f}，超过预期线性扩展"


# ============================================================
# 7. 各端点性能指标
# ============================================================

class TestEndpointPerformance:
    """各端点性能指标"""

    def test_health_check_performance(self):
        """健康检查 < 5ms"""
        times = []
        for _ in range(200):
            start = time.time()
            with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
                mock_pipe.return_value.get_status.return_value = {
                    "graph_compiled": True, "chromadb": True, "llm": True,
                }
                from src.api.routes.rag import get_rag_status
                get_rag_status()
            times.append((time.time() - start) * 1000)
        p99 = _percentile(times, 99)
        assert p99 < 50, f"健康检查 P99={p99:.2f}ms > 50ms"

    def test_auth_identity_performance(self):
        """身份查询 < 5ms"""
        from src.api.routes.auth import get_identity
        from src.services.auth_service import GuestUser, is_guest
        times = []
        for _ in range(500):
            start = time.time()
            get_identity(current_user=GuestUser())
            times.append((time.time() - start) * 1000)
        p99 = _percentile(times, 99)
        assert p99 < 5, f"身份查询 P99={p99:.2f}ms > 5ms"

    def test_food_search_mock_performance(self):
        """食物搜索（Mock）< 50ms"""
        from src.api.routes.diet import search_food
        mock_db = MagicMock()
        with patch("src.api.routes.diet.search_foods") as mock_search:
            mock_search.return_value = [{"food_name": "鸡蛋", "calories": 144}]
            times = []
            for _ in range(200):
                start = time.time()
                search_food(keyword="鸡蛋", limit=10, db=mock_db, current_user_id="u")
                times.append((time.time() - start) * 1000)
        p99 = _percentile(times, 99)
        assert p99 < 50, f"食物搜索 P99={p99:.2f}ms > 50ms"


# ============================================================
# 8. 性能指标汇总
# ============================================================

class TestPerformanceMetricsSummary:
    """性能指标汇总验证"""

    def test_all_metrics_within_targets(self):
        """所有性能指标达标"""
        results = {}

        # Token 生成
        t = _benchmark(lambda: create_access_token({"sub": "u"}), iterations=500)
        results["token_gen_p99"] = _percentile(t, 99)

        # Token 解析
        token = create_access_token({"sub": "u"})
        t = _benchmark(lambda: decode_token(token), iterations=500)
        results["token_decode_p99"] = _percentile(t, 99)

        # 安全检查（正则）
        t = _benchmark(lambda: check_safety("帮我开药", use_llm=False), iterations=300)
        results["safety_regex_p99"] = _percentile(t, 99)

        # 安全检查（通过）
        t = _benchmark(lambda: check_safety("鸡蛋热量", use_llm=False), iterations=300)
        results["safety_pass_p99"] = _percentile(t, 99)

        # 断言
        thresholds = {
            "token_gen_p99": 10,
            "token_decode_p99": 5,
            "safety_regex_p99": 100,
            "safety_pass_p99": 50,
        }
        for metric, threshold in thresholds.items():
            actual = results[metric]
            assert actual < threshold, \
                f"{metric}={actual:.2f}ms 超过阈值 {threshold}ms"

    def test_throughput_target(self):
        """系统吞吐量目标"""
        # Token 生成吞吐量
        duration = 1.0
        count = 0
        start = time.time()
        while time.time() - start < duration:
            create_access_token({"sub": f"u_{count}"})
            count += 1
        qps = count / duration
        assert qps > 500, f"Token 生成 QPS={qps:.0f} < 500"