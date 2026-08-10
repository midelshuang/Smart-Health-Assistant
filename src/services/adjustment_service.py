"""
动态调整引擎（AdjustmentNode）

核心职责：
1. 读取用户当前激活的饮食计划（Plan.config 含 target_calories + macros）
2. 读取用户今日已记录的饮食（DailyDietLog 聚合）
3. 计算差值：计划目标 - 今日已摄入
4. 按宏量营养素权重分摊到后续餐次
5. 返回调整后的剩余配额与建议

调整算法：
- 剩余热量 = 计划目标热量 - 今日已摄入热量
- 剩余宏量 = 计划目标宏量 - 今日已摄入宏量
- 若剩余 < 0（超标）：按比例削减后续餐次，但单餐不得低于 MIN_MEAL_CALORIES
- 若剩余 > 0（不足）：按比例增加后续餐次，但单餐不得高于 MAX_MEAL_CALORIES
- 剩余餐次权重按计划中各餐占比分配（如早午晚 25%/35%/30%/10%）

该模块可独立调用，也可作为 LangGraph 节点集成。
"""

from typing import Dict, Any, List, Optional
from datetime import date, datetime
from sqlalchemy.orm import Session
from decimal import Decimal
from src.utils.logger import setup_logger

logger = setup_logger(name="AdjustmentEngine")


# ============================================================
# 常量
# ============================================================

# 默认餐次权重（占总热量的比例）
DEFAULT_MEAL_WEIGHTS = {
    "breakfast": 0.25,
    "lunch": 0.35,
    "dinner": 0.30,
    "snack": 0.10,
}

# 安全底线：单餐不得低于此值（kcal）
MIN_MEAL_CALORIES = 200
# 单餐不得高于此值（kcal）
MAX_MEAL_CALORIES = 1200

# 餐次时间顺序
MEAL_ORDER = ["breakfast", "lunch", "snack", "dinner"]


# ============================================================
# 数据聚合
# ============================================================

def get_today_intake(db: Session, user_id: str, log_date: date = None) -> Dict[str, Any]:
    """
    聚合用户某天的实际摄入（按宏量营养素）

    Args:
        db: 数据库会话
        user_id: 用户 ID
        log_date: 日期，默认今天

    Returns:
        dict: {
            "total_calories": float,
            "total_protein_g": float,
            "total_fat_g": float,
            "total_carbs_g": float,
            "by_meal": {meal_type: {"calories":..., "protein_g":...}},
            "items": [...]
        }
    """
    from src.models.db_models import DailyDietLog
    if log_date is None:
        log_date = date.today()

    logs = db.query(DailyDietLog).filter(
        DailyDietLog.user_id == user_id,
        DailyDietLog.log_date == log_date,
    ).all()

    by_meal = {k: {"calories": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0}
               for k in MEAL_ORDER}

    total_cal = 0.0
    total_pro = 0.0
    total_fat = 0.0
    total_carb = 0.0
    items = []

    for log in logs:
        cal = float(log.calculated_calories or 0)
        grams = float(log.quantity_grams or 0)

        # 从 nutrition_info 反推宏量
        if log.nutrition_info:
            ratio = Decimal(str(grams)) / Decimal("100")
            pro = float(Decimal(str(log.nutrition_info.protein or 0)) * ratio)
            fat = float(Decimal(str(log.nutrition_info.fat or 0)) * ratio)
            carb = float(Decimal(str(log.nutrition_info.carbs or 0)) * ratio)
        else:
            pro = fat = carb = 0.0

        meal_key = log.meal_type if log.meal_type in by_meal else "snack"
        by_meal[meal_key]["calories"] += cal
        by_meal[meal_key]["protein_g"] += pro
        by_meal[meal_key]["fat_g"] += fat
        by_meal[meal_key]["carbs_g"] += carb

        total_cal += cal
        total_pro += pro
        total_fat += fat
        total_carb += carb

        items.append({
            "log_id": log.id,
            "food_name": log.nutrition_info.food_name if log.nutrition_info else "未知",
            "meal_type": meal_key,
            "calories": round(cal, 1),
            "quantity_grams": round(grams, 1),
        })

    return {
        "total_calories": round(total_cal, 1),
        "total_protein_g": round(total_pro, 1),
        "total_fat_g": round(total_fat, 1),
        "total_carbs_g": round(total_carb, 1),
        "by_meal": {k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in by_meal.items()},
        "items": items,
        "item_count": len(items),
    }


