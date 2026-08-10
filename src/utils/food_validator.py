"""
食物安全校验器（food_validator）

提供饮食相关的安全边界校验功能：
1. 食物别名映射（标准化食物名称）
2. 饮食禁忌检查（基于用户档案：过敏/疾病禁忌）
3. 单餐宏量营养素上限校验（如单餐碳水 ≤ 150g）
4. 每日总摄入合理性校验
5. 食物-药物相互作用检查（如华法林 + 富维 K 食物）

被以下模块调用：
- Service/adjustment_service.py：动态计划调整后校验份量是否越界
- Service/plan_service.py：生成饮食计划时校验总能量与宏量配比
- Service/diet_service.py：用户记录饮食时提示潜在风险
"""

from typing import Dict, Any, List, Tuple, Optional
from src.config import settings
from src.utils.logger import setup_logger

logger = setup_logger(name="FoodValidator")


# ============================================================
# 1. 食物别名映射（标准化食物名 → 规范名）
# ============================================================
# 键：常见别名/俗称，值：规范食物名
FOOD_ALIASES: Dict[str, str] = {
    # 主食类
    "米饭": "米饭", "白饭": "米饭", "白米": "米饭", "粳米饭": "米饭",
    "面条": "面条", "挂面": "面条", "汤面": "面条",
    "馒头": "馒头", "馍": "馒头", "花卷": "馒头",
    "粥": "白米粥", "稀饭": "白米粥", "白粥": "白米粥",
    # 蛋白质类
    "鸡蛋": "鸡蛋", "土鸡蛋": "鸡蛋", "白煮蛋": "鸡蛋",
    "鸡胸肉": "鸡胸肉", "鸡胸": "鸡胸肉",
    "牛奶": "牛奶", "纯牛奶": "牛奶", "鲜奶": "牛奶",
    "豆腐": "豆腐", "北豆腐": "豆腐", "南豆腐": "豆腐",
    "瘦肉": "猪瘦肉", "猪肉": "猪瘦肉",
    "牛肉": "牛肉", "瘦牛肉": "牛肉",
    "鱼": "鱼肉", "鱼肉": "鱼肉",
    "虾": "虾肉", "基围虾": "虾肉", "明虾": "虾肉",
    # 蔬菜类
    "西红柿": "番茄", "番茄": "番茄",
    "土豆": "马铃薯", "马铃薯": "马铃薯",
    "青菜": "小白菜", "油菜": "小白菜",
    "黄瓜": "黄瓜", "青瓜": "黄瓜",
    # 水果类
    "苹果": "苹果", "红富士": "苹果",
    "香蕉": "香蕉", "芭蕉": "香蕉",
    "西瓜": "西瓜",
    # 其他
    "一碗饭": "米饭", "一碗面": "面条", "一个蛋": "鸡蛋",
}


def normalize_food_name(raw_name: str) -> str:
    """
    将食物别名/俗称标准化为规范食物名

    Args:
        raw_name: 用户输入的原始食物名

    Returns:
        规范化后的食物名（未匹配则原样返回）
    """
    if not raw_name:
        return raw_name
    # 精确匹配
    if raw_name in FOOD_ALIASES:
        return FOOD_ALIASES[raw_name]
    # 去除前后空格后匹配
    cleaned = raw_name.strip()
    if cleaned in FOOD_ALIASES:
        return FOOD_ALIASES[cleaned]
    # 子串匹配（包含某别名）
    for alias, canonical in FOOD_ALIASES.items():
        if alias in cleaned and len(alias) >= 2:
            return canonical
    return raw_name


# ============================================================
# 2. 单餐宏量营养素安全上限
# ============================================================
# 依据：《中国居民膳食营养素参考摄入量》及内分泌科常规建议
# 单位：克（g）
MEAL_SAFETY_LIMITS: Dict[str, float] = {
    "carbs_g": 150.0,        # 单餐碳水不超过 150g（约 600kcal）
    "protein_g": 60.0,       # 单餐蛋白质不超过 60g（一次性摄入上限）
    "fat_g": 40.0,           # 单餐脂肪不超过 40g（约 360kcal）
    "calories": 1200.0,      # 单餐总能量不超过 1200kcal
}

