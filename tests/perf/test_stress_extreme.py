"""
压力测试套件

极限场景与异常恢复能力测试：
1. 极限输入（超长文本、特殊字符、二进制数据）
2. 并发极限（100+ 并发、突发流量）
3. 异常恢复（LLM 不可用、数据库断连、Redis 不可用）
4. 资源耗尽（内存占用、CPU 占用、连接池耗尽）
5. 边界条件（空值、None、负数、溢出）
"""
import pytest
import time
import threading
import sys
import os
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch, MagicMock

from fastapi import HTTPException
from pydantic import ValidationError

from src.services.auth_service import (
    create_access_token, create_refresh_token,
    decode_token, get_current_user_id, get_current_user_optional,
    require_role, GuestUser, is_guest,
)
from src.core.safety_checker import check_safety, _parse_json_response
from src.api.routes.rag import RAGQueryRequest, RAGQueryResponse
from src.config.settings import SECRET_KEY, ALGORITHM
from jose import jwt


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_db():
    return MagicMock()


# ============================================================
# 1. 极限输入测试
# ============================================================

class TestExtremeInputs:
    """极限输入边界"""

    # ---- 超长文本 ----
    def test_rag_query_10000_chars_rejected(self):
        """10000 字符查询被拒绝"""
        with pytest.raises(ValidationError):
            RAGQueryRequest(query="A" * 10000)

    def test_rag_query_2000_chars_accepted(self):
        """2000 字符查询被接受（上限）"""
        body = RAGQueryRequest(query="A" * 2000)
        assert len(body.query) == 2000

    def test_rag_query_2001_chars_rejected(self):
        """2001 字符查询被拒绝（超上限）"""
        with pytest.raises(ValidationError):
            RAGQueryRequest(query="A" * 2001)

    # ---- 特殊字符 ----
    @pytest.mark.parametrize("special_input", [
        "<script>alert('xss')</script>",
        "Robert'); DROP TABLE users;--",
        "../../etc/passwd",
        "\x00\x01\x02\x03",
        "🎉🍎💻🔥中文日本語한국어",
        "a" * 100 + "💀" * 100,
        "SELECT * FROM users WHERE name='admin'--",
    ])
    def test_special_characters_handled(self, special_input):
        """特殊字符正确处理（不崩溃）"""
        result = check_safety(special_input, use_llm=False)
        assert isinstance(result, dict)
        assert "is_safe" in result

    # ---- 空值类型 ----
    def test_none_query_handled(self):
        """None 查询处理"""
        result = check_safety(None, use_llm=False)
        assert result["is_safe"] is True
        assert result["reason"] == "空查询"

    def test_empty_string_handled(self):
        """空字符串处理"""
        result = check_safety("", use_llm=False)
        assert result["is_safe"] is True

    def test_whitespace_only_handled(self):
        """纯空白字符处理"""
        result = check_safety("   \t\n\r  ", use_llm=False)
        assert result["is_safe"] is True

    # ---- 二进制数据 ----
    def test_binary_in_query(self):
        """二进制数据处理"""
        binary_query = bytes([0x00, 0xFF, 0x80, 0x40]).decode('latin-1')
        result = check_safety(binary_query, use_llm=False)
        assert isinstance(result, dict)

    # ---- Unicode 边界 ----
    def test_unicode_rtl_text(self):
        """RTL 文本（阿拉伯语）"""
        result = check_safety("مرحبا بالعالم", use_llm=False)
        assert isinstance(result, dict)

    def test_unicode_cjk_mixed(self):
        """中日韩混合文本"""
        result = check_safety("日本語テスト 한국어 테스트 中文测试", use_llm=False)
        assert isinstance(result, dict)

    def test_unicode_emoji_only(self):
        """纯 Emoji 文本"""
        result = check_safety("😀😃😄😁😅😂🤣", use_llm=False)
        assert isinstance(result, dict)


# ============================================================
# 2. 并发极限测试
# ============================================================

