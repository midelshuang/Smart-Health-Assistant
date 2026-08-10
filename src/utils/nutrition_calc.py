"""
TDEE（每日总能量消耗）计算工具

依据：
- BMR 公式：Mifflin-St Jeor（1990）——美国营养学会推荐，比 Harris-Benedict 更准
  男性：BMR = 10 * 体重(kg) + 6.25 * 身高(cm) - 5 * 年龄 + 5
  女性：BMR = 10 * 体重(kg) + 6.25 * 身高(cm) - 5 * 年龄 - 161
- TDEE = BMR × 活动系数
- 目标调整：
  - 减脂：TDEE - 500 kcal/天（每周约减 0.5kg）
  - 增肌：TDEE + 300 kcal/天
  - 维持：TDEE
- 医学安全底线：男性 ≥ 1500 kcal/天，女性 ≥ 1200 kcal/天

宏量营养素分配（基于目标热量）：
  减脂：蛋白 30% / 脂肪 30% / 碳水 40%
  增肌：蛋白 25% / 脂肪 25% / 碳水 50%
  维持：蛋白 20% / 脂肪 30% / 碳水 50%
（蛋白质 4 kcal/g、脂肪 9 kcal/g、碳水 4 kcal/g）
"""

from typing import Dict, Any, Optional, Literal
from decimal import Decimal
from src.utils.logger import setup_logger

logger = setup_logger(name="TDEE_Calculator")

# 活动系数映射（Harris-Benedict 标准）
ACTIVITY_LEVELS = {
    "sedentary": 1.2,        # 久坐：办公室工作，无规律运动
    "light": 1.375,          # 轻度：每周 1-3 次轻度运动
    "moderate": 1.55,        # 中度：每周 3-5 次中等运动
    "active": 1.725,         # 高度：每周 6-7 次剧烈运动
    "very_active": 1.9,      # 极高：体力工作者或每天训练
}

# 活动系数中文别名
ACTIVITY_ALIASES = {
    "久坐": "sedentary", "静坐": "sedentary", "无运动": "sedentary",
    "轻度": "light", "偶尔": "light", "每周1-3次": "light",
    "中度": "moderate", "规律": "moderate", "每周3-5次": "moderate",
    "高度": "active", "经常": "active", "每周6-7次": "active",
    "极高": "very_active", "运动员": "very_active",
}

# 目标类型
GoalType = Literal["weight_loss", "muscle_gain", "maintenance"]

GOAL_ALIASES = {
    "减脂": "weight_loss", "减重": "weight_loss", "减肥": "weight_loss",
    "增肌": "muscle_gain", "增重": "muscle_gain",
    "维持": "maintenance", "保持": "maintenance", "健康": "maintenance",
}

# 不同目标的宏量营养素分配比例（蛋白/脂肪/碳水）
MACRO_RATIOS = {
    "weight_loss":   {"protein": 0.30, "fat": 0.30, "carbs": 0.40},
    "muscle_gain":   {"protein": 0.25, "fat": 0.25, "carbs": 0.50},
    "maintenance":   {"protein": 0.20, "fat": 0.30, "carbs": 0.50},
}

# 目标调整的卡路里偏移
GOAL_CALORIE_ADJUSTMENT = {
    "weight_loss": -500,    # 减脂：每天 -500 kcal
    "muscle_gain": +300,    # 增肌：每天 +300 kcal
    "maintenance": 0,
}

# 医学安全底线（kcal/天）
MIN_SAFE_CALORIES = {
    "male": 1500,
    "female": 1200,
}


def resolve_activity_level(activity: str) -> str:
    """
    将中文/英文活动描述解析为标准 key

    Args:
        activity: 用户输入的活动等级（中文/英文/别名）

    Returns:
        标准活动等级 key（sedentary/light/moderate/active/very_active）
    """
    if not activity:
        return "sedentary"

    activity_lower = activity.lower().strip()

    # 直接命中英文标准 key
    if activity_lower in ACTIVITY_LEVELS:
        return activity_lower

    # 中文别名映射
    for cn, std in ACTIVITY_ALIASES.items():
        if cn in activity or activity in cn:
            return std

    return "sedentary"


def resolve_goal_type(goal: str) -> str:
    """
    将中文/英文目标描述解析为标准 key

    Args:
        goal: 用户输入的目标类型

    Returns:
        标准目标 key（weight_loss/muscle_gain/maintenance）
    """
    if not goal:
        return "maintenance"

    goal_lower = goal.lower().strip()

    if goal_lower in GOAL_CALORIE_ADJUSTMENT:
        return goal_lower

    for cn, std in GOAL_ALIASES.items():
        if cn in goal or goal in cn:
            return std

    return "maintenance"


def calculate_bmr(
    weight_kg: float,
    height_cm: float,
    age: int,
    gender: str,
) -> float:
    """
    计算 BMR（基础代谢率）—— 使用 Mifflin-St Jeor 公式

    Args:
        weight_kg: 体重（kg）
        height_cm: 身高（cm）
        age: 年龄
        gender: 性别（male/female/男/女）

    Returns:
        BMR (kcal/天)
    """
    gender_std = "male" if (gender or "").lower() in ("male", "男", "m") else "female"

    if gender_std == "male":
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    else:
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161

    return round(bmr, 1)


