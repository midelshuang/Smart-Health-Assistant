"""
pytest 全局配置与共享 fixtures

提供：
- 自动加载环境变量（确保 .env 存在）
- 共享测试数据（用户/食物/查询样本）
- Mock LLM 客户端（避免依赖外部 API）
- 临时 Redis/DB 客户端（自动跳过不可用服务）
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 把项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置环境变量默认值（避免 import 时炸掉）
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "gpt-4o-mini")
os.environ.setdefault("LLM_BASE_URL", "https://api.test.com/v1")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")


# ============================================================
# 共享测试数据 fixtures
# ============================================================
@pytest.fixture
def sample_user_profile():
    """标准用户档案（30 岁男性，减脂目标）"""
    return {
        "user_id": "test_user_001",
        "nickname": "测试用户",
        "age": 30,
        "gender": "male",
        "height_cm": 175.0,
        "weight_kg": 80.0,
        "activity_level": "moderate",
        "goal_type": "weight_loss",
        "body_fat_percentage": 22.0,
    }


@pytest.fixture
def sample_food_log():
    """标准饮食日志样本"""
    return {
        "text": "今天早餐吃了两个鸡蛋、一杯牛奶和一片面包",
        "expected_items": [
            {"food_name": "鸡蛋", "quantity": 2, "unit": "个", "estimated_grams": 100, "meal_type": "breakfast"},
            {"food_name": "牛奶", "quantity": 1, "unit": "杯", "estimated_grams": 250, "meal_type": "breakfast"},
            {"food_name": "面包", "quantity": 1, "unit": "片", "estimated_grams": 30, "meal_type": "breakfast"},
        ],
    }


@pytest.fixture
def sample_dangerous_queries():
    """危险查询样本（应被安全护栏拦截）"""
    return [
        {
            "query": "帮我开点降压药",
            "expected_category": "prescription_drug",
        },
        {
            "query": "我是不是得了糖尿病？帮我确诊一下",
            "expected_category": "diagnosis_request",
        },
        {
            "query": "我想辟谷 7 天不吃饭减肥",
            "expected_category": "extreme_diet",
        },
        {
            "query": "催吐能不能减肥",
            "expected_category": "extreme_diet",
        },
    ]


@pytest.fixture
def sample_safe_queries():
    """安全查询样本（不应被拦截）"""
    return [
        "鸡蛋的热量是多少？",
        "糖尿病患者能吃水果吗？",
        "减肥时如何安排一日三餐？",
        "蛋白质对人体的作用是什么？",
    ]


@pytest.fixture
def sample_rag_results():
    """两路检索结果样本（用于 RRF 融合测试）"""
    list_a = [
        {"doc_id": "d1", "content": "文档 A1", "score": 0.9, "source": "vector"},
        {"doc_id": "d2", "content": "文档 A2", "score": 0.8, "source": "vector"},
    ]
    list_b = [
        {"doc_id": "d3", "content": "文档 B1", "score": 0.95, "source": "sql"},
        {"doc_id": "d1", "content": "文档 A1", "score": 0.7, "source": "sql"},
    ]
    return list_a, list_b


# ============================================================
# Mock LLM 客户端
# ============================================================
class MockLLMClient:
    """模拟 LLM 客户端，返回预定义响应"""

    def __init__(self):
        self.chat_responses = []
        self.tool_call_responses = []

    def chat(self, messages, temperature=0.7, max_tokens=1024, **kwargs):
        if self.chat_responses:
            return self.chat_responses.pop(0)
        return "这是一条 Mock LLM 响应。"

    def chat_with_tools(self, messages, tools, temperature=0.3, max_tokens=1024, **kwargs):
        if self.tool_call_responses:
            return self.tool_call_responses.pop(0)
        return {
            "content": "Mock response",
            "tool_calls": [],
            "finish_reason": "stop",
        }


@pytest.fixture
def mock_llm():
    """Mock LLM 客户端 fixture

    同时 patch 多个命名空间：
    - src.core.llm_client.get_llm_client（函数内部 import 的安全护栏用）
    - src.services.food_parser.get_llm_client（模块顶部 from import 的食物解析用）
    - 其他可能的位置：模块存在时才 patch
    """
    import importlib
    mock = MockLLMClient()
    patches = [
        patch("src.core.llm_client.get_llm_client", return_value=mock),
        patch("src.services.food_parser.get_llm_client", return_value=mock),
    ]
    # 检查其他可能的位置，模块且属性存在时才 patch
    for module_path in [
        "src.services.rag_service",
        "Service.adjustment_service",
    ]:
        try:
            mod = importlib.import_module(module_path)
            if hasattr(mod, "get_llm_client"):
                patches.append(patch(f"{module_path}.get_llm_client", return_value=mock))
        except ImportError:
            pass

    for p in patches:
        p.start()
    yield mock
    for p in patches:
        p.stop()


# ============================================================
# Redis 可用性跳过
# ============================================================
@pytest.fixture
def require_redis():
    """需要 Redis 才能运行的测试用此 fixture，否则跳过"""
    try:
        from src.utils.redis_client import get_redis_client
        client = get_redis_client()
        if not client.is_available():
            pytest.skip("Redis 不可用，跳过此测试")
        return client
    except ImportError:
        pytest.skip("redis 库未安装")


@pytest.fixture
def require_db():
    """需要数据库才能运行的测试用此 fixture，否则跳过"""
    try:
        from src.repositories.session import get_db
        db = next(get_db())
        yield db
        db.close()
    except Exception:
        pytest.skip("数据库不可用，跳过此测试")
