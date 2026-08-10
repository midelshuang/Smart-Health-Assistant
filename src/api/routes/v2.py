"""
V2 路由聚合模块

为 V4 预留的新版 API 路由占位：
- 深度个性化推荐（基于 dietary_preference / medical_conditions）
- 多模态对话（基于 attachments / meal_photo_url）
- 计划版本链查询（基于 plan_version / parent_plan_id）
- 用户代谢数据动态更新

注意：当前仅为占位实现，返回 501 Not Implemented，供 V4 正式启用时填充实现。
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/recommendations/personalized", summary="[V4] 深度个性化推荐")
async def personalized_recommendations():
    """基于用户档案的深度个性化推荐（饮食/运动/营养补充）"""
    return {
        "status": "not_implemented",
        "message": "V2 接口尚未启用，当前为 V4 预留占位",
        "expected_in": "V4.0.0",
        "required_fields": [
            "user_profiles.dietary_preference",
            "user_profiles.metabolic_data",
            "user_profiles.medical_conditions",
        ],
    }


@router.post("/chat/multimodal", summary="[V4] 多模态对话")
async def multimodal_chat():
    """支持图片/语音附件的多模态对话"""
    return {
        "status": "not_implemented",
        "message": "多模态对话接口尚未启用",
        "expected_in": "V4.0.0",
        "required_fields": ["chat_histories.attachments"],
    }


@router.get("/plans/{plan_id}/versions", summary="[V4] 计划版本链查询")
async def plan_version_chain(plan_id: int):
    """查询计划的完整版本历史链"""
    return {
        "status": "not_implemented",
        "message": "计划版本链接口尚未启用",
        "expected_in": "V4.0.0",
        "required_fields": ["plans.plan_version", "plans.parent_plan_id"],
        "plan_id": plan_id,
    }


@router.put("/users/{user_id}/metabolic-data", summary="[V4] 更新用户代谢数据")
async def update_metabolic_data(user_id: str):
    """更新用户代谢数据（基础代谢率/活动系数）"""
    return {
        "status": "not_implemented",
        "message": "代谢数据更新接口尚未启用",
        "expected_in": "V4.0.0",
        "required_fields": ["user_profiles.metabolic_data"],
        "user_id": user_id,
    }


@router.get("/diet-logs/{log_id}/nutrition-analysis", summary="[V4] 饮食精细化分析")
async def nutrition_analysis(log_id: int):
    """获取饮食记录的精细化营养分析（含实际宏量营养素）"""
    return {
        "status": "not_implemented",
        "message": "精细化营养分析接口尚未启用",
        "expected_in": "V4.0.0",
        "required_fields": ["daily_diet_logs.actual_macros", "daily_diet_logs.confidence_score"],
        "log_id": log_id,
    }
