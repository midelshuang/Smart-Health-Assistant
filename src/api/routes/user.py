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
    metabolic_data: Optional[dict] = Field(default=None, description="TDEE等代谢数据")


class TDEERequest(BaseModel):
    """TDEE 计算请求体（可选传入，否则从档案读取）"""
    weight_kg: Optional[float] = Field(default=None, gt=0, description="体重(kg)")
    height_cm: Optional[float] = Field(default=None, gt=0, description="身高(cm)")
    age: Optional[int] = Field(default=None, ge=1, le=150)
    gender: Optional[str] = None
    activity_level: Optional[str] = Field(default=None, description="活动等级（中文/英文均可）")
    goal_type: Optional[str] = Field(default=None, description="目标类型")
    body_fat_percentage: Optional[float] = Field(default=None, ge=0, le=100)
    save_to_profile: Optional[bool] = Field(default=None, description="是否保存到档案（None=由前端决定，True=强制保存，False=不保存）")


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

    # 保存 TDEE 到档案（仅登录用户）
    if not guest_mode and body.save_to_profile is not False:
        try:
            profile = _get_or_create_profile(db, current_user.external_id)
            existing_tdee = (profile.metabolic_data or {}).get("tdee_result")
            if body.save_to_profile is True:
                # 强制保存
                should_save = True
            elif existing_tdee is None:
                # 首次：自动保存，不询问
                should_save = True
            else:
                # 已有TDEE：不自动保存，前端询问后决定
                should_save = False

            if should_save:
                tdee_snapshot = {
                    "bmr": result.get("bmr"),
                    "tdee": result.get("tdee"),
                    "target_calories": result.get("target_calories"),
                    "macros": result.get("macros"),
                    "macros_ratios": result.get("macros_ratios"),
                    "activity_factor": result.get("activity_factor"),
                    "activity_level": result.get("activity_level"),
                    "goal_type": result.get("goal_type"),
                    "bmi": result.get("bmi"),
                    "safety_warning": result.get("safety_warning"),
                    "saved_at": datetime.utcnow().isoformat(),
                }
                if profile.metabolic_data:
                    profile.metabolic_data["tdee_result"] = tdee_snapshot
                else:
                    profile.metabolic_data = {"tdee_result": tdee_snapshot}
                db.commit()
                result["tdee_saved"] = True
                result["tdee_is_first_save"] = (existing_tdee is None)
            else:
                result["tdee_saved"] = False
                result["tdee_is_first_save"] = False
                result["tdee_existing"] = existing_tdee
        except Exception as e:
            logger.warning(f"TDEE 保存到档案失败: {e}")
            result["tdee_saved"] = False
    else:
        result["tdee_saved"] = False

    return result


@router.get("/tdee/saved")
def get_saved_tdee(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """获取已保存的 TDEE 数据"""
    profile = _get_or_create_profile(db, current_user_id)
    metabolic = profile.metabolic_data or {}
    tdee_result = metabolic.get("tdee_result")
    return {
        "has_saved_tdee": tdee_result is not None,
        "tdee_result": tdee_result,
    }


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
        "metabolic_data": p.metabolic_data,
        "updated_at": str(p.updated_at) if p.updated_at else None,
    }