# ============================================================
# 核心调整算法
# ============================================================

def compute_adjustment(
    plan_config: Dict[str, Any],
    today_intake: Dict[str, Any],
    current_meal: str = None,
) -> Dict[str, Any]:
    """
    计算动态调整方案（纯函数，不依赖数据库）

    Args:
        plan_config: Plan.config 字典，含 target_calories / macros / meals
        today_intake: get_today_intake 返回值
        current_meal: 当前要规划的餐次（breakfast/lunch/dinner/snack），
                     None=自动取下一个未吃的餐次

    Returns:
        dict: {
            "target_calories": float,        # 计划目标总热量
            "consumed_calories": float,      # 今日已摄入热量
            "remaining_calories": float,     # 剩余热量
            "remaining_protein_g": float,   # 剩余蛋白
            "remaining_fat_g": float,
            "remaining_carbs_g": float,
            "next_meal": str,                # 建议下一餐
            "next_meal_target_calories": float,   # 下一餐建议摄入
            "next_meal_target_protein_g": float,
            "next_meal_target_fat_g": float,
            "next_meal_target_carbs_g": float,
            "adjustment_ratio": float,       # 调整比例（>1 增加，<1 削减）
            "status": str,                   # on_track / over_budget / under_budget
            "message": str,                  # 人类可读建议
        }
    """
    # 1. 提取计划目标
    target_cal = float(plan_config.get("target_calories", 0))
    target_macros = plan_config.get("macros", {})
    target_pro = float(target_macros.get("protein_g", 0))
    target_fat = float(target_macros.get("fat_g", 0))
    target_carb = float(target_macros.get("carbs_g", 0))

    if target_cal <= 0:
        return {
            "error": "计划配置缺少 target_calories",
            "status": "invalid_plan",
        }

    # 2. 提取今日已摄入
    consumed_cal = today_intake.get("total_calories", 0)
    consumed_pro = today_intake.get("total_protein_g", 0)
    consumed_fat = today_intake.get("total_fat_g", 0)
    consumed_carb = today_intake.get("total_carbs_g", 0)

    # 3. 计算剩余配额
    remaining_cal = target_cal - consumed_cal
    remaining_pro = target_pro - consumed_pro
    remaining_fat = target_fat - consumed_fat
    remaining_carb = target_carb - consumed_carb

    # 4. 判断状态
    if remaining_cal < -100:
        status = "over_budget"
        message = f"今日已超标 {-remaining_cal:.0f} kcal，建议下一餐清淡饮食（蔬菜+瘦肉），跳过加餐。"
    elif remaining_cal > target_cal * 0.5:
        status = "under_budget"
        message = f"今日剩余 {remaining_cal:.0f} kcal 配额充足，可正常安排下一餐。"
    else:
        status = "on_track"
        message = f"今日进度正常，剩余 {remaining_cal:.0f} kcal，按计划执行下一餐即可。"

    # 5. 确定下一餐
    next_meal = current_meal or _determine_next_meal(today_intake.get("by_meal", {}))

    # 6. 计算下一餐的调整后目标
    meal_weight = DEFAULT_MEAL_WEIGHTS.get(next_meal, 0.25)
    next_meal_target_cal = target_cal * meal_weight

    # 调整比例：剩余热量 / (剩余餐次总权重 * 目标热量)
    # 找出还未吃的餐次权重总和
    remaining_meal_weights = _get_remaining_meal_weights(today_intake.get("by_meal", {}), next_meal)
    remaining_weight_sum = sum(remaining_meal_weights.values())

    if remaining_weight_sum > 0:
        adjustment_ratio = remaining_cal / (target_cal * remaining_weight_sum)
    else:
        adjustment_ratio = 1.0

    # 应用调整比例，但夹在安全区间
    next_meal_adjusted_cal = next_meal_target_cal * adjustment_ratio
    next_meal_adjusted_cal = max(MIN_MEAL_CALORIES, min(MAX_MEAL_CALORIES, next_meal_adjusted_cal))

    # 宏量按同样比例调整
    next_meal_target_pro = target_pro * meal_weight * adjustment_ratio
    next_meal_target_fat = target_fat * meal_weight * adjustment_ratio
    next_meal_target_carb = target_carb * meal_weight * adjustment_ratio

    result = {
        "target_calories": round(target_cal, 1),
        "consumed_calories": round(consumed_cal, 1),
        "remaining_calories": round(remaining_cal, 1),
        "remaining_protein_g": round(remaining_pro, 1),
        "remaining_fat_g": round(remaining_fat, 1),
        "remaining_carbs_g": round(remaining_carb, 1),
        "next_meal": next_meal,
        "next_meal_target_calories": round(next_meal_adjusted_cal, 1),
        "next_meal_target_protein_g": round(next_meal_target_pro, 1),
        "next_meal_target_fat_g": round(next_meal_target_fat, 1),
        "next_meal_target_carbs_g": round(next_meal_target_carb, 1),
        "adjustment_ratio": round(adjustment_ratio, 3),
        "status": status,
        "message": message,
        "consumed_by_meal": today_intake.get("by_meal", {}),
        "remaining_meals": list(remaining_meal_weights.keys()),
    }

    logger.info(
        f"调整计算: 目标 {target_cal} / 已摄入 {consumed_cal} / 剩余 {remaining_cal} / "
        f"下一餐 {next_meal}={next_meal_adjusted_cal:.0f} kcal (比例 {adjustment_ratio:.2f})"
    )

    return result