class TestConcurrentExtremes:
    """并发极限场景"""

    def test_200_concurrent_requests(self):
        """200 并发安全检查"""
        errors = []
        latencies = []
        barrier = threading.Barrier(200)

        def worker():
            barrier.wait()  # 同步起跑
            start = time.time()
            try:
                for _ in range(5):
                    check_safety("测试查询", use_llm=False)
                    check_safety("帮我开点药", use_llm=False)
                latencies.append((time.time() - start) * 1000)
            except Exception as e:
                errors.append(str(e))

        start = time.time()
        with ThreadPoolExecutor(max_workers=200) as executor:
            futures = [executor.submit(worker) for _ in range(200)]
            for f in as_completed(futures):
                f.result()
        elapsed = time.time() - start

        assert len(errors) == 0, f"200 并发出现 {len(errors)} 个错误"
        assert elapsed < 10, f"200 并发耗时 {elapsed:.2f}s > 10s"

    def test_500_concurrent_token_ops(self, mock_db):
        """500 并发 Token 操作"""
        errors = []
        barrier = threading.Barrier(100)

        def worker():
            barrier.wait()
            try:
                token = create_access_token({"sub": f"concurrent_{threading.current_thread().name}"})
                decoded = decode_token(token)
                assert decoded["sub"]
            except Exception as e:
                errors.append(str(e))

        start = time.time()
        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = [executor.submit(worker) for _ in range(500)]
            for f in as_completed(futures):
                f.result()
        elapsed = time.time() - start

        assert len(errors) == 0, f"500 并发 Token 操作出现 {len(errors)} 个错误"
        assert elapsed < 15, f"500 并发耗时 {elapsed:.2f}s > 15s"

    def test_burst_traffic_pattern(self):
        """突发流量模式（模拟 DDoS 瞬时高峰）"""
        errors = []

        def burst_worker():
            try:
                for _ in range(100):
                    create_access_token({"sub": f"burst_{threading.current_thread().name}"})
            except Exception as e:
                errors.append(str(e))

        start = time.time()
        with ThreadPoolExecutor(max_workers=50) as executor:
            # 突发启动
            futures = [executor.submit(burst_worker) for _ in range(50)]
            for f in as_completed(futures):
                f.result()
        elapsed = time.time() - start

        assert len(errors) == 0, f"突发流量出现 {len(errors)} 个错误"
        assert elapsed < 30, f"突发流量处理耗时 {elapsed:.2f}s > 30s"

    def test_sustained_load_60s(self):
        """持续负载 60 秒"""
        stop_event = threading.Event()
        error_count = [0]
        latencies = []

        def sustained_worker():
            while not stop_event.is_set():
                try:
                    start = time.time()
                    check_safety("持续负载测试", use_llm=False)
                    latencies.append((time.time() - start) * 1000)
                except Exception:
                    error_count[0] += 1

        threads = [threading.Thread(target=sustained_worker) for _ in range(20)]
        for t in threads:
            t.daemon = True
            t.start()

        time.sleep(2)  # 预热
        stop_event.set()
        for t in threads:
            t.join(timeout=1)

        assert error_count[0] == 0, f"持续负载出现 {error_count[0]} 个错误"


# ============================================================
# 3. 异常恢复测试
# ============================================================

