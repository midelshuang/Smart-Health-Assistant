from sqlalchemy.orm import Session
from src.models.db_models import DailyDietLog, NutritionFact
from src.utils.logger import setup_logger
from decimal import Decimal
from src.services.base_service import require_ownership

logger = setup_logger(name="DietService")


# ==========================================
# 🆕 新增：搜索食物接口
# ==========================================
def search_foods(
        db: Session,
        keyword: str,
        limit: int = 10
) -> dict:
    """
    根据关键词搜索食物，返回前 limit 条结果供用户选择
    """
    if not keyword:
        return {"error": "请输入搜索关键词"}

    # 模糊搜索：名字包含关键词
    results = db.query(NutritionFact).filter(
        NutritionFact.food_name.contains(keyword)
    ).limit(limit).all()

    if not results:
        return {
            "message": f"未找到包含 '{keyword}' 的食物",
            "data": []
        }

    # 构建返回给前端的数据列表
    food_list = []
    for item in results:
        # 安全处理可能为 None 的数值
        cals = float(item.calories_per_100g or 0)
        pro = float(item.protein or 0)
        fat = float(item.fat or 0)
        carb = float(item.carbs or 0)

        food_list.append({
            "id": item.id,
            "name": item.food_name,
            "calories_per_100g": round(cals, 1),
            "protein_per_100g": round(pro, 1),
            "fat_per_100g": round(fat, 1),
            "carbs_per_100g": round(carb, 1),
            "category": item.category
        })

    return {
        "message": "搜索成功",
        "count": len(food_list),
        "data": food_list
    }


# ==========================================
# 🔄 修改：记录饮食接口
# ==========================================
def log_diet_entry(
        db: Session,
        user_id: str,
        nutrition_id: int,
        quantity_grams: float,
        meal_type: str = "snack"
) -> dict:
    # 1. 翻译餐别
    MEAL_TYPE_MAP = {
        "早餐": "breakfast", "午饭": "lunch", "晚餐": "dinner", "加餐": "snack",
        "breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner", "snack": "snack"
    }
    standard_meal_type = MEAL_TYPE_MAP.get(meal_type, "snack")

    # 2. 根据 ID 精准查找食物
    food_item = db.query(NutritionFact).filter(NutritionFact.id == nutrition_id).first()

    if not food_item:
        return {"error": f"未找到 ID 为 {nutrition_id} 的食物，请重新搜索"}

    # 3. 计算营养 (使用 Decimal 保证精度)
    weight_ratio = Decimal(str(quantity_grams)) / Decimal("100")

    # 获取基础数值，处理 None
    base_cal = Decimal(str(food_item.calories_per_100g or 0))
    base_pro = Decimal(str(food_item.protein or 0))
    base_fat = Decimal(str(food_item.fat or 0))
    base_carb = Decimal(str(food_item.carbs or 0))

    # 计算实际摄入
    calc_cal = float(base_cal * weight_ratio)
    calc_pro = float(base_pro * weight_ratio)
    calc_fat = float(base_fat * weight_ratio)
    calc_carb = float(base_carb * weight_ratio)

    # 4. 写入数据库
    new_log = DailyDietLog(
        user_id=user_id,
        meal_type=standard_meal_type,
        nutrition_id=nutrition_id,
        quantity_grams=Decimal(str(quantity_grams)),
        calculated_calories=calc_cal,
    )

    db.add(new_log)
    db.commit()
    db.refresh(new_log)

    return {
        "message": "记录成功",
        "log_id": new_log.id,
        "food_name": food_item.food_name,
        "calories": round(calc_cal, 1),
        "protein": round(calc_pro, 1),
        "fat": round(calc_fat, 1),
        "carbs": round(calc_carb, 1)
    }


# ========== 查询与删除 ==========
# ✅ 修正：参数名必须是 user_id 和 obj_id，以匹配装饰器 @require_ownership(DailyDietLog, "user_id")
@require_ownership(DailyDietLog, "user_id")
def get_diet_log_detail(
        db: Session,
        user_id: str,  # ✅ 装饰器会注入当前用户ID
        obj_id: int,  # ✅ 装饰器会注入 URL 中的 log_id
        verified_obj: DailyDietLog,  # ✅ 装饰器会注入查询到的对象
        **kwargs
) -> dict:
    food_name = "未知食物"
    if verified_obj.nutrition_info:
        food_name = verified_obj.nutrition_info.food_name

    return {
        "log_id": verified_obj.id,
        "food_name": food_name,
        "calories": float(verified_obj.calculated_calories),
        "meal_type": verified_obj.meal_type,
        "quantity_grams": float(verified_obj.quantity_grams)
    }