@router.get("/dashboard/stats")
def admin_dashboard(
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    """管理员仪表盘：系统统计数据"""
    from sqlalchemy import func

    # 1. 用户统计
    total_users = db.query(User).filter(User.is_active == True).count()
    total_profiles = db.query(UserProfile).count()

    # 按角色统计
    role_stats = {}
    for role_name, role_label in [("user", "普通用户"), ("expert", "专家"), ("admin", "管理员")]:
        cnt = db.query(User).filter(User.role == role_name, User.is_active == True).count()
        role_stats[role_name] = {"count": cnt, "label": role_label}

    # 待审批专家
    pending_expert_count = 0
    if hasattr(User, "approved"):
        pending_expert_count = db.query(User).filter(
            User.role == "expert",
            User.approved == False,
        ).count()

    # 2. 饮食记录统计
    from src.models.db_models import DailyDietLog, NutritionFact, ChatHistory
    from sqlalchemy import cast, Date

    diet_logs_total = db.query(DailyDietLog).count()
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 今日记录数
    diet_logs_today = db.query(DailyDietLog).filter(
        cast(DailyDietLog.log_date, Date) == datetime.now().date()
    ).count()

    # 近7天每日记录数趋势
    from datetime import timedelta
    trend = []
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).date()
        cnt = db.query(DailyDietLog).filter(
            cast(DailyDietLog.log_date, Date) == day
        ).count()
        total_cal_q = db.query(func.sum(DailyDietLog.calculated_calories)).filter(
            cast(DailyDietLog.log_date, Date) == day
        ).scalar()
        trend.append({
            "date": str(day),
            "count": cnt,
            "total_calories": float(total_cal_q or 0),
        })

    # 3. 营养数据统计
    nutrition_facts_count = db.query(NutritionFact).count()

    # 4. 计划统计
    from src.models.db_models import Plan
    plan_count = db.query(Plan).count() if _table_exists(db, "plans") else 0

    # 5. 聊天记录统计
    chat_total = db.query(ChatHistory).count() if _table_exists(db, "chat_histories") else 0
    chat_today = 0
    if _table_exists(db, "chat_histories"):
        chat_today = db.query(ChatHistory).filter(
            cast(ChatHistory.created_at, Date) == datetime.now().date()
        ).count()

    # 6. 近24小时活跃用户
    last_24h_active = 0
    if hasattr(User, "last_login_at"):
        cutoff = datetime.utcnow() - timedelta(hours=24)
        last_24h_active = db.query(User).filter(User.last_login_at >= cutoff).count()

    return {
        "total_users": total_users,
        "total_profiles": total_profiles,
        "total_plans": plan_count,
        "role_stats": role_stats,
        "pending_expert_count": pending_expert_count,
        "diet_stats": {
            "total_logs": diet_logs_total,
            "today_logs": diet_logs_today,
            "last_7_days_trend": trend,
        },
        "nutrition_count": nutrition_facts_count,
        "chat_stats": {
            "total": chat_total,
            "today": chat_today,
        },
        "last_24h_active_users": last_24h_active,
        "server_time": datetime.utcnow().isoformat(),
    }


def _table_exists(db: Session, table_name: str) -> bool:
    from sqlalchemy import inspect
    inspector = inspect(db.bind)
    return table_name in inspector.get_table_names()


# ============================================================
# 专家审批 API（管理员）
# ============================================================

class ApproveExpertRequest(BaseModel):
    approved: bool = Field(..., description="True=通过审批, False=拒绝")


@router.get("/admin/pending-experts")
def list_pending_experts(
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    """获取待审批专家列表"""
    pending = db.query(User).filter(
        User.role == "expert",
        User.approved == False,
    ).order_by(User.created_at.desc()).all()

    data = []
    for u in pending:
        data.append({
            "id": u.id,
            "external_id": u.external_id,
            "username": u.username,
            "email": u.email,
            "source": u.source,
            "created_at": str(u.created_at) if u.created_at else None,
        })

    return {
        "count": len(data),
        "pending_experts": data,
    }


@router.get("/admin/users")
def list_all_users(
    role: Optional[str] = None,
    approved: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    """获取所有用户列表（支持筛选）"""
    query = db.query(User)
    if role:
        query = query.filter(User.role == role)
    if approved is not None:
        if hasattr(User, "approved"):
            query = query.filter(User.approved == approved)

    users = query.order_by(User.created_at.desc()).limit(200).all()

    data = []
    for u in users:
        data.append({
            "id": u.id,
            "external_id": u.external_id,
            "username": u.username,
            "email": u.email,
            "role": u.role,
            "approved": u.approved if hasattr(u, "approved") else True,
            "source": u.source,
            "is_active": u.is_active,
            "created_at": str(u.created_at) if u.created_at else None,
            "last_login_at": str(u.last_login_at) if u.last_login_at else None,
        })

    return {
        "count": len(data),
        "users": data,
    }


@router.put("/admin/users/{user_id}/approve")
def approve_expert(
    user_id: int,
    body: ApproveExpertRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    """审批专家账号（通过/拒绝）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.role != "expert":
        raise HTTPException(status_code=400, detail="仅专家账号需要审批")

    user.approved = body.approved
    db.commit()

    logger.info(
        f"管理员 {current_user.username} 审批专家: id={user_id}, "
        f"result={'通过' if body.approved else '拒绝'}"
    )

    return {
        "message": "审批成功" if body.approved else "已拒绝该专家账号",
        "user_id": user_id,
        "approved": body.approved,
    }
