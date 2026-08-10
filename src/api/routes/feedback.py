"""
用户反馈 API 路由

对外暴露 feedback_service 的 HTTP 接口，提供：
1. POST /feedback/answer         —— 对回答提交反馈（点赞/踩）
2. POST /feedback/plan           —— 对计划提交反馈（点赞/踩/使用计划）
3. DELETE /feedback              —— 撤销反馈
4. GET  /feedback/list            —— 查询用户反馈列表
5. GET  /feedback/preferences     —— 查询用户反馈偏好与风格指令
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

from src.repositories.session import get_db
from src.services.feedback_service import (
    submit_feedback,
    get_user_preferences,
    get_feedback_list,
    delete_feedback,
    ACTION_LIKE,
    ACTION_DISLIKE,
    ACTION_USE_PLAN,
    TARGET_ANSWER,
    TARGET_PLAN,
)
from src.services.auth_service import get_current_user_id, oauth2_scheme
from src.utils.logger import setup_logger

logger = setup_logger(name="Feedback_Router")

router = APIRouter(prefix="/feedback", tags=["用户反馈"])


# ============================================================
# 数据模型定义 (Schemas)
# ============================================================

class AnswerFeedbackRequest(BaseModel):
    """回答反馈请求体"""
    message_uuid: str = Field(..., description="回答的 message_uuid（对应 ChatHistory.message_uuid）")
    action: str = Field(..., description="反馈动作: like / dislike")
    rating: Optional[int] = Field(default=None, ge=1, le=5, description="评分(1-5)")
    comment: Optional[str] = Field(default=None, description="评论内容")
    category: Optional[str] = Field(
        default=None,
        description="反馈分类: factual_error / style_issue / unsafe / other",
    )


class PlanFeedbackRequest(BaseModel):
    """计划反馈请求体"""
    plan_id: int = Field(..., description="计划 ID")
    action: str = Field(..., description="反馈动作: like / dislike / use_plan")
    rating: Optional[int] = Field(default=None, ge=1, le=5, description="评分(1-5)")
    comment: Optional[str] = Field(default=None, description="评论内容")
    category: Optional[str] = Field(
        default=None,
        description="反馈分类: factual_error / style_issue / unsafe / other",
    )


class FeedbackResponse(BaseModel):
    """反馈操作响应"""
    success: bool = Field(..., description="操作是否成功")
    feedback: Optional[Dict[str, Any]] = Field(default=None, description="反馈详情")
    error: Optional[str] = Field(default=None, description="错误信息")


class PreferencesResponse(BaseModel):
    """偏好查询响应"""
    success: bool = Field(default=True)
    stats: Dict[str, Any] = Field(default_factory=dict, description="反馈统计数据")
    style_directive: str = Field(default="", description="风格调整指令")
    updated_at: Optional[str] = Field(default=None, description="最后更新时间")


class FeedbackListResponse(BaseModel):
    """反馈列表响应"""
    success: bool = Field(default=True)
    total: int = Field(default=0, description="记录总数")
    data: List[Dict[str, Any]] = Field(default_factory=list, description="反馈记录列表")


class DeleteFeedbackRequest(BaseModel):
    """删除反馈请求体"""
    target_type: str = Field(..., description="目标类型: answer / plan")
    target_id: str = Field(..., description="目标 ID")


# ============================================================
# 接口实现
# ============================================================

@router.post("/answer", response_model=FeedbackResponse)
def submit_answer_feedback(
    body: AnswerFeedbackRequest,
    token: str = Depends(oauth2_scheme),
    db=Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    对回答提交反馈

    支持的动作:
    - like: 点赞，表示回答有帮助
    - dislike: 踩，表示回答需要改进

    幂等保证:
    - 同一用户对同一回答只能有一条反馈
    - 重复提交会更新原有反馈的动作
    """
    if body.action not in (ACTION_LIKE, ACTION_DISLIKE):
        raise HTTPException(
            status_code=400,
            detail=f"回答反馈只支持 like/dislike 动作，当前: {body.action}",
        )

    result = submit_feedback(
        db=db,
        user_id=current_user_id,
        target_type=TARGET_ANSWER,
        target_id=body.message_uuid,
        action=body.action,
        rating=body.rating,
        comment=body.comment,
        category=body.category,
    )

    if not result.get("success", False):
        raise HTTPException(status_code=400, detail=result.get("error", "提交失败"))

    return result


@router.post("/plan", response_model=FeedbackResponse)
def submit_plan_feedback(
    body: PlanFeedbackRequest,
    token: str = Depends(oauth2_scheme),
    db=Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    对计划提交反馈

    支持的动作:
    - like: 点赞，表示计划实用
    - dislike: 踩，表示计划需要改进
    - use_plan: 使用该计划（最强正向信号）
    """
    result = submit_feedback(
        db=db,
        user_id=current_user_id,
        target_type=TARGET_PLAN,
        target_id=str(body.plan_id),
        action=body.action,
        rating=body.rating,
        comment=body.comment,
        category=body.category,
    )

    if not result.get("success", False):
        raise HTTPException(status_code=400, detail=result.get("error", "提交失败"))

    return result


@router.delete("", response_model=FeedbackResponse)
def remove_feedback(
    body: DeleteFeedbackRequest,
    token: str = Depends(oauth2_scheme),
    db=Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """撤销反馈"""
    result = delete_feedback(
        db=db,
        user_id=current_user_id,
        target_type=body.target_type,
        target_id=body.target_id,
    )

    if not result.get("success", False):
        raise HTTPException(status_code=404, detail=result.get("error", "反馈不存在"))

    return result


@router.get("/list", response_model=FeedbackListResponse)
def list_user_feedback(
    target_type: Optional[str] = Query(default=None, description="过滤目标类型: answer / plan"),
    limit: int = Query(default=50, ge=1, le=200, description="返回条数"),
    token: str = Depends(oauth2_scheme),
    db=Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """查询用户反馈列表"""
    data = get_feedback_list(
        db=db,
        user_id=current_user_id,
        target_type=target_type,
        limit=limit,
    )

    return {"success": True, "total": len(data), "data": data}


@router.get("/preferences", response_model=PreferencesResponse)
def get_my_preferences(
    token: str = Depends(oauth2_scheme),
    db=Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    查询用户反馈偏好与风格指令

    返回:
    - stats: 最近30天的反馈统计
    - style_directive: 供 RAG 回答生成时注入的风格调整指令
    """
    result = get_user_preferences(db=db, user_id=current_user_id)
    return result


@router.get("/stats", response_model=PreferencesResponse)
def get_feedback_stats(
    token: str = Depends(oauth2_scheme),
    db=Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """查询用户反馈统计（便捷接口）"""
    result = get_user_preferences(db=db, user_id=current_user_id)
    return result