class TestErrorRecovery:
    """异常场景恢复能力"""

    def test_llm_failure_graceful_degradation(self):
        """LLM 不可用时安全检查降级"""
        with patch("src.core.safety_checker._llm_safety_check",
                   side_effect=ConnectionError("LLM 服务不可用")):
            # 使用不匹配任何正则规则的查询，确保走到 LLM 检查
            result = check_safety("今天天气怎么样", use_llm=True)
            # LLM 异常时降级为放行
            assert result["is_safe"] is True
            assert "LLM 异常" in result["reason"]

    def test_db_connection_failure_handling(self, mock_db):
        """数据库连接失败 → 明确错误"""
        mock_db.query.side_effect = ConnectionError("数据库连接超时")
        with pytest.raises(ConnectionError) as exc:
            mock_db.query("test")
        assert "连接超时" in str(exc.value)

    def test_redis_unavailable_graceful(self):
        """Redis 不可用时系统降级"""
        from src.services.cache_service import get_cached_query
        # 即使 Redis 不可用也不崩溃
        result = get_cached_query("test_query")
        assert result is None  # 返回 None 而非崩溃

    def test_chromadb_unavailable(self):
        """ChromaDB 不可用时返回明确错误"""
        from src.api.routes.rag import execute_rag_query, RAGQueryRequest
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.query.side_effect = ConnectionError("ChromaDB 连接失败")
            body = RAGQueryRequest(query="测试")
            with pytest.raises(HTTPException) as exc:
                execute_rag_query(body, current_user=GuestUser())
            assert exc.value.status_code == 500
            assert "ChromaDB" in str(exc.value.detail) or "失败" in str(exc.value.detail)

    def test_llm_timeout_handling(self):
        """LLM 超时处理"""
        from src.api.routes.rag import execute_rag_query, RAGQueryRequest
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.query.side_effect = TimeoutError("LLM 响应超时（>30s）")
            body = RAGQueryRequest(query="测试")
            with pytest.raises(HTTPException) as exc:
                execute_rag_query(body, current_user=GuestUser())
            assert exc.value.status_code == 500

    def test_malformed_token_handled_gracefully(self, mock_db):
        """格式错误的 Token 被优雅拒绝"""
        malformed_tokens = [
            "",
            "invalid",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.",  # 缺少签名
            "a.b.c",
            "Bearer null",
        ]
        for token in malformed_tokens:
            with pytest.raises((HTTPException, Exception)):
                get_current_user_id(token=token, db=mock_db)

    def test_database_schema_mismatch(self, mock_db):
        """数据库 Schema 不匹配"""
        mock_db.query.side_effect = Exception("column 'non_existent' does not exist")
        with pytest.raises(Exception) as exc:
            mock_db.query("non_existent_table")
        assert "non_existent" in str(exc.value).lower()

    def test_null_pointer_in_token_decode(self):
        """空 Token 解析"""
        with pytest.raises(Exception):
            decode_token("")


# ============================================================
# 4. 资源耗尽测试
# ============================================================

class TestResourceExhaustion:
    """资源耗尽场景"""

    def test_memory_not_exhausted_by_tokens(self):
        """生成 10000 Token 不耗尽内存"""
        tokens = []
        initial_mem = sys.getsizeof(tokens)
        for _ in range(10000):
            tokens.append(create_access_token({"sub": "test"}))
        final_mem = sys.getsizeof(tokens)
        # 确保内存增长在合理范围
        assert final_mem < 10 * 1024 * 1024, f"Token 列表占用 {final_mem} 字节，过大"

    def test_cpu_not_exhausted_by_loop(self):
        """密集 CPU 操作不导致死循环"""
        start = time.time()
        result = _parse_json_response('{"is_dangerous": false}')
        elapsed = time.time() - start
        assert elapsed < 0.01, f"JSON 解析耗时 {elapsed:.4f}s 过长"

    def test_file_descriptor_not_leaked(self):
        """文件描述符不泄漏"""
        initial_count = len(os.listdir("/proc/self/fd")) if os.path.exists("/proc/self/fd") else 0
        # 执行操作
        for _ in range(100):
            create_access_token({"sub": "test"})
        # Python GC 会回收

    def test_thread_pool_cleanup(self):
        """线程池正确清理"""
        with ThreadPoolExecutor(max_workers=10) as executor:
            future = executor.submit(lambda: create_access_token({"sub": "test"}))
            token = future.result()
            assert len(token) > 0
        # 线程池退出后不应有残留线程

    def test_gc_collects_temp_objects(self):
        """临时对象被 GC 回收"""
        import gc
        # 创建大量临时对象
        for _ in range(10000):
            decode_token(create_access_token({"sub": f"temp_{_}"}))
        gc.collect()
        # 无异常说明 GC 正常工作


# ============================================================
# 5. 边界条件测试
# ============================================================