# ✅ 修正：参数名必须是 user_id 和 obj_id
@require_ownership(DailyDietLog, "user_id")
def delete_diet_entry(
        db: Session,
        user_id: str,
        obj_id: int,
        verified_obj: DailyDietLog,
        **kwargs
) -> dict:
    db.delete(verified_obj)
    db.commit()
    return {"message": "删除成功"}


# ==========================================
# 🆕 自然语言饮食记录（基于 Function Calling）
# ==========================================

# 餐次中文映射
MEAL_TYPE_CN = {
    "breakfast": "早餐",
    "lunch": "午餐",
    "dinner": "晚餐",
    "snack": "加餐",
}


def parse_and_log_diet(
        db: Session,
        user_id: str,
        text: str,
        auto_create: bool = True,
) -> dict:
    """
    自然语言饮食记录主入口

    流程：
    1. 调用 LLM Function Calling 解析用户描述，提取食物列表
    2. 对每个食物去 nutrition_facts 表查询匹配
       - 精确命中：自动写入 daily_diet_logs
       - 模糊命中：返回候选列表让用户选择
       - 未命中：返回食物名让用户补充
    3. 返回完整结构化结果

    Args:
        db: 数据库会话
        user_id: 当前用户 ID
        text: 用户的自然语言饮食描述
        auto_create: True=精确命中时自动创建记录；False=仅解析不写入

    Returns:
        dict: {
            "summary": str,
            "success": bool,
            "total_items": int,
            "logged": [...],          # 已成功写入的记录
            "candidates": [...],      # 模糊匹配候选，需用户确认
            "unmatched": [...],        # 完全未命中的食物
            "total_calories": float,
            "total_protein": float,
            "total_fat": float,
            "total_carbs": float,
            "error": Optional[str],
        }
    """
    from src.services.food_parser import parse_food_intake, match_food_in_db

    # 1. LLM 解析
    parse_result = parse_food_intake(text)

    if not parse_result.get("success"):
        return {
            "summary": "",
            "success": False,
            "total_items": 0,
            "logged": [],
            "candidates": [],
            "unmatched": [],
            "total_calories": 0,
            "total_protein": 0,
            "total_fat": 0,
            "total_carbs": 0,
            "error": parse_result.get("error", "解析失败"),
        }

    items = parse_result["items"]
    summary = parse_result.get("summary", "")

    logged = []
    candidates = []
    unmatched = []
    total_cal = 0.0
    total_pro = 0.0
    total_fat = 0.0
    total_carb = 0.0

    # 2. 逐项匹配并入库
    for it in items:
        food_name = it["food_name"]
        grams = it["estimated_grams"]
        meal_type = it["meal_type"]

        matches = match_food_in_db(db, food_name, limit=3)

        if not matches:
            # 完全未命中
            unmatched.append({
                "food_name": food_name,
                "quantity": it["quantity"],
                "unit": it["unit"],
                "estimated_grams": grams,
                "meal_type": meal_type,
                "meal_type_cn": MEAL_TYPE_CN.get(meal_type, "加餐"),
            })
            continue

        if len(matches) == 1:
            # 唯一命中：自动入库
            food = matches[0]
            log_entry = _create_diet_log_internal(
                db=db,
                user_id=user_id,
                nutrition_id=food["id"],
                quantity_grams=grams,
                meal_type=meal_type,
                food_name=food["food_name"],
                calories_per_100g=food["calories_per_100g"],
                protein=food["protein"],
                fat=food["fat"],
                carbs=food["carbs"],
            )
            logged.append(log_entry)
            total_cal += log_entry["calories"]
            total_pro += log_entry["protein"]
            total_fat += log_entry["fat"]
            total_carb += log_entry["carbs"]
        else:
            # 多个候选：返回给用户选择
            candidates.append({
                "food_name": food_name,
                "quantity": it["quantity"],
                "unit": it["unit"],
                "estimated_grams": grams,
                "meal_type": meal_type,
                "meal_type_cn": MEAL_TYPE_CN.get(meal_type, "加餐"),
                "candidates": matches,
            })

    logger.info(
        f"饮食记录完成: 已写入 {len(logged)} 项, 候选 {len(candidates)} 项, "
        f"未匹配 {len(unmatched)} 项, 总热量 {total_cal:.1f} kcal"
    )

    return {
        "summary": summary,
        "success": True,
        "total_items": len(items),
        "logged": logged,
        "candidates": candidates,
        "unmatched": unmatched,
        "total_calories": round(total_cal, 1),
        "total_protein": round(total_pro, 1),
        "total_fat": round(total_fat, 1),
        "total_carbs": round(total_carb, 1),
        "error": None,
    }


