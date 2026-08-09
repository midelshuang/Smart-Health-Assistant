"""
用户反馈服务 (Feedback Service)

职责:
1. 提交/更新反馈（点赞/踩/使用计划）—— 使用 UPSERT 语义保证幂等性
2. 聚合用户反馈偏好 —— 计算风格调整指令，供 RAG 回答生成时注入
3. 查询用户反馈统计

核心设计:
- 防刷分: 通过 (user_id, target_type, target_id) 唯一约束 + upsert 实现幂等
- 偏好聚合: 点赞率 < 40% 时倾向于调整回答方式，点赞率 > 60% 时保持当前方式
- 风格指令: 将偏好转化为 Prompt 注入字符串，无缝集成到现有 RAG 流程
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert

from src.models.db_models import UserFeedback, UserProfile
from src.utils.logger import setup_logger

logger = setup_logger(name="FeedbackService")

# 反馈动作常量
ACTION_LIKE = "like"
ACTION_DISLIKE = "dislike"
ACTION_USE_PLAN = "use_plan"
VALID_ACTIONS = {ACTION_LIKE, ACTION_DISLIKE, ACTION_USE_PLAN}

# 目标类型常量
TARGET_ANSWER = "answer"
TARGET_PLAN = "plan"
VALID_TARGETS = {TARGET_ANSWER, TARGET_PLAN}

# 偏好阈值
LIKE_RATIO_HIGH = 0.6   # 点赞率超过此值 → 保持当前回答方式
LIKE_RATIO_LOW = 0.4    # 点赞率低于此值 → 调整回答方式
RECENT_WINDOW_DAYS = 30  # 最近反馈窗口


def submit_feedback(
    db: Session,
    user_id: str,
    target_type: str,
    target_id: str,
    action: str,
    rating: Optional[int] = None,
    comment: Optional[str] = None,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """
    提交或更新用户反馈（原子 UPSERT，数据库级幂等）

    使用 PostgreSQL INSERT ... ON CONFLICT 实现真正的原子 UPSERT：
    - 解决原 SELECT→INSERT 的竞态条件（并发请求时可能产生重复行）
    - 依赖 (user_id, target_type, target_id) 唯一约束做冲突检测
    - 同一用户对同一目标的反馈，后一次提交会覆盖前一次的 action/rating/comment

    Args:
        db: 数据库会话
        user_id: 用户 external_id
        target_type: 目标类型 (answer / plan)
        target_id: 目标 ID
        action: 反馈动作 (like / dislike / use_plan)
        rating: 可选评分 (1-5)
        comment: 可选评论
        category: 可选反馈分类

    Returns:
        反馈结果字典
    """
    if action not in VALID_ACTIONS:
        return {"success": False, "error": f"无效的反馈动作: {action}"}
    if target_type not in VALID_TARGETS:
        return {"success": False, "error": f"无效的目标类型: {target_type}"}

    now = datetime.now()
    try:
        # ---- 原子 UPSERT：INSERT ON CONFLICT DO UPDATE ----
        # 相比原 SELECT → UPDATE/INSERT，避免了 TOCTOU 竞态：
        #   线程A查不存在 → 线程B查也不存在 → 两个都 INSERT → 唯一约束报错
        # 现在由 PostgreSQL 在一次原子操作中完成冲突检测和更新。
        stmt = (
            insert(UserFeedback)
            .values(
                user_id=user_id,
                target_type=target_type,
                target_id=target_id,
                feedback_action=action,
                rating=rating,
                comment=comment,
                category=category,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                # 对应 db_models 中 UniqueConstraint('user_id', 'target_type', 'target_id')
                constraint="uq_user_target_feedback",
                set_={
                    "feedback_action": action,
                    "rating": rating if rating is not None else UserFeedback.rating,
                    "comment": comment if comment is not None else UserFeedback.comment,
                    "category": category if category is not None else UserFeedback.category,
                    "updated_at": now,
                },
            )
            .returning(UserFeedback.id, UserFeedback.created_at)
        )

        result = db.execute(stmt).fetchone()
        db.commit()

        fb_id = result[0] if result else None
        created_at_iso = result[1].isoformat() if result and result[1] else None

        # ---- 并发安全兜底：如果 returning 未拿到 id，退化为查询 ----
        if fb_id is None:
            fb = (
                db.query(UserFeedback)
                .filter(
                    UserFeedback.user_id == user_id,
                    UserFeedback.target_type == target_type,
                    UserFeedback.target_id == target_id,
                )
                .first()
            )
            if fb:
                fb_id = fb.id
                created_at_iso = fb.created_at.isoformat() if fb.created_at else None

        is_update = (
            created_at_iso is not None
            and datetime.fromisoformat(created_at_iso.replace("Z", "+00:00")).replace(tzinfo=None) < now
        )
        logger.info(
            f"{'更新' if is_update else '创建'}反馈(UPSERT): "
            f"user={user_id}, target={target_type}:{target_id}, action={action}"
        )

        _update_user_preferences(db, user_id)

        return {
            "success": True,
            "feedback": {
                "id": fb_id,
                "action": action,
                "created_at": created_at_iso,
            },
        }

    except Exception as e:
        db.rollback()
        logger.error(f"提交反馈(UPSERT)失败: {e}")
        return {"success": False, "error": f"提交反馈失败: {e}"}


def _update_user_preferences(db: Session, user_id: str) -> None:
    """
    聚合用户反馈并更新 UserProfile.feedback_preferences

    统计逻辑:
    - 只统计最近 RECENT_WINDOW_DAYS 天的反馈
    - 分别统计 answer 和 plan 两类反馈
    - 计算点赞率、踩率、使用率
    - 根据比率生成风格偏好信号
    """
    since = datetime.now() - timedelta(days=RECENT_WINDOW_DAYS)

    profiles = [TARGET_ANSWER, TARGET_PLAN]
    aggregated = {}

    for ptype in profiles:
        feedbacks = (
            db.query(UserFeedback)
            .filter(
                UserFeedback.user_id == user_id,
                UserFeedback.target_type == ptype,
                UserFeedback.created_at >= since,
            )
            .all()
        )

        total = len(feedbacks)
        likes = sum(1 for f in feedbacks if f.feedback_action == ACTION_LIKE)
        dislikes = sum(1 for f in feedbacks if f.feedback_action == ACTION_DISLIKE)
        uses = sum(1 for f in feedbacks if f.feedback_action == ACTION_USE_PLAN)

        like_ratio = likes / total if total > 0 else 0.5
        dislike_ratio = dislikes / total if total > 0 else 0.0

        aggregated[ptype] = {
            "total": total,
            "likes": likes,
            "dislikes": dislikes,
            "uses": uses,
            "like_ratio": round(like_ratio, 3),
            "dislike_ratio": round(dislike_ratio, 3),
        }

    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if profile:
        prefs = profile.feedback_preferences or {}
        prefs["stats"] = aggregated
        prefs["updated_at"] = datetime.now().isoformat()
        profile.feedback_preferences = prefs
        db.commit()
        logger.info(f"更新用户反馈偏好: user={user_id}")


def get_user_preferences(db: Session, user_id: str) -> Dict[str, Any]:
    """
    获取用户反馈偏好（供 API 查询使用）

    Returns:
        包含统计数据和风格指令的偏好字典
    """
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile or not profile.feedback_preferences:
        return {
            "stats": {
                "answer": {"total": 0, "likes": 0, "dislikes": 0, "uses": 0, "like_ratio": 0.5, "dislike_ratio": 0.0},
                "plan": {"total": 0, "likes": 0, "dislikes": 0, "uses": 0, "like_ratio": 0.5, "dislike_ratio": 0.0},
            },
            "style_directive": "",
        }

    prefs = profile.feedback_preferences
    stats = prefs.get("stats", {})
    directive = get_style_directive(stats)

    return {
        "stats": stats,
        "style_directive": directive,
        "updated_at": prefs.get("updated_at"),
    }


def get_style_directive(stats: Dict[str, Any]) -> str:
    """
    根据反馈统计生成风格调整指令

    策略:
    - answer 点赞率 >= 60%: 保持当前回答方式，强调用户偏好的格式
    - answer 点赞率 < 40%: 调整回答方式 —— 更简洁、增加结构化、优先引用来源
    - plan 使用率 >= 50%: 计划生成时保持当前风格
    - plan 踩率 >= 40%: 计划生成时调整 —— 更灵活的餐次安排、更多备选方案

    Args:
        stats: 反馈统计数据

    Returns:
        注入到系统提示词中的指令字符串（空字符串表示无需调整）
    """
    directives = []

    answer_stats = stats.get("answer", {})
    plan_stats = stats.get("plan", {})

    answer_total = answer_stats.get("total", 0)
    answer_like_ratio = answer_stats.get("like_ratio", 0.5)
    answer_dislike_ratio = answer_stats.get("dislike_ratio", 0.0)

    if answer_total >= 3:
        if answer_dislike_ratio >= LIKE_RATIO_LOW:
            directives.append(
                "用户近期对回答的满意度较低。请调整回答方式：更简洁明了，"
                "使用更多结构化格式（列表/表格），优先引用知识库中的权威来源。"
            )
        elif answer_like_ratio >= LIKE_RATIO_HIGH:
            directives.append(
                "用户近期对回答满意度较高，保持当前的回答风格和详细程度。"
            )

    plan_total = plan_stats.get("total", 0)
    plan_like_ratio = plan_stats.get("like_ratio", 0.5)
    plan_uses = plan_stats.get("uses", 0)
    plan_dislike_ratio = plan_stats.get("dislike_ratio", 0.0)

    if plan_total >= 3:
        if plan_dislike_ratio >= LIKE_RATIO_LOW:
            directives.append(
                "用户近期对计划的满意度较低。生成计划时请提供更灵活的餐次安排、"
                "更多备选食物方案，并考虑用户的执行便利性。"
            )
        elif plan_uses >= max(2, plan_total * 0.5):
            directives.append(
                "用户近期频繁使用生成的计划，生成新计划时保持当前风格，"
                "同时可以适当增加食物多样性。"
            )
        elif plan_like_ratio >= LIKE_RATIO_HIGH:
            directives.append(
                "用户近期对计划满意度较高，保持当前的计划风格。"
            )

    return "\n\n".join(directives)


def get_style_directive_for_answer(db: Session, user_id: str) -> str:
    """
    获取用户回答风格指令（供 generator_node 调用）

    Args:
        db: 数据库会话
        user_id: 用户 external_id

    Returns:
        风格指令字符串，空字符串表示无需调整
    """
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile or not profile.feedback_preferences:
        return ""

    prefs = profile.feedback_preferences
    stats = prefs.get("stats", {})
    return get_style_directive(stats)


def get_style_directive_for_plan(db: Session, user_id: str) -> str:
    """
    获取用户计划风格指令（供 plan_generator_node 调用）

    与 get_style_directive_for_answer 类似，但侧重计划类反馈

    Args:
        db: 数据库会话
        user_id: 用户 external_id

    Returns:
        风格指令字符串
    """
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile or not profile.feedback_preferences:
        return ""

    prefs = profile.feedback_preferences
    stats = prefs.get("stats", {})
    directive = get_style_directive(stats)

    plan_stats = stats.get("plan", {})
    plan_dislike_ratio = plan_stats.get("dislike_ratio", 0.0)
    plan_total = plan_stats.get("total", 0)

    if plan_total >= 3 and plan_dislike_ratio >= LIKE_RATIO_LOW:
        extra = (
            "\n\n[计划生成特别指令] 请确保每餐热量分配合理，"
            "食物选择多样化，并提供至少2个备选方案以提高可执行性。"
        )
        directive = (directive + extra) if directive else extra.lstrip()

    return directive


def get_feedback_list(
    db: Session,
    user_id: str,
    target_type: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    获取用户反馈列表

    Args:
        db: 数据库会话
        user_id: 用户 external_id
        target_type: 可选过滤目标类型
        limit: 返回条数

    Returns:
        反馈记录列表
    """
    query = db.query(UserFeedback).filter(UserFeedback.user_id == user_id)
    if target_type:
        query = query.filter(UserFeedback.target_type == target_type)

    feedbacks = (
        query.order_by(UserFeedback.updated_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "id": f.id,
            "target_type": f.target_type,
            "target_id": f.target_id,
            "action": f.feedback_action,
            "rating": f.rating,
            "category": f.category,
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "updated_at": f.updated_at.isoformat() if f.updated_at else None,
        }
        for f in feedbacks
    ]


def delete_feedback(db: Session, user_id: str, target_type: str, target_id: str) -> Dict[str, Any]:
    """
    删除反馈（用户撤销反馈）

    Args:
        db: 数据库会话
        user_id: 用户 external_id
        target_type: 目标类型
        target_id: 目标 ID

    Returns:
        删除结果
    """
    try:
        feedback = (
            db.query(UserFeedback)
            .filter(
                UserFeedback.user_id == user_id,
                UserFeedback.target_type == target_type,
                UserFeedback.target_id == target_id,
            )
            .first()
        )

        if not feedback:
            return {"success": False, "error": "反馈不存在"}

        db.delete(feedback)
        db.commit()
        _update_user_preferences(db, user_id)
        logger.info(f"删除反馈: user={user_id}, target={target_type}:{target_id}")
        return {"success": True}

    except Exception as e:
        db.rollback()
        logger.error(f"删除反馈失败: {e}")
        return {"success": False, "error": f"删除反馈失败: {e}"}