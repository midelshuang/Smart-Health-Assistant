"""
个性化饮食计划 API 路由

对外暴露 plan_service 的 HTTP 接口，提供：
1. POST /plans/generate     —— 生成个性化饮食计划（LLM + RAG + 用户档案）
2. GET  /plans              —— 列出当前用户的所有计划
3. GET  /plans/{plan_id}    —— 获取单个计划详情
4. PUT  /plans/{plan_id}    —— 更新计划的 config（用户在 UI 上修改后调用）
5. DELETE /plans/{plan_id}  —— 删除计划
6. POST /plans/{plan_id}/checkin —— 计划打卡（记录当日执行情况）
7. GET  /plans/{plan_id}/checkins —— 列出某计划的所有打卡记录
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import date
from sqlalchemy.orm import Session

from src.repositories.session import get_db
from src.services.plan_service import (
    generate_plan,
    list_plans,
    get_plan_detail,
    update_plan_config,
    delete_plan,
    checkin_plan,
    list_checkins,
    save_plan_config,
)
from src.services.auth_service import get_current_user_id, oauth2_scheme
from src.utils.logger import setup_logger

logger = setup_logger(name="Plan_Router")

router = APIRouter(prefix="/plans", tags=["个性化饮食计划"])


# ============================================================
# 数据模型定义 (Schemas)
# ============================================================

class PlanGenerateRequest(BaseModel):
    """生成计划请求体"""
    goal_type: str = Field(
        default="health_maintenance",
        description="目标类型: weight_loss / muscle_gain / health_maintenance",
    )
    start_date: Optional[date] = Field(default=None, description="计划开始日期，默认今天")
    end_date: Optional[date] = Field(default=None, description="计划结束日期（可选）")


class PlanConfigUpdateRequest(BaseModel):
    """更新计划 config 请求体"""
    config: Dict[str, Any] = Field(..., description="新的计划 config（JSONB 结构）")


class PlanSaveConfigRequest(BaseModel):
    """保存已有计划 config（手动保存/前端兜底）"""
    config: Dict[str, Any] = Field(..., description="计划 config（直接入库，不调用 LLM）")
    goal_type: str = Field(default="health_maintenance", description="目标类型")


class PlanCheckInRequest(BaseModel):
    """计划打卡请求体"""
    checkin_date: Optional[date] = Field(default=None, description="打卡日期，默认今天")
    actual_calories: Optional[float] = Field(default=None, description="当日实际摄入热量(kcal)")
    notes: Optional[str] = Field(default=None, description="打卡备注")


# ============================================================
# 接口实现
# ============================================================

@router.post("/generate")
def generate_diet_plan(
    body: PlanGenerateRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    生成个性化饮食计划

    流程：
    1. 读取用户档案（身高/体重/BMI/体脂率/健康目标等）
    2. 基于档案构建检索查询
    3. RAG 检索相关权威指南
    4. 调用 LLM 生成结构化计划 JSON
    5. 持久化到 Plan 表
    """
    try:
        result = generate_plan(
            db=db,
            user_id=current_user_id,
            goal_type=body.goal_type,
            start_date=body.start_date,
            end_date=body.end_date,
        )

        if not result.get("success", False):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "计划生成失败"),
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成计划异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="生成计划失败，请稍后重试或联系管理员")


