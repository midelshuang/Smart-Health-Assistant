from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from src.repositories.session import get_db
# ✅ 修正导入：函数名是 log_diet_entry，不是 log_diet_entry_by_id
from src.services.diet_service import search_foods, log_diet_entry, get_diet_log_detail, delete_diet_entry
from src.services.auth_service import get_current_user_id, oauth2_scheme
from src.models.db_models import DietLogCreate
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date as date_type

router = APIRouter(prefix="/diet", tags=["饮食记录"])


# ============================================================
# 自然语言饮食记录请求体
# ============================================================

class DietParseRequest(BaseModel):
    """自然语言饮食记录请求体"""
    text: str = Field(..., description="用户的自然语言饮食描述", min_length=1, max_length=1000)
    auto_create: bool = Field(default=True, description="精确命中食物时是否自动写入数据库")

# ==========================================
# 🆕 新增：搜索食物接口 (GET)
# ==========================================
@router.get("/search")
def search_food(
        # ✅ 修改点1: 使用 alias 允许接收带空格的参数名
        # ✅ 修改点2: 设置默认值为 None，并允许为 None
        keyword: str | None = Query(default=None, alias="keyword", description="搜索关键词"),
        limit: int = Query(default=10, description="返回数量限制"),
        db: Session = Depends(get_db),
        current_user_id: str = Depends(get_current_user_id)
):
    # ✅ 修改点3: 增加防御性判断
    if not keyword:
        return {"error": "请输入搜索关键词"}

    return search_foods(db=db, keyword=keyword, limit=limit)

# ==========================================
# 🔄 修改：记录饮食接口 (POST)
# ==========================================
@router.post("/record")
def create_diet_log(
    record: DietLogCreate,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """
    根据用户选择的 nutrition_id 创建饮食记录
    """
    # ✅ 修正：调用 log_diet_entry
    return log_diet_entry(
        db=db,
        user_id=current_user_id,
        nutrition_id=record.nutrition_id,
        quantity_grams=record.quantity_grams,
        meal_type=record.meal_type
    )

# ==========================================
# 📌 原有功能保持不变 (修复参数传递)
# ==========================================
@router.get("/{log_id}")
def get_log(
    log_id: int,
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    # ✅ 修正：显式传递 obj_id=current_user_id
    return get_diet_log_detail(
        db=db,
        user_id=current_user_id, # 对应装饰器需要的 user_id
        obj_id=log_id            # 对应装饰器需要的 obj_id
    )

@router.delete("/{log_id}")
def delete_log(
    log_id: int,
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    # ✅ 修正：显式传递 obj_id=current_user_id
    return delete_diet_entry(
        db=db,
        user_id=current_user_id,
        obj_id=log_id
    )


# ==========================================
# 🆕 自然语言饮食记录接口（Function Calling）
# ==========================================

@router.post("/parse")
def parse_diet_text(
    body: DietParseRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    自然语言饮食记录

    通过 LLM Function Calling 解析用户描述，自动匹配食物数据库并写入饮食记录。

    **示例请求：**
    ```
    {"text": "我今天中午吃了一碗米饭、两个鸡蛋和一杯牛奶"}
    ```

    **返回结构：**
    - `logged`: 已自动写入的记录（精确命中）
    - `candidates`: 多个匹配项，需用户在前端选择 nutrition_id 后调用 /diet/record 写入
    - `unmatched`: 数据库未收录的食物
    - `total_calories`: 本次记录的总热量
    """
    from src.services.diet_service import parse_and_log_diet
    return parse_and_log_diet(
        db=db,
        user_id=current_user_id,
        text=body.text,
        auto_create=body.auto_create,
    )


# ==========================================
# 🆕 每日饮食汇总查询接口
# ==========================================

@router.get("/summary")
def get_daily_summary(
    log_date: Optional[date_type] = Query(default=None, description="查询日期，默认今天，格式 YYYY-MM-DD"),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    查询用户某天的饮食汇总（按餐次分组 + 总热量/营养）

    用于前端饮食看板展示。
    """
    from src.services.diet_service import get_daily_nutrition_summary
    return get_daily_nutrition_summary(db=db, user_id=current_user_id, log_date=log_date)