def calculate_tdee(
    weight_kg: float,
    height_cm: float,
    age: int,
    gender: str,
    activity_level: str = "moderate",
    goal_type: str = "maintenance",
    body_fat_percentage: Optional[float] = None,
) -> Dict[str, Any]:
    """
    计算 TDEE 与目标热量、宏量营养素分配

    Args:
        weight_kg: 体重（kg）
        height_cm: 身高（cm）
        age: 年龄
        gender: 性别（male/female/男/女）
        activity_level: 活动等级（中文/英文均可）
        goal_type: 目标类型（weight_loss/muscle_gain/maintenance，中文亦可）
        body_fat_percentage: 体脂率（可选，传入则额外计算去脂体重 BMR）

    Returns:
        dict: {
            "bmr": float,
            "tdee": float,
            "target_calories": float,
            "activity_level": str,
            "goal_type": str,
            "macros": {"protein_g", "fat_g", "carbs_g"},
            "bmi": float,
            "safety_warning": Optional[str],
        }
    """
    # 1. 参数校验
    if weight_kg <= 0 or height_cm <= 0 or age <= 0:
        raise ValueError(f"参数非法: weight={weight_kg}, height={height_cm}, age={age}")

    # 2. 解析活动等级与目标类型
    activity_std = resolve_activity_level(activity_level)
    goal_std = resolve_goal_type(goal_type)
    gender_std = "male" if (gender or "").lower() in ("male", "男", "m") else "female"

    # 3. 计算 BMR
    # 若提供体脂率，使用 Katch-McArdle 公式（更精确）：
    #   BMR = 370 + 21.6 * 去脂体重(kg)
    # 去脂体重 = 体重 * (1 - 体脂率/100)
    if body_fat_percentage is not None and 5 < body_fat_percentage < 60:
        lean_mass = weight_kg * (1 - body_fat_percentage / 100)
        bmr = 370 + 21.6 * lean_mass
        logger.info(f"使用 Katch-McArdle 公式计算 BMR（体脂率={body_fat_percentage}%，去脂体重={lean_mass:.1f}kg）")
    else:
        bmr = calculate_bmr(weight_kg, height_cm, age, gender_std)

    # 4. 计算 TDEE
    activity_factor = ACTIVITY_LEVELS[activity_std]
    tdee = bmr * activity_factor

    # 5. 目标热量调整
    adjustment = GOAL_CALORIE_ADJUSTMENT[goal_std]
    target_calories = tdee + adjustment

    # 6. 安全底线校验
    min_safe = MIN_SAFE_CALORIES[gender_std]
    safety_warning = None
    if target_calories < min_safe:
        safety_warning = (
            f"目标热量 {target_calories:.0f} kcal 低于医学安全底线 {min_safe} kcal/天，"
            f"已自动调整为 {min_safe} kcal。请勿长期低于此值，建议咨询营养师。"
        )
        target_calories = float(min_safe)
        logger.warning(safety_warning)

    # 7. 计算宏量营养素
    ratios = MACRO_RATIOS[goal_std]
    protein_g = (target_calories * ratios["protein"]) / 4  # 蛋白质 4 kcal/g
    fat_g = (target_calories * ratios["fat"]) / 9          # 脂肪 9 kcal/g
    carbs_g = (target_calories * ratios["carbs"]) / 4      # 碳水 4 kcal/g

    # 8. BMI 计算
    bmi = weight_kg / ((height_cm / 100) ** 2)

    result = {
        "bmr": round(bmr, 1),
        "tdee": round(tdee, 1),
        "target_calories": round(target_calories, 1),
        "activity_level": activity_std,
        "activity_factor": activity_factor,
        "goal_type": goal_std,
        "macros": {
            "protein_g": round(protein_g, 1),
            "fat_g": round(fat_g, 1),
            "carbs_g": round(carbs_g, 1),
        },
        "macros_ratios": ratios,
        "bmi": round(bmi, 1),
        "gender": gender_std,
        "safety_warning": safety_warning,
    }

    logger.info(
        f"TDEE 计算完成: BMR={result['bmr']}, TDEE={result['tdee']}, "
        f"目标={result['target_calories']} kcal, 目标类型={goal_std}"
    )

    return result


def get_calorie_advice(target_calories: float, goal_type: str) -> str:
    """
    生成针对用户目标的简短饮食建议文案

    Args:
        target_calories: 目标每日热量
        goal_type: 目标类型

    Returns:
        饮食建议文字
    """
    goal_std = resolve_goal_type(goal_type)

    if goal_std == "weight_loss":
        return (
            f"建议每日摄入 {target_calories:.0f} kcal，制造 500 kcal 热量缺口。"
            f"优先选择高蛋白、高纤维食物，避免精制糖和油炸食品。"
            f"配合每周 3-5 次有氧 + 力量训练，效果更佳。"
        )
    elif goal_std == "muscle_gain":
        return (
            f"建议每日摄入 {target_calories:.0f} kcal，制造 300 kcal 热量盈余。"
            f"训练日补充足量碳水与蛋白质（每公斤体重 1.6-2.2g 蛋白质），"
            f"非训练日略减碳水，保持蛋白质摄入。"
        )
    else:
        return (
            f"建议每日摄入 {target_calories:.0f} kcal 维持当前体重。"
            f"保持均衡饮食，规律运动，定期监测体重与体脂率变化。"
        )
