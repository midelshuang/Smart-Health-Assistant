"""
食物解析服务：基于 Function Calling 的结构化饮食解析

将用户的自然语言饮食描述（如"我今天中午吃了一碗米饭和两个鸡蛋"）
通过 LLM Function Calling 解析为结构化数据（食物名、数量、单位、餐次）。

设计要点：
- 使用 OpenAI Function Calling 协议（ZhipuAI 兼容）
- 工具 schema 严格约束输出格式，避免 LLM 自由发挥
- 低温度（0.3）保证解析稳定
- 支持多食物同时解析（一次说多句话也能拆开）
- 食物名规范化后再去 nutrition_facts 查询匹配
"""

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from src.models.db_models import NutritionFact
from src.core.llm_client import get_llm_client
from src.utils.logger import setup_logger
from decimal import Decimal
import json

logger = setup_logger(name="FoodParser")


# ============================================================
# Function Calling 工具定义
# ============================================================

PARSE_FOOD_INTAKE_TOOL = {
    "type": "function",
    "function": {
        "name": "parse_food_intake",
        "description": (
            "解析用户自然语言描述的饮食内容，提取出每一项食物的名称、数量、单位与餐次。"
            "用户可能一次描述多个食物（如'中午吃了一碗米饭、两个鸡蛋和一杯牛奶'）。"
            "需将每个食物拆分为独立项返回。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "解析出的食物列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "food_name": {
                                "type": "string",
                                "description": "食物名称（规范化后的中文，如'米饭'、'鸡蛋'、'牛奶'）",
                            },
                            "quantity": {
                                "type": "number",
                                "description": "数量数值（如 1、2、1.5）",
                            },
                            "unit": {
                                "type": "string",
                                "enum": ["碗", "个", "杯", "份", "盘", "克", "毫升", "片", "块", "根", "把"],
                                "description": "数量单位",
                            },
                            "estimated_grams": {
                                "type": "number",
                                "description": (
                                    "估算的克重（克）。若单位是'碗'默认 200g，'个'鸡蛋默认 50g，"
                                    "'杯'牛奶默认 250ml，'份'默认 150g，'盘'默认 300g，"
                                    "'片'面包默认 30g，'块'豆腐默认 100g，'根'香蕉默认 100g。"
                                ),
                            },
                            "meal_type": {
                                "type": "string",
                                "enum": ["breakfast", "lunch", "dinner", "snack"],
                                "description": "餐次类型",
                            },
                        },
                        "required": ["food_name", "quantity", "unit", "estimated_grams", "meal_type"],
                    },
                },
                "summary": {
                    "type": "string",
                    "description": "对用户输入的一句话总结（用于日志和回显）",
                },
            },
            "required": ["items", "summary"],
        },
    },
}

PARSE_SYSTEM_PROMPT = (
    "你是一个严谨的饮食解析助手。"
    "任务：从用户的自然语言描述中提取出每一项食物，并规范化为结构化数据。\n\n"
    "规则：\n"
    "1. 食物名使用通用中文名（如'米饭'而非'白米饭一碗'）\n"
    "2. 若用户未明说餐次，根据时间词推断（'早上'→breakfast，'中午'→lunch，'晚上'→dinner，"
    "'下午茶/零食/加餐'→snack）；若完全无线索，默认 snack\n"
    "3. estimated_grams 必须填写估算克重，单位克\n"
    "4. 若用户描述模糊（如'吃了点水果'），尽量根据常识估算克重\n"
    "5. 如果用户输入与饮食完全无关，返回空 items 列表\n"
)


def parse_food_intake(text: str) -> Dict[str, Any]:
    """
    用 LLM Function Calling 解析用户饮食描述

    Args:
        text: 用户的自然语言饮食描述

    Returns:
        dict: {
            "items": [ {food_name, quantity, unit, estimated_grams, meal_type} ],
            "summary": str,
            "raw_tool_call": dict,    # 原始 tool_call 对象
            "success": bool,
            "error": Optional[str],
        }
    """
    if not text or not text.strip():
        return {"items": [], "summary": "", "success": False, "error": "输入为空"}

    llm = get_llm_client()

    messages = [
        {"role": "system", "content": PARSE_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]

    logger.info(f"开始解析饮食描述: {text}")

    result = llm.chat_with_tools(
        messages=messages,
        tools=[PARSE_FOOD_INTAKE_TOOL],
        temperature=0.3,
        max_tokens=1024,
        tool_choice={"type": "function", "function": {"name": "parse_food_intake"}},
    )

    tool_calls = result.get("tool_calls", [])
    if not tool_calls:
        return {
            "items": [],
            "summary": "",
            "success": False,
            "error": f"LLM 未触发工具调用, finish_reason={result.get('finish_reason')}",
        }

    tool_call = tool_calls[0]
    try:
        args_str = tool_call.get("function", {}).get("arguments", "{}")
        args = json.loads(args_str) if isinstance(args_str, str) else args_str

        items = args.get("items", [])
        summary = args.get("summary", "")

        # 字段补全与类型转换
        normalized_items = []
        for it in items:
            normalized_items.append({
                "food_name": str(it.get("food_name", "")).strip(),
                "quantity": float(it.get("quantity", 1) or 1),
                "unit": str(it.get("unit", "克")),
                "estimated_grams": float(it.get("estimated_grams", 0) or 0),
                "meal_type": str(it.get("meal_type", "snack")),
            })

        logger.info(f"解析成功: {len(normalized_items)} 项食物, summary='{summary}'")
        return {
            "items": normalized_items,
            "summary": summary,
            "raw_tool_call": tool_call,
            "success": True,
            "error": None,
        }

    except json.JSONDecodeError as e:
        logger.error(f"工具调用参数解析失败: {e}, args={tool_call.get('function', {}).get('arguments')}")
        return {
            "items": [],
            "summary": "",
            "success": False,
            "error": f"参数解析失败: {e}",
        }
    except Exception as e:
        logger.error(f"饮食解析异常: {e}")
        return {
            "items": [],
            "summary": "",
            "success": False,
            "error": str(e),
        }


def match_food_in_db(db: Session, food_name: str, limit: int = 3) -> List[Dict[str, Any]]:
    """
    在 nutrition_facts 表中模糊匹配食物

    Args:
        db: 数据库会话
        food_name: 解析出的食物名
        limit: 返回数量上限

    Returns:
        匹配到的食物列表，每项包含 id、food_name、calories_per_100g 等
    """
    if not food_name:
        return []

    # 1. 精确匹配
    exact = db.query(NutritionFact).filter(
        NutritionFact.food_name == food_name
    ).first()
    if exact:
        return [_nutrition_to_dict(exact)]

    # 2. 模糊匹配（包含关系）
    results = db.query(NutritionFact).filter(
        NutritionFact.food_name.contains(food_name)
    ).limit(limit).all()

    if results:
        return [_nutrition_to_dict(r) for r in results]

    # 3. 反向匹配（食物名包含查询词）
    results = db.query(NutritionFact).filter(
        NutritionFact.aliases.contains(food_name)
    ).limit(limit).all()

    return [_nutrition_to_dict(r) for r in results]


def _nutrition_to_dict(n: NutritionFact) -> Dict[str, Any]:
    return {
        "id": n.id,
        "food_name": n.food_name,
        "aliases": n.aliases,
        "category": n.category,
        "calories_per_100g": float(n.calories_per_100g or 0),
        "protein": float(n.protein or 0),
        "fat": float(n.fat or 0),
        "carbs": float(n.carbs or 0),
    }