@router.post("/save-config")
def save_plan_config_api(
    body: PlanSaveConfigRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    手动保存一份已有的计划 config（前端兜底）

    场景：RAG 聊天里生成的计划因为是匿名/状态不匹配等原因未自动入库，
    前端拿到 config 后调用此接口直接保存，不走 LLM 重新生成。
    """
    try:
        result = save_plan_config(
            db=db,
            user_id=current_user_id,
            goal_type=body.goal_type,
            config=body.config,
        )
        if not result.get("success", False):
            raise HTTPException(status_code=400, detail=result.get("error", "保存失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"手动保存计划异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="保存失败")


@router.get("")
def list_user_plans(
    status: Optional[str] = Query(default=None, description="计划状态过滤: active/archived"),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """列出当前用户的所有计划"""
    return list_plans(db=db, user_id=current_user_id, status=status)


@router.get("/{plan_id}")
def get_plan(
    plan_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """获取单个计划详情"""
    result = get_plan_detail(db=db, user_id=current_user_id, plan_id=plan_id)
    if not result.get("success", False):
        raise HTTPException(status_code=404, detail=result.get("error", "计划不存在"))
    return result


@router.put("/{plan_id}")
def update_plan(
    plan_id: int,
    body: PlanConfigUpdateRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    更新计划的 config

    用户在 UI 上修改计划内容后调用此接口。
    config 结构参考 plan_service.py 中的说明。
    """
    result = update_plan_config(db=db, user_id=current_user_id, plan_id=plan_id, config=body.config)
    if not result.get("success", False):
        raise HTTPException(status_code=404, detail=result.get("error", "计划不存在"))
    return result


@router.delete("/{plan_id}")
def remove_plan(
    plan_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """删除计划"""
    result = delete_plan(db=db, user_id=current_user_id, plan_id=plan_id)
    if not result.get("success", False):
        raise HTTPException(status_code=404, detail=result.get("error", "计划不存在"))
    return result


@router.post("/{plan_id}/checkin")
def create_checkin(
    plan_id: int,
    body: PlanCheckInRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    计划打卡（记录当日执行情况）

    同一天同一计划只能打卡一次（数据库唯一约束保证）。
    """
    result = checkin_plan(
        db=db,
        user_id=current_user_id,
        plan_id=plan_id,
        checkin_date_val=body.checkin_date,
        actual_calories=body.actual_calories,
        notes=body.notes,
    )
    if not result.get("success", False):
        raise HTTPException(status_code=400, detail=result.get("error", "打卡失败"))
    return result


@router.get("/{plan_id}/checkins")
def list_plan_checkins(
    plan_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """列出某计划的所有打卡记录"""
    result = list_checkins(db=db, user_id=current_user_id, plan_id=plan_id)
    if not result.get("success", False):
        raise HTTPException(status_code=404, detail=result.get("error", "计划不存在"))
    return result


# ==========================================
# 🆕 动态调整接口（AdjustmentNode）
# ==========================================

class AdjustmentRequest(BaseModel):
    """动态调整请求体"""
    current_meal: Optional[str] = Field(
        default=None,
        description="当前餐次 breakfast/lunch/dinner/snack，不传则自动推断",
    )
    log_date: Optional[date] = Field(default=None, description="查询日期，默认今天")


@router.get("/adjustment/today")
def get_today_adjustment_api(
    plan_id: Optional[int] = Query(default=None, description="指定计划ID，不传则取最新激活计划"),
    current_meal: Optional[str] = Query(default=None, description="当前餐次"),
    log_date: Optional[date] = Query(default=None, description="查询日期"),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    获取今日动态调整方案

    基于用户当前激活的饮食计划 + 今日已记录的饮食摄入，
    计算剩余热量配额，并给出下一餐的建议摄入量。

    返回结构：
    ```json
    {
      "success": true,
      "plan_id": 1,
      "plan_target_calories": 1800,
      "today_intake": {
        "total_calories": 600,
        "by_meal": {"breakfast": {"calories": 450}, "lunch": {"calories": 150}}
      },
      "adjustment": {
        "remaining_calories": 1200,
        "next_meal": "snack",
        "next_meal_target_calories": 180,
        "adjustment_ratio": 1.2,
        "status": "on_track",
        "message": "今日进度正常..."
      }
    }
    ```
    """
    from src.services.adjustment_service import get_today_adjustment
    result = get_today_adjustment(
        db=db,
        user_id=current_user_id,
        plan_id=plan_id,
        current_meal=current_meal,
        log_date=log_date,
    )
    if not result.get("success", False):
        raise HTTPException(status_code=400, detail=result.get("error", "调整计算失败"))
    return result