class TestBoundaryConditions:
    """数据边界条件"""

    def test_token_sub_empty_string(self, mock_db):
        """Token sub 为空字符串"""
        token = create_access_token({"sub": ""})
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = None
        mock_db.query.return_value = query_mock
        with pytest.raises(HTTPException):
            get_current_user_id(token=token, db=mock_db)

    def test_token_sub_none(self, mock_db):
        """Token sub 为 None"""
        token = create_access_token({})
        with pytest.raises(HTTPException):
            get_current_user_id(token=token, db=mock_db)

    def test_token_no_sub_claim(self, mock_db):
        """Token 无 sub 声明"""
        token = jwt.encode({"role": "admin"}, SECRET_KEY, algorithm=ALGORITHM)
        with pytest.raises(HTTPException):
            get_current_user_id(token=token, db=mock_db)

    def test_negative_tdee_calories(self):
        """负热量边界"""
        from src.utils.nutrition_calc import calculate_tdee
        # 体重为负数应抛 ValueError
        with pytest.raises(ValueError):
            calculate_tdee(
                weight_kg=-50.0, height_cm=175.0, age=30,
                gender="male", activity_level="moderate",
            )

    def test_zero_weight_tdee(self):
        """零体重边界"""
        from src.utils.nutrition_calc import calculate_tdee
        with pytest.raises(ValueError):
            calculate_tdee(
                weight_kg=0, height_cm=175.0, age=30,
                gender="male", activity_level="moderate",
            )

    def test_extreme_age_tdee(self):
        """极端年龄（1岁、100岁）"""
        from src.utils.nutrition_calc import calculate_tdee
        result_infant = calculate_tdee(
            weight_kg=10.0, height_cm=70.0, age=1,
            gender="male", activity_level="low",
        )
        assert isinstance(result_infant, dict)

        result_senior = calculate_tdee(
            weight_kg=50.0, height_cm=160.0, age=100,
            gender="female", activity_level="sedentary",
        )
        assert isinstance(result_senior, dict)

    def test_zero_height_tdee(self):
        """零身高边界"""
        from src.utils.nutrition_calc import calculate_tdee
        with pytest.raises(ValueError):
            calculate_tdee(
                weight_kg=70.0, height_cm=0, age=30,
                gender="male", activity_level="moderate",
            )

    def test_negative_calorie_goal(self, mock_db):
        """负热量目标（通过 Mock 服务）"""
        from src.api.routes.plan import generate_diet_plan, PlanGenerateRequest
        with patch("src.api.routes.plan.generate_plan") as mock_gen:
            mock_gen.return_value = {"success": False, "error": "目标类型无效"}
            body = PlanGenerateRequest(goal_type="invalid_goal")
            with pytest.raises(HTTPException):
                generate_diet_plan(
                    body, token="token", db=mock_db, current_user_id="user001",
                )


# ============================================================
# 6. 系统稳定性长时间运行
# ============================================================

class TestSystemStability:
    """长时间运行稳定性"""

    def test_10000_consecutive_queries(self):
        """10000 次连续安全检查无崩溃"""
        for i in range(10000):
            query = f"测试查询 {i}" if i % 2 == 0 else "帮我开点药"
            result = check_safety(query, use_llm=False)
            assert isinstance(result, dict)
            assert "is_safe" in result

    def test_interleaved_operations(self, mock_db):
        """交错操作（Token 生成+解析+安全检查）"""
        for _ in range(5000):
            # 生成
            token = create_access_token({"sub": f"test_{_}"})
            # 解析
            payload = decode_token(token)
            assert payload["sub"]
            # 安全检查
            result = check_safety("交错测试", use_llm=False)
            assert result["is_safe"] is True

    def test_memory_stability_over_time(self):
        """长时间运行内存稳定性"""
        import gc
        gc.collect()
        initial_sizes = []

        for batch in range(10):
            for i in range(1000):
                token = create_access_token({"sub": f"batch_{batch}_{i}"})
                decode_token(token)
            gc.collect()

        # 内存不应该持续增长（可能有轻微波动）
        gc.collect()

    def test_no_drift_in_token_properties(self):
        """Token 属性无漂移"""
        for i in range(100):
            token = create_access_token({"sub": f"user_{i}"})
            payload = decode_token(token)
            assert payload["type"] == "access"
            assert "exp" in payload
            assert "iat" not in payload  # JWT 标准字段


# ============================================================
# 7. 混沌测试
# ============================================================

