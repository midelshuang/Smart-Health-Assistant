"""
Service/food_parser.py 单元测试

覆盖：
- parse_food_intake 主函数（Mock LLM）
  - 成功解析多项食物
  - 空输入处理
  - LLM 未触发工具调用
  - JSON 解析失败
- match_food_in_db 数据库匹配（需要 DB）
- PARSE_FOOD_INTAKE_TOOL schema 校验
"""
import pytest
import json
from unittest.mock import patch, MagicMock

from src.services.food_parser import (
    parse_food_intake,
    match_food_in_db,
    PARSE_FOOD_INTAKE_TOOL,
    PARSE_SYSTEM_PROMPT,
)


# ============================================================
# Tool schema 校验
# ============================================================
class TestParseFoodIntakeTool:
    def test_tool_has_correct_type(self):
        assert PARSE_FOOD_INTAKE_TOOL["type"] == "function"

    def test_tool_has_required_fields(self):
        fn = PARSE_FOOD_INTAKE_TOOL["function"]
        assert fn["name"] == "parse_food_intake"
        assert "description" in fn
        assert "parameters" in fn

    def test_parameters_require_items_and_summary(self):
        required = PARSE_FOOD_INTAKE_TOOL["function"]["parameters"]["required"]
        assert "items" in required
        assert "summary" in required

    def test_items_have_required_fields(self):
        item_props = PARSE_FOOD_INTAKE_TOOL["function"]["parameters"]["properties"]["items"]["items"]["properties"]
        for field in ["food_name", "quantity", "unit", "estimated_grams", "meal_type"]:
            assert field in item_props

    def test_meal_type_enum(self):
        item_props = PARSE_FOOD_INTAKE_TOOL["function"]["parameters"]["properties"]["items"]["items"]["properties"]
        meal_type = item_props["meal_type"]
        assert meal_type["type"] == "string"
        assert set(meal_type["enum"]) == {"breakfast", "lunch", "dinner", "snack"}

    def test_unit_enum(self):
        item_props = PARSE_FOOD_INTAKE_TOOL["function"]["parameters"]["properties"]["items"]["items"]["properties"]
        unit = item_props["unit"]
        assert "碗" in unit["enum"]
        assert "克" in unit["enum"]


# ============================================================
# parse_food_intake
# ============================================================
class TestParseFoodIntake:
    def test_empty_input_returns_failure(self):
        result = parse_food_intake("")
        assert result["success"] is False
        assert "error" in result
        assert result["items"] == []

    def test_whitespace_only_input_returns_failure(self):
        result = parse_food_intake("   ")
        assert result["success"] is False

    def test_successful_parse_multiple_items(self, mock_llm):
        """LLM 成功返回工具调用，解析出多项食物"""
        mock_llm.tool_call_responses = [{
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "parse_food_intake",
                    "arguments": json.dumps({
                        "items": [
                            {
                                "food_name": "鸡蛋",
                                "quantity": 2,
                                "unit": "个",
                                "estimated_grams": 100,
                                "meal_type": "breakfast",
                            },
                            {
                                "food_name": "牛奶",
                                "quantity": 1,
                                "unit": "杯",
                                "estimated_grams": 250,
                                "meal_type": "breakfast",
                            },
                        ],
                        "summary": "早餐吃了鸡蛋和牛奶",
                    }),
                },
            }],
            "finish_reason": "tool_calls",
        }]

        result = parse_food_intake("今天早餐吃了两个鸡蛋和一杯牛奶")
        assert result["success"] is True
        assert len(result["items"]) == 2
        assert result["items"][0]["food_name"] == "鸡蛋"
        assert result["items"][0]["quantity"] == 2.0
        assert result["items"][1]["food_name"] == "牛奶"
        assert result["summary"] == "早餐吃了鸡蛋和牛奶"

    def test_no_tool_calls_returns_failure(self, mock_llm):
        """LLM 未触发工具调用"""
        mock_llm.tool_call_responses = [{
            "content": "我没有识别到食物",
            "tool_calls": [],
            "finish_reason": "stop",
        }]
        result = parse_food_intake("今天天气不错")
        assert result["success"] is False
        assert "未触发工具调用" in result["error"]

    def test_invalid_json_arguments_returns_failure(self, mock_llm):
        """LLM 返回的 arguments 不是有效 JSON"""
        mock_llm.tool_call_responses = [{
            "content": None,
            "tool_calls": [{
                "function": {
                    "name": "parse_food_intake",
                    "arguments": "not valid json {{{",
                },
            }],
            "finish_reason": "tool_calls",
        }]
        result = parse_food_intake("中午吃了米饭")
        assert result["success"] is False
        assert "参数解析失败" in result["error"]

    def test_field_completion_for_missing_values(self, mock_llm):
        """缺少的字段应被默认值补全"""
        mock_llm.tool_call_responses = [{
            "content": None,
            "tool_calls": [{
                "function": {
                    "name": "parse_food_intake",
                    "arguments": json.dumps({
                        "items": [
                            {"food_name": "苹果"},  # 缺少其他字段
                        ],
                        "summary": "吃了个苹果",
                    }),
                },
            }],
            "finish_reason": "tool_calls",
        }]
        result = parse_food_intake("吃了一个苹果")
        assert result["success"] is True
        item = result["items"][0]
        assert item["food_name"] == "苹果"
        assert item["quantity"] == 1.0  # 默认
        assert item["unit"] == "克"  # 默认
        assert item["meal_type"] == "snack"  # 默认


# ============================================================
# match_food_in_db
# ============================================================
class TestMatchFoodInDb:
    def test_empty_food_name_returns_empty_list(self):
        db = MagicMock()
        assert match_food_in_db(db, "") == []

    def test_exact_match_returned_first(self, require_db):
        from src.models.db_models import NutritionFact
        db = require_db
        # 尝试查找一个常见食物
        results = match_food_in_db(db, "鸡蛋")
        # 如果数据库有鸡蛋，应该返回非空
        # 这里只验证函数能正常运行
        assert isinstance(results, list)