# 每日总摄入安全上限（成人）
DAILY_SAFETY_LIMITS: Dict[str, float] = {
    "calories": 3500.0,      # 每日总能量不超过 3500kcal（普通成人）
    "carbs_g": 500.0,        # 每日碳水不超过 500g
    "protein_g": 200.0,      # 每日蛋白质不超过 200g（避免肾负担）
    "fat_g": 120.0,          # 每日脂肪不超过 120g
    "sugar_g": 50.0,         # 每日添加糖不超过 50g（WHO 建议 < 25g）
    "sodium_mg": 2300.0,     # 每日钠不超过 2300mg（中国成人 UL）
}


def validate_meal_safety(meal_items: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """
    校验单餐宏量营养素是否越界

    用于 AdjustmentNode 在调整份量后检查单餐是否超标。
    若超标，应触发"超标预警"而非继续强行分配。

    Args:
        meal_items: 单餐食物列表，每项包含 calories/protein/fat/carbs 字段
            例: [{"food": "米饭", "calories": 200, "protein": 4, "fat": 0.5, "carbs": 45}]

    Returns:
        (is_safe, warnings)
        - is_safe: True 表示通过校验
        - warnings: 警告信息列表（空列表表示通过）
    """
    if not meal_items:
        return True, []

    totals = {"calories": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0}
    for item in meal_items:
        totals["calories"] += float(item.get("calories", 0) or 0)
        totals["protein_g"] += float(item.get("protein", 0) or item.get("protein_g", 0) or 0)
        totals["fat_g"] += float(item.get("fat", 0) or item.get("fat_g", 0) or 0)
        totals["carbs_g"] += float(item.get("carbs", 0) or item.get("carbs_g", 0) or 0)

    warnings = []
    for key, limit in MEAL_SAFETY_LIMITS.items():
        actual = totals.get(key, 0.0)
        if actual > limit:
            warnings.append(
                f"单餐{key.replace('_g', '').replace('calories', '能量')} "
                f"{actual:.1f} 超过安全上限 {limit:.1f}"
            )

    if warnings:
        logger.warning(f"单餐校验未通过: {warnings}")
    return len(warnings) == 0, warnings


def validate_daily_intake(daily_nutrition: Dict[str, float]) -> Tuple[bool, List[str]]:
    """
    校验每日总摄入是否合理

    用于 plan_service 生成计划后做最终合理性检查。

    Args:
        daily_nutrition: 每日营养素汇总
            例: {"calories": 2000, "protein_g": 80, "fat_g": 60, "carbs_g": 250, "sugar_g": 30}

    Returns:
        (is_safe, warnings)
    """
    warnings = []
    for key, limit in DAILY_SAFETY_LIMITS.items():
        actual = float(daily_nutrition.get(key, 0) or 0)
        if actual > limit:
            warnings.append(
                f"每日{key.replace('_g', '').replace('calories', '总能量').replace('_mg', '')} "
                f"{actual:.1f} 超过安全上限 {limit:.1f}"
            )

    if warnings:
        logger.warning(f"每日摄入校验未通过: {warnings}")
    return len(warnings) == 0, warnings


# ============================================================
# 3. 饮食禁忌检查（基于用户档案）
# ============================================================

# 过敏原 → 触发食物关键词
ALLERGEN_MAP: Dict[str, List[str]] = {
    "milk": ["牛奶", "奶酪", "酸奶", "奶油", "黄油", "乳糖", "奶粉"],
    "eggs": ["鸡蛋", "鸭蛋", "鹅蛋", "蛋白", "蛋黄", "蛋糕"],
    "peanut": ["花生", "花生油", "花生酱"],
    "nuts": ["核桃", "杏仁", "腰果", "榛子", "巴旦木", "夏威夷果"],
    "seafood": ["虾", "蟹", "龙虾", "贝", "蛤", "蚝", "海鱼", "三文鱼", "金枪鱼"],
    "gluten": ["小麦", "面粉", "面条", "馒头", "面包", "饼干"],
    "soy": ["大豆", "黄豆", "豆腐", "豆浆", "豆干"],
}

# 疾病 → 需限制的食物类别
DISEASE_DIET_RESTRICTIONS: Dict[str, Dict[str, Any]] = {
    "diabetes": {
        "forbidden": ["白糖", "红糖", "蜂蜜", "糖果", "甜饮料", "蛋糕", "冰淇淋"],
        "limit": ["西瓜", "葡萄", "荔枝", "桂圆", "香蕉", "白米饭", "白面包"],
        "reason": "高 GI / 高糖食物会导致血糖快速升高",
    },
    "hypertension": {
        "forbidden": ["腌制食品", "腊肉", "咸菜", "方便面", "罐头", "酱油（过量）"],
        "limit": ["动物内脏", "蛋黄", "肥肉"],
        "reason": "高钠食物会升高血压",
    },
    "hyperlipidemia": {
        "forbidden": ["肥肉", "动物内脏", "油炸食品", "黄油", "椰子油"],
        "limit": ["蛋黄", "蟹黄", "虾膏"],
        "reason": "高胆固醇/饱和脂肪会加重血脂异常",
    },
    "fatty_liver": {
        "forbidden": ["酒精", "肥肉", "油炸食品", "含糖饮料"],
        "limit": ["精制米面", "果糖"],
        "reason": "酒精与果糖会加重肝脏脂肪沉积",
    },
    "gout": {
        "forbidden": ["动物内脏", "浓肉汤", "啤酒", "海鲜（部分）", "凤尾鱼", "沙丁鱼"],
        "limit": ["豆制品", "蘑菇", "菠菜"],
        "reason": "高嘌呤食物会诱发痛风发作",
    },
}


def check_allergens(foods: List[str], user_allergens: List[str]) -> List[Dict[str, str]]:
    """
    检查食物列表是否包含用户过敏原

    Args:
        foods: 食物名列表
        user_allergens: 用户过敏原列表，如 ["milk", "eggs"]

    Returns:
        命中的过敏风险列表，每项含 allergen / food / message
    """
    risks = []
    for allergen in user_allergens:
        triggers = ALLERGEN_MAP.get(allergen, [])
        for food in foods:
            normalized = normalize_food_name(food)
            for trigger in triggers:
                if trigger in normalized or trigger in food:
                    risks.append({
                        "allergen": allergen,
                        "food": food,
                        "message": f"食物「{food}」可能含过敏原 {allergen}，请避免食用",
                    })
    if risks:
        logger.warning(f"过敏原检查命中 {len(risks)} 项: {risks}")
    return risks


def check_disease_restrictions(
    foods: List[str], user_diseases: List[str]
) -> List[Dict[str, str]]:
    """
    检查食物列表是否触发疾病饮食禁忌

    Args:
        foods: 食物名列表
        user_diseases: 用户疾病列表，如 ["diabetes", "hypertension"]

    Returns:
        命中的禁忌列表，每项含 disease / food / level / message
    """
    risks = []
    for disease in user_diseases:
        restriction = DISEASE_DIET_RESTRICTIONS.get(disease)
        if not restriction:
            continue
        for food in foods:
            normalized = normalize_food_name(food)
            for forbidden in restriction.get("forbidden", []):
                if forbidden in normalized or forbidden in food:
                    risks.append({
                        "disease": disease,
                        "food": food,
                        "level": "forbidden",
                        "message": f"疾病 {disease} 禁忌「{food}」: {restriction['reason']}",
                    })
            for limit in restriction.get("limit", []):
                if limit in normalized or limit in food:
                    risks.append({
                        "disease": disease,
                        "food": food,
                        "level": "limit",
                        "message": f"疾病 {disease} 需限制「{food}」: {restriction['reason']}",
                    })
    if risks:
        logger.warning(f"疾病禁忌检查命中 {len(risks)} 项")
    return risks


# ============================================================
# 4. 食物-药物相互作用
# ============================================================
# 药物 → 需避免的食物
FOOD_DRUG_INTERACTIONS: Dict[str, Dict[str, Any]] = {
    "warfarin": {
        "avoid": ["菠菜", "西兰花", "动物肝脏", "绿茶", "纳豆"],
        "reason": "富维 K 食物会拮抗华法林抗凝作用",
    },
    "statins": {
        "avoid": ["葡萄柚", "西柚汁"],
        "reason": "葡萄柚汁会抑制他汀类药物代谢，增加横纹肌溶解风险",
    },
    "maoi": {
        "avoid": ["奶酪", "腌鱼", "红酒", "啤酒", "鸡肝"],
        "reason": "富含酪胺食物与 MAOI 合用会引发高血压危象",
    },
    "metformin": {
        "avoid": ["酒精"],
        "reason": "酒精会加重二甲双胍的乳酸酸中毒风险",
    },
}


def check_drug_interactions(
    foods: List[str], user_medications: List[str]
) -> List[Dict[str, str]]:
    """
    检查食物-药物相互作用

    Args:
        foods: 食物名列表
        user_medications: 用户正在使用的药物列表（通用名或商品名）

    Returns:
        命中的相互作用列表
    """
    risks = []
    for med in user_medications:
        interaction = FOOD_DRUG_INTERACTIONS.get(med.lower())
        if not interaction:
            continue
        for food in foods:
            normalized = normalize_food_name(food)
            for avoid in interaction.get("avoid", []):
                if avoid in normalized or avoid in food:
                    risks.append({
                        "medication": med,
                        "food": food,
                        "message": f"药物 {med} 期间应避免「{food}」: {interaction['reason']}",
                    })
    if risks:
        logger.warning(f"食物-药物相互作用检查命中 {len(risks)} 项")
    return risks


# ============================================================
# 5. 综合校验入口
# ============================================================

def validate_food_for_user(
    foods: List[str],
    user_profile: Dict[str, Any],
) -> Dict[str, Any]:
    """
    综合校验：检查食物列表对当前用户是否安全

    汇总：过敏原 + 疾病禁忌 + 药物相互作用

    Args:
        foods: 食物名列表
        user_profile: 用户档案，含以下字段（均可选）：
            - allergens: List[str] 过敏原
            - diseases: List[str] 疾病
            - medications: List[str] 在用药物

    Returns:
        {
            "is_safe": bool,
            "allergen_risks": [...],
            "disease_risks": [...],
            "drug_interaction_risks": [...],
            "all_risks": [...],
        }
    """
    allergens = user_profile.get("allergens", []) or []
    diseases = user_profile.get("diseases", []) or []
    medications = user_profile.get("medications", []) or []

    allergen_risks = check_allergens(foods, allergens)
    disease_risks = check_disease_restrictions(foods, diseases)
    drug_risks = check_drug_interactions(foods, medications)

    all_risks = allergen_risks + disease_risks + drug_risks
    # forbidden 级别直接判不安全；limit 级别仍可食用但需警告
    forbidden_count = sum(1 for r in all_risks if r.get("level") == "forbidden")
    is_safe = len(allergen_risks) == 0 and forbidden_count == 0 and len(drug_risks) == 0

    return {
        "is_safe": is_safe,
        "allergen_risks": allergen_risks,
        "disease_risks": disease_risks,
        "drug_interaction_risks": drug_risks,
        "all_risks": all_risks,
    }