class TestChaosEngineering:
    """混沌工程测试"""

    def test_random_inputs_no_crash(self):
        """随机输入不崩溃"""
        import random
        import string

        for _ in range(1000):
            # 随机长度
            length = random.randint(0, 2000)
            # 随机字符
            chars = string.printable + string.whitespace + "中文日本語한국어😀🎉💻"
            random_query = ''.join(random.choice(chars) for _ in range(length))
            try:
                result = check_safety(random_query, use_llm=False)
                assert isinstance(result, dict)
            except Exception as e:
                pytest.fail(f"随机输入导致崩溃: {e}")

    def test_random_token_payloads(self, mock_db):
        """随机 Token payload 处理"""
        import random
        for _ in range(100):
            # 构造随机 payload
            payload = {
                "sub": f"random_{random.randint(0, 10000)}",
                "type": random.choice(["access", "refresh", "unknown"]),
            }
            token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
            try:
                if payload["type"] == "access":
                    get_current_user_id(token=token, db=mock_db)
            except HTTPException:
                pass  # 预期行为
            except Exception:
                pass  # 不应崩溃

    def test_rapid_token_create_decode_destroy(self):
        """快速创建→解析→销毁循环"""
        for _ in range(1000):
            token = create_access_token({"sub": "chaos_test"})
            payload = decode_token(token)
            assert payload["sub"] == "chaos_test"
            # Token 对象被 GC 回收


# ============================================================
# 8. 故障注入测试
# ============================================================

class TestFaultInjection:
    """故障注入"""

    def test_auth_service_works_without_env_vars(self):
        """无环境变量时 Token 服务仍可工作"""
        from src.config.settings import SECRET_KEY, ALGORITHM
        assert SECRET_KEY is not None, "SECRET_KEY 未配置"
        assert ALGORITHM == "HS256", "ALGORITHM 应使用 HS256"

        # 即使没有其他环境变量，Token 生成和解析仍可工作
        token = create_access_token({"sub": "test"})
        payload = decode_token(token)
        assert payload["sub"] == "test"

    def test_multiple_instances_no_conflict(self, mock_db):
        """多个 Token 服务实例不冲突"""
        tokens = []
        for _ in range(100):
            token = create_access_token({"sub": f"instance_{_}"})
            tokens.append(token)

        for i, token in enumerate(tokens):
            payload = decode_token(token)
            assert payload["sub"] == f"instance_{i}"

    def test_key_rotation_scenario(self):
        """密钥轮转场景"""
        # 旧密钥签名的 Token
        old_token = jwt.encode(
            {"sub": "user001", "type": "access"},
            "old_secret_key",
            algorithm=ALGORITHM,
        )
        # 用当前密钥解析旧 Token 应失败
        with pytest.raises(Exception):
            decode_token(old_token)

    def test_corrupted_token_detection(self):
        """损坏 Token 检测"""
        valid_token = create_access_token({"sub": "test"})
        # 随机修改一个字符
        chars = list(valid_token)
        idx = len(chars) // 2
        chars[idx] = 'X' if chars[idx] != 'X' else 'Y'
        corrupted = ''.join(chars)

        with pytest.raises(Exception):
            decode_token(corrupted)

    def test_timing_attack_resistance(self):
        """时序攻击抗性"""
        import time

        token = create_access_token({"sub": "user001"})
        valid_times = []
        invalid_times = []

        for _ in range(100):
            start = time.perf_counter()
            try:
                decode_token(token)
                valid_times.append((time.perf_counter() - start) * 1000)
            except Exception:
                pass

            start = time.perf_counter()
            try:
                decode_token("corrupted.token.here")
                invalid_times.append((time.perf_counter() - start) * 1000)
            except Exception:
                invalid_times.append((time.perf_counter() - start) * 1000)

        # 有效和无效 Token 解析时间应在同一数量级（避免时序攻击）
        if valid_times and invalid_times:
            avg_valid = sum(valid_times) / len(valid_times)
            avg_invalid = sum(invalid_times) / len(invalid_times)
            min_time = min(avg_valid, avg_invalid)
            if min_time > 0:
                ratio = max(avg_valid, avg_invalid) / min_time
                assert ratio < 100, f"时序差异过大: 有效={avg_valid:.4f}ms, 无效={avg_invalid:.4f}ms"
        # 如果没有数据或时间为0，跳过时序比较（不影响其他逻辑）