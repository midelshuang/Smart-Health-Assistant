"""
用户档案与 TDEE 计算 API 路由

提供：
1. GET  /user/profile         —— 获取当前用户档案
2. PUT  /user/profile         —— 更新用户档案（身高/体重/年龄/性别/活动等级等）
3. POST /user/tdee            —— 基于档案计算 TDEE 与宏量营养素分配
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

from src.repositories.session import get_db
from src.models.db_models import UserProfile, User
from src.services.auth_service import get_current_user_id, get_current_user_optional, oauth2_scheme, is_guest, require_role
from src.utils.logger import setup_logger

logger = setup_logger(name="User_Router")

router = APIRouter(prefix="/user", tags=["用户档案与营养目标"])


# ============================================================
# 数据模型
# ============================================================

class UserProfileUpdate(BaseModel):
    """用户档案更新请求体"""
    nickname: Optional[str] = None
    age: Optional[int] = Field(default=None, ge=1, le=150)
    gender: Optional[str] = Field(default=None, description="male/female/男/女")
    height: Optional[float] = Field(default=None, gt=0, le=300, description="身高(cm)")
    weight: Optional[float] = Field(default=None, gt=0, le=500, description="体重(kg)")
    body_fat_percentage: Optional[float] = Field(default=None, ge=0, le=100, description="体脂率(%)")
    province: Optional[str] = None
    sports_hobby: Optional[str] = None
    health_goal: Optional[str] = None
    health_tags: Optional[list] = None
    allergies: Optional[str] = None


class TDEERequest(BaseModel):
    """TDEE 计算请求体（可选传入，否则从档案读取）"""
    weight_kg: Optional[float] = Field(default=None, gt=0, description="体重(kg)")
    height_cm: Optional[float] = Field(default=None, gt=0, description="身高(cm)")
    age: Optional[int] = Field(default=None, ge=1, le=150)
    gender: Optional[str] = None
    activity_level: Optional[str] = Field(default=None, description="活动等级（中文/英文均可）")
    goal_type: Optional[str] = Field(default=None, description="目标类型")
    body_fat_percentage: Optional[float] = Field(default=None, ge=0, le=100)


# ============================================================
# 工具函数
# ============================================================

def _get_user_profile(db: Session, user_id: str) -> UserProfile:
    """根据 user_id 查询 UserProfile，不存在则 404"""
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="用户档案不存在，请先完善档案")
    return profile


def _get_or_create_profile(db: Session, user_id: str) -> UserProfile:
    """查询 UserProfile，不存在则自动创建空档案"""
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile:
        profile = UserProfile(user_id=user_id, nickname=user_id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
        logger.info(f"自动创建用户档案: {user_id}")
    return profile


# ============================================================
# 接口实现
# ============================================================

@router.get("/profile")
def get_profile(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """获取当前用户档案（不存在则自动创建空档案）"""
    profile = _get_or_create_profile(db, current_user_id)
    return _profile_to_dict(profile)


@router.put("/profile")
def update_profile(
    body: UserProfileUpdate,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """更新用户档案（部分更新，仅传入字段会被更新）"""
    profile = _get_or_create_profile(db, current_user_id)

    update_data = body.dict(exclude_none=True)
    for key, value in update_data.items():
        setattr(profile, key, value)

    # 自动重算 BMI
    if body.weight is not None and body.height is not None:
        try:
            profile.bmi = round(float(body.weight) / ((float(body.height) / 100) ** 2), 1)
        except Exception:
            pass

    profile.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(profile)

    logger.info(f"用户 {current_user_id} 更新档案: {list(update_data.keys())}")
    return _profile_to_dict(profile)


@router.post("/tdee")
def calculate_tdee(
    body: TDEERequest,
    current_user=Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    计算 TDEE 与宏量营养素分配

    - 游客：必须在请求体中提供身高、体重、年龄、性别等参数
    - 登录用户：请求体参数优先，缺失字段从档案自动读取
    - 自动应用医学安全底线（男性 ≥ 1500 kcal，女性 ≥ 1200 kcal）
    """
    from src.utils.nutrition_calc import calculate_tdee as _calc_tdee, get_calorie_advice

    guest_mode = is_guest(current_user)

    if guest_mode:
        if not body.weight_kg or not body.height_cm:
            raise HTTPException(
                status_code=400,
                detail="游客模式需在请求体中提供 weight_kg 和 height_cm（如登录可从档案读取）",
            )
        weight = body.weight_kg
        height = body.height_cm
        age = body.age or 25
        gender = body.gender or "male"
        activity = body.activity_level or "moderate"
        goal = body.goal_type or "maintenance"
        body_fat = body.body_fat_percentage
    else:
        profile = _get_or_create_profile(db, current_user.external_id)
        weight = body.weight_kg or float(profile.weight or 0)
        height = body.height_cm or float(profile.height or 0)
        age = body.age or profile.age or 25
        gender = body.gender or profile.gender or "male"
        activity = body.activity_level or "moderate"
        goal = body.goal_type or profile.health_goal or "maintenance"
        body_fat = body.body_fat_percentage
        if body_fat is None and profile.body_fat_percentage is not None:
            body_fat = float(profile.body_fat_percentage)

    if weight <= 0 or height <= 0:
        raise HTTPException(
            status_code=400,
            detail="身高/体重缺失，请填写完整信息",
        )

    result = _calc_tdee(
        weight_kg=weight,
        height_cm=height,
        age=age,
        gender=gender,
        activity_level=activity,
        goal_type=goal,
        body_fat_percentage=body_fat,
    )

    result["advice"] = get_calorie_advice(result["target_calories"], goal)
    result["guest_mode"] = guest_mode
    result["profile_source"] = {
        "weight_kg": weight,
        "height_cm": height,
        "age": age,
        "gender": result["gender"],
    }

    return result


def _profile_to_dict(p: UserProfile) -> dict:
    """UserProfile ORM 转 dict"""
    return {
        "user_id": p.user_id,
        "nickname": p.nickname,
        "age": p.age,
        "gender": p.gender,
        "height": float(p.height) if p.height else None,
        "weight": float(p.weight) if p.weight else None,
        "bmi": float(p.bmi) if p.bmi else None,
        "body_fat_percentage": float(p.body_fat_percentage) if p.body_fat_percentage else None,
        "province": p.province,
        "sports_hobby": p.sports_hobby,
        "health_goal": p.health_goal,
        "health_tags": p.health_tags,
        "allergies": p.allergies,
        "updated_at": str(p.updated_at) if p.updated_at else None,
    }


@router.get("/dashboard/stats")
def admin_dashboard(
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    """管理员仪表盘：系统统计数据"""
    user_count = db.query(User).filter(User.is_active == True).count()
    profile_count = db.query(UserProfile).count()

    from src.models.db_models import Plan
    plan_count = db.query(Plan).count() if _table_exists(db, "plans") else 0

    return {
        "total_users": user_count,
        "total_profiles": profile_count,
        "total_plans": plan_count,
        "server_time": datetime.utcnow().isoformat(),
    }


def _table_exists(db: Session, table_name: str) -> bool:
    from sqlalchemy import inspect
    inspector = inspect(db.bind)
    return table_name in inspector.get_table_names()
