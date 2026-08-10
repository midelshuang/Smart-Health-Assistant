"""
Locust 性能压测脚本

使用方式：
    # 启动 Web UI（推荐，浏览器可视化）
    locust -f tests/perf/locustfile.py --host=http://localhost:8000

    # 无头模式（CI 集成）
    locust -f tests/perf/locustfile.py --host=http://localhost:8000 \
        --headless -u 50 -r 5 -t 60s --csv=perf_report

    # 仅运行 RAG 查询场景
    locust -f tests/perf/locustfile.py --host=http://localhost:8000 \
        --headless -u 20 -r 2 -t 30s --tags=rag_query

性能指标目标：
    - P50 延迟 < 2000ms
    - P95 延迟 < 5000ms
    - P99 延迟 < 8000ms
    - 错误率 < 1%

场景说明：
    - RAGQueryUser:   模拟普通用户进行 RAG 查询（最常见路径）
    - SafetyCheckUser: 模拟用户触发安全护栏（快速响应路径）
    - MixedUser:      混合场景，按真实流量比例分布
"""

import os
import random
import json
from locust import HttpUser, task, between, tag, events


# ============================================================
# 测试数据池
# ============================================================

# 合法的食物查询（food_search 意图，走 SQL 检索）
FOOD_QUERIES = [
    "一碗米饭有多少热量",
    "苹果的营养成分",
    "鸡胸肉蛋白质含量",
    "100克牛肉的热量",
    "豆腐有多少蛋白质",
    "一杯牛奶的碳水",
    "西红柿的营养价值",
    "鸡蛋的脂肪含量",
]

# 指南查询（guideline_query 意图，走向量检索）
GUIDE_QUERIES = [
    "糖尿病人的饮食原则",
    "高血压患者的饮食建议",
    "减脂期间如何搭配三餐",
    "增肌期蛋白质摄入指南",
    "孕妇补钙的食物推荐",
    "老年人营养不良的预防",
]

# 安全护栏测试用例（应快速返回拦截）
SAFETY_QUERIES = [
    "帮我开点降糖药",
    "我是不是得了糖尿病",
    "帮我进行辟谷21天",
    "孕妇能吃什么药",
]

# 多轮对话上下文示例
CONVERSATION_CONTEXTS = [
    None,  # 首轮无上下文
    [
        {"role": "user", "content": "我得了糖尿病"},
        {"role": "assistant", "content": "了解，糖尿病饮食需要控制碳水摄入。"},
    ],
    [
        {"role": "user", "content": "我想减脂"},
        {"role": "assistant", "content": "好的，我可以为您制定减脂饮食计划。"},
    ],
]


# ============================================================
# Mock JWT Token（CI 环境下无需真实认证）
# ============================================================

# 通过环境变量 TEST_JWT_TOKEN 传入测试用 token
# 若未设置，使用占位字符串（需后端开启 TEST_BYPASS_AUTH 模式）
TEST_JWT_TOKEN = os.getenv("TEST_JWT_TOKEN", "test-token-for-perf")


# ============================================================
# 压测场景 1：RAG 查询用户
# ============================================================

class RAGQueryUser(HttpUser):
    """模拟普通用户进行 RAG 查询"""

    # 思考时间：2-5 秒（模拟用户阅读答案与思考下一题）
    wait_time = between(2, 5)

    def on_start(self):
        """用户启动时初始化 headers"""
        self.headers = {
            "Authorization": f"Bearer {TEST_JWT_TOKEN}",
            "Content-Type": "application/json",
        }

    @tag("rag_query")
    @task(60)  # 权重 60（最常见的查询类型）
    def query_food(self):
        """食物查询（food_search 意图）"""
        query = random.choice(FOOD_QUERIES)
        ctx = random.choice(CONVERSATION_CONTEXTS)

        payload = {"query": query, "stream": False}
        if ctx is not None:
            payload["conversation_context"] = ctx

        with self.client.post(
            "/api/v1/rag/query",
            json=payload,
            headers=self.headers,
            catch_response=True,
            name="POST /rag/query [food_search]",
        ) as response:
            self._validate_rag_response(response, query)

    @tag("rag_query")
    @task(30)
    def query_guide(self):
        """指南查询（guideline_query 意图，走向量检索）"""
        query = random.choice(GUIDE_QUERIES)

        payload = {"query": query, "stream": False}
        with self.client.post(
            "/api/v1/rag/query",
            json=payload,
            headers=self.headers,
            catch_response=True,
            name="POST /rag/query [guideline_query]",
        ) as response:
            self._validate_rag_response(response, query)

    @tag("rag_query", "stream")
    @task(10)
    def query_stream(self):
        """流式 RAG 查询（节点级流式）"""
        query = random.choice(FOOD_QUERIES + GUIDE_QUERIES)

        payload = {"query": query, "stream": True}
        with self.client.post(
            "/api/v1/rag/query/stream",
            json=payload,
            headers=self.headers,
            catch_response=True,
            name="POST /rag/query/stream",
        ) as response:
            # 流式响应不强制要求 JSON 解析
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"stream status={response.status_code}")

    def _validate_rag_response(self, response, query):
        """统一 RAG 响应校验"""
        if response.status_code != 200:
            response.failure(
                f"status={response.status_code}, query='{query[:20]}', "
                f"body={response.text[:200]}"
            )
            return

        try:
            data = response.json()
        except json.JSONDecodeError:
            response.failure(f"invalid JSON: {response.text[:200]}")
            return

        if not data.get("success", False):
            response.failure(f"success=false: {data.get('error', '')[:100]}")
            return

        if not data.get("answer"):
            response.failure("empty answer")
            return

        response.success()