def _create_diet_log_internal(
        db: Session,
        user_id: str,
        nutrition_id: int,
        quantity_grams: float,
        meal_type: str,
        food_name: str,
        calories_per_100g: float,
        protein: float,
        fat: float,
        carbs: float,
) -> dict:
    """
    内部方法：创建单条饮食记录并返回结构化结果

    供 parse_and_log_diet 批量调用，已在外层校验。
    """
    # 餐次标准化
    standard_meal = MEAL_TYPE_MAP.get(meal_type, meal_type)
    if standard_meal not in ("breakfast", "lunch", "dinner", "snack"):
        standard_meal = "snack"

    weight_ratio = Decimal(str(quantity_grams)) / Decimal("100")

    calc_cal = float(Decimal(str(calories_per_100g)) * weight_ratio)
    calc_pro = float(Decimal(str(protein)) * weight_ratio)
    calc_fat = float(Decimal(str(fat)) * weight_ratio)
    calc_carb = float(Decimal(str(carbs)) * weight_ratio)

    new_log = DailyDietLog(
        user_id=user_id,
        meal_type=standard_meal,
        nutrition_id=nutrition_id,
        quantity_grams=Decimal(str(quantity_grams)),
        calculated_calories=calc_cal,
        ai_parsed=True,
    )
    db.add(new_log)
    db.commit()
    db.refresh(new_log)

    return {
        "log_id": new_log.id,
        "food_name": food_name,
        "quantity_grams": round(quantity_grams, 1),
        "meal_type": standard_meal,
        "meal_type_cn": MEAL_TYPE_CN.get(standard_meal, "加餐"),
        "calories": round(calc_cal, 1),
        "protein": round(calc_pro, 1),
        "fat": round(calc_fat, 1),
        "carbs": round(calc_carb, 1),
    }


def get_daily_nutrition_summary(
        db: Session,
        user_id: str,
        log_date=None,
) -> dict:
    """
    查询用户某天的饮食汇总（按餐次分组 + 总营养）

    Args:
        db: 数据库会话
        user_id: 用户 ID
        log_date: 日期，默认今天

    Returns:
        dict: 按餐次分组的热量/营养汇总
    """
    from datetime import date
    if log_date is None:
        log_date = date.today()

    logs = db.query(DailyDietLog).filter(
        DailyDietLog.user_id == user_id,
        DailyDietLog.log_date == log_date,
    ).all()

    by_meal = {"breakfast": [], "lunch": [], "dinner": [], "snack": []}
    total_cal = 0.0
    total_pro = 0.0
    total_fat = 0.0
    total_carb = 0.0

    for log in logs:
        food_name = log.nutrition_info.food_name if log.nutrition_info else "未知"
        cal = float(log.calculated_calories or 0)
        grams = float(log.quantity_grams or 0)

        # 从 nutrition_info 拿营养比例，反推蛋白/脂肪/碳水
        if log.nutrition_info:
            ratio = Decimal(str(grams)) / Decimal("100")
            pro = float(Decimal(str(log.nutrition_info.protein or 0)) * ratio)
            fat = float(Decimal(str(log.nutrition_info.fat or 0)) * ratio)
            carb = float(Decimal(str(log.nutrition_info.carbs or 0)) * ratio)
        else:
            pro = fat = carb = 0.0

        entry = {
            "log_id": log.id,
            "food_name": food_name,
            "quantity_grams": round(grams, 1),
            "calories": round(cal, 1),
            "protein": round(pro, 1),
            "fat": round(fat, 1),
            "carbs": round(carb, 1),
        }

        meal_key = log.meal_type if log.meal_type in by_meal else "snack"
        by_meal[meal_key].append(entry)

        total_cal += cal
        total_pro += pro
        total_fat += fat
        total_carb += carb

    return {
        "date": str(log_date),
        "by_meal": {
            "breakfast": {"items": by_meal["breakfast"], "calories": round(sum(x["calories"] for x in by_meal["breakfast"]), 1)},
            "lunch":     {"items": by_meal["lunch"],     "calories": round(sum(x["calories"] for x in by_meal["lunch"]), 1)},
            "dinner":    {"items": by_meal["dinner"],    "calories": round(sum(x["calories"] for x in by_meal["dinner"]), 1)},
            "snack":     {"items": by_meal["snack"],     "calories": round(sum(x["calories"] for x in by_meal["snack"]), 1)},
        },
        "total_calories": round(total_cal, 1),
        "total_protein": round(total_pro, 1),
        "total_fat": round(total_fat, 1),
        "total_carbs": round(total_carb, 1),
        "item_count": len(logs),
    }