def _determine_next_meal(by_meal: Dict[str, Dict]) -> str:
    """
    根据已吃的餐次推断下一餐

    逻辑：按时间顺序找第一个未吃的餐次，都吃了就返回 snack
    """
    for meal in MEAL_ORDER:
        if by_meal.get(meal, {}).get("calories", 0) <= 0:
            return meal
    return "snack"


def _get_remaining_meal_weights(by_meal: Dict[str, Dict], current_meal: str) -> Dict[str, float]:
    """
    获取从当前餐次开始的剩余餐次权重

    Args:
        by_meal: 各餐已摄入情况
        current_meal: 当前餐次

    Returns:
        剩余餐次权重字典
    """
    # 找出从 current_meal 开始往后所有未吃的餐次
    start_idx = MEAL_ORDER.index(current_meal) if current_meal in MEAL_ORDER else 0

    remaining = {}
    for i, meal in enumerate(MEAL_ORDER):
        if i < start_idx:
            continue
        # 已有摄入的餐次跳过（表示已吃过）
        if by_meal.get(meal, {}).get("calories", 0) > 0:
            continue
        remaining[meal] = DEFAULT_MEAL_WEIGHTS.get(meal, 0.25)

    # 如果没有剩余未吃餐次（都吃过了），至少给当前餐次一个权重
    if not remaining:
        remaining[current_meal] = DEFAULT_MEAL_WEIGHTS.get(current_meal, 0.25)

    return remaining


# ============================================================
# 对外服务入口
# ============================================================

def get_today_adjustment(
    db: Session,
    user_id: str,
    plan_id: int = None,
    current_meal: str = None,
    log_date: date = None,
) -> Dict[str, Any]:
    """
    获取用户今日的动态调整方案（完整服务入口）

    流程：
    1. 查询用户激活的饮食计划（plan_id 未传则取最新 active 计划）
    2. 聚合今日已摄入
    3. 调用 compute_adjustment 计算调整方案
    4. 返回完整结构化结果

    Args:
        db: 数据库会话
        user_id: 用户 ID
        plan_id: 指定计划 ID，None=取最新 active 计划
        current_meal: 当前餐次，None=自动推断
        log_date: 日期，默认今天

    Returns:
        dict: 调整方案（含计划、摄入、建议）
    """
    from src.models.db_models import Plan

    # 1. 查询计划
    if plan_id:
        plan = db.query(Plan).filter(
            Plan.id == plan_id,
            Plan.user_id == user_id,
        ).first()
    else:
        plan = db.query(Plan).filter(
            Plan.user_id == user_id,
            Plan.status == "active",
        ).order_by(Plan.created_at.desc()).first()

    if not plan:
        return {
            "success": False,
            "error": "未找到激活的饮食计划，请先生成计划",
            "user_id": user_id,
        }

    plan_config = plan.config or {}
    if not plan_config.get("target_calories"):
        return {
            "success": False,
            "error": "计划配置缺失 target_calories 字段",
            "plan_id": plan.id,
        }

    # 2. 聚合今日摄入
    today_intake = get_today_intake(db, user_id, log_date)

    # 3. 计算调整
    adjustment = compute_adjustment(plan_config, today_intake, current_meal)

    return {
        "success": True,
        "plan_id": plan.id,
        "plan_goal_type": plan.goal_type,
        "plan_target_calories": plan_config.get("target_calories"),
        "plan_macros": plan_config.get("macros"),
        "today_intake": today_intake,
        "adjustment": adjustment,
    }