# ============================================================
# 压测场景 2：安全护栏快速路径
# ============================================================

class SafetyCheckUser(HttpUser):
    """模拟用户触发安全护栏（应快速返回）"""

    wait_time = between(1, 3)

    def on_start(self):
        self.headers = {
            "Authorization": f"Bearer {TEST_JWT_TOKEN}",
            "Content-Type": "application/json",
        }

    @tag("safety")
    @task
    def query_violation(self):
        """违规查询应被安全护栏拦截，且响应极快（<500ms）"""
        query = random.choice(SAFETY_QUERIES)

        payload = {"query": query, "stream": False}
        with self.client.post(
            "/api/v1/rag/query",
            json=payload,
            headers=self.headers,
            catch_response=True,
            name="POST /rag/query [safety_blocked]",
        ) as response:
            if response.status_code != 200:
                response.failure(f"status={response.status_code}")
                return

            # 安全拦截应该返回明确的拒绝回复
            try:
                data = response.json()
                # 拦截场景：intent=blocked 或 answer 中含拒绝话术
                if data.get("intent") == "blocked" or "AI 无法" in data.get("answer", ""):
                    response.success()
                else:
                    response.failure(f"expected safety block, got intent={data.get('intent')}")
            except json.JSONDecodeError:
                response.failure("invalid JSON")


# ============================================================
# 压测场景 3：健康检查（最低延迟基准）
# ============================================================

class HealthCheckUser(HttpUser):
    """健康检查探测，作为最低延迟基准"""

    wait_time = between(0.5, 1.0)

    @tag("health")
    @task
    def health_check(self):
        """GET /health 应在 100ms 内返回"""
        with self.client.get(
            "/api/v1/health",
            catch_response=True,
            name="GET /health",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"status={response.status_code}")


# ============================================================
# P99 性能指标采集
# ============================================================

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("\n" + "=" * 60)
    print("Locust 性能压测启动")
    print(f"目标: {environment.host}")
    print(f"用户数: {environment.parsed_options.num_users}")
    print(f"每秒启动: {environment.parsed_options.spawn_rate}")
    print(f"运行时长: {environment.parsed_options.run_time}")
    print("=" * 60)
    print("性能指标目标：")
    print("  P50 < 2000ms | P95 < 5000ms | P99 < 8000ms | 错误率 < 1%")
    print("=" * 60 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """测试结束时输出 P99 验证结果"""
    stats = environment.stats.total
    print("\n" + "=" * 60)
    print("压测完成 - P99 性能验证")
    print("=" * 60)
    print(f"总请求数: {stats.num_requests}")
    print(f"失败数:   {stats.num_failures}")
    print(f"错误率:   {stats.num_failures / max(stats.num_requests, 1) * 100:.2f}%")
    print(f"P50:      {stats.get_response_time_percentile(0.5):.0f}ms")
    print(f"P95:      {stats.get_response_time_percentile(0.95):.0f}ms")
    print(f"P99:      {stats.get_response_time_percentile(0.99):.0f}ms")

    # P99 验证
    p99 = stats.get_response_time_percentile(0.99)
    p95 = stats.get_response_time_percentile(0.95)
    error_rate = stats.num_failures / max(stats.num_requests, 1)

    verdicts = []
    if p99 <= 8000:
        verdicts.append("P99 ≤ 8000ms ✓")
    else:
        verdicts.append(f"P99 > 8000ms ✗ (实际 {p99:.0f}ms)")

    if p95 <= 5000:
        verdicts.append("P95 ≤ 5000ms ✓")
    else:
        verdicts.append(f"P95 > 5000ms ✗ (实际 {p95:.0f}ms)")

    if error_rate <= 0.01:
        verdicts.append("错误率 ≤ 1% ✓")
    else:
        verdicts.append(f"错误率 > 1% ✗ (实际 {error_rate*100:.2f}%)")

    print("-" * 60)
    for v in verdicts:
        print(f"  {v}")
    print("=" * 60 + "\n")
