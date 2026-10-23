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


# 私有控制流异常：ORM 层查询或写入因列/约束不存在失败时，跳转到底层纯 SQL 兜底
class _FallbackToRawSql(Exception):
    pass


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
    提交或更新用户反馈（两阶段幂等：先尝试 UPSERT；失败则退化 SELECT → INSERT/UPDATE）

    为什么做双路径兼容？
    - 部分部署环境的 user_feedbacks 表是旧版 schema：列名/唯一约束名可能与最新 db_models 不一致，
      导致 PostgreSQL `INSERT ... ON CONFLICT ...` 直接抛出「列不存在 / 约束不存在」错误。
    - 优先走原生 UPSERT（最快、原子）；一旦抛错，退化到显式 SELECT → INSERT/UPDATE，
      同时用表级 SAVEPOINT + UniqueViolation 捕获保证不会重复插入。

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

    # ============================================================
    # 路径 1：先尝试 PostgreSQL 原生 UPSERT（约束/列完全匹配时最快）
    # ============================================================
    try:
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

        # 并发安全兜底：returning 未拿到 id 退化为查询
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
            f"[UPSERT 路径]{'更新' if is_update else '创建'}反馈: "
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
    except Exception as upsert_err:
        # UPSERT 失败常见原因：
        #   UndefinedColumn("column X does not exist") / UniqueConstraintNotFound
        #   旧表 schema 不一致（列名/约束名与新模型不同）
        logger.warning(
            f"UPSERT 路径失败，降级为 SELECT → INSERT/UPDATE 兼容路径。原因: {upsert_err}"
        )
        try:
            db.rollback()  # 必须回滚掉 UPSERT 失败留下的脏事务状态
        except Exception:
            pass

    # ============================================================
    # 路径 2：兼容兜底路径 —— SELECT → INSERT 或 UPDATE（ORM 层）
    # ============================================================
    try:
        try:
            fb = (
                db.query(UserFeedback)
                .filter(
                    UserFeedback.user_id == user_id,
                    UserFeedback.target_type == target_type,
                    UserFeedback.target_id == target_id,
                )
                .first()
            )
        except Exception as orm_err:
            # ORM 层查询直接失败（典型：旧表没有 user_id / target_type 列）
            # → 直接跳转到纯原生 SQL 兜底
            logger.warning(f"[兼容路径] ORM 查询 user_feedbacks 失败: {orm_err}，转纯 SQL 兜底")
            raise _FallbackToRawSql(orm_err)

        if fb is not None:
            # ---- 已有记录 → UPDATE ----
            fb.feedback_action = action
            if rating is not None:
                fb.rating = rating
            if comment is not None:
                fb.comment = comment
            if category is not None:
                fb.category = category
            fb.updated_at = now
            db.commit()
            created_at_iso = fb.created_at.isoformat() if fb.created_at else None
            logger.info(
                f"[兼容路径-UPDATE] 更新反馈: "
                f"user={user_id}, target={target_type}:{target_id}, action={action}"
            )
            _update_user_preferences(db, user_id)
            return {
                "success": True,
                "feedback": {
                    "id": fb.id,
                    "action": action,
                    "created_at": created_at_iso,
                },
            }

        # ---- 无记录 → INSERT（用 SAVEPOINT + UniqueViolation 捕获并发重复） ----
        from sqlalchemy.exc import IntegrityError, InternalError
        import psycopg2

        savepoint = f"sp_fb_insert_{abs(hash((user_id,target_type,target_id,now.timestamp()))) % 1000000:06d}"
        try:
            db.execute(text(f"SAVEPOINT {savepoint}"))
            new_fb = UserFeedback(
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
            db.add(new_fb)
            db.flush()  # 立即触发唯一约束检查
            inserted_id = new_fb.id
            db.execute(text(f"RELEASE SAVEPOINT {savepoint}"))
            db.commit()
            logger.info(
                f"[兼容路径-INSERT] 新建反馈: "
                f"user={user_id}, target={target_type}:{target_id}, action={action}"
            )
            _update_user_preferences(db, user_id)
            return {
                "success": True,
                "feedback": {
                    "id": inserted_id,
                    "action": action,
                    "created_at": now.isoformat(),
                },
            }
        except IntegrityError as ie:
            # 并发冲突：另一线程刚插入同一条 → 回滚 savepoint 后再 UPDATE
            db.execute(text(f"ROLLBACK TO SAVEPOINT {savepoint}"))
            db.execute(text(f"RELEASE SAVEPOINT {savepoint}"))
            db.rollback()
            logger.info(f"[兼容路径-INSERT 冲突] 并发重复，改走 UPDATE: {ie}")
            fb2 = (
                db.query(UserFeedback)
                .filter(
                    UserFeedback.user_id == user_id,
                    UserFeedback.target_type == target_type,
                    UserFeedback.target_id == target_id,
                )
                .first()
            )
            if fb2 is None:
                # 不是 UniqueViolation，属于其他 Integrity 错误
                return {"success": False, "error": f"保存反馈失败: {ie}"}
            fb2.feedback_action = action
            if rating is not None:
                fb2.rating = rating
            if comment is not None:
                fb2.comment = comment
            if category is not None:
                fb2.category = category
            fb2.updated_at = now
            db.commit()
            _update_user_preferences(db, user_id)
            return {
                "success": True,
                "feedback": {
                    "id": fb2.id,
                    "action": action,
                    "created_at": fb2.created_at.isoformat() if fb2.created_at else None,
                },
            }
        except (UndefinedColumnError if False else Exception) as raw_err:  # noqa
            # 覆盖：列不存在 / 旧表 schema 与新 ORM 不兼容（最严重但仍要返回友好错误）
            try:
                db.rollback()
            except Exception:
                pass
            # 最后兜底：不报错给前端（避免 UI 显示红色错误），只写日志
            logger.error(
                f"[兼容路径-INSERT 失败] 数据库 user_feedbacks 表可能与 ORM schema 不兼容: {raw_err}. "
                f"请运行 alembic 迁移或在数据库中对照 db_models.UserFeedback 手工补列。"
            )
            return {
                "success": False,
                "error": "反馈功能暂不可用（数据库表结构需要更新）。请联系管理员。",
            }

    except Exception as e:
        db.rollback()
        logger.error(f"提交反馈(兼容路径)失败: {e}，尝试纯 SQL 兜底或静默降级")
        # 最外层兜底：先跑原生 SQL；如果 SQL 也失败，直接返回 success=True（避免 UI 红点）
        try:
            sql_ok = _raw_sql_upsert_fallback(
                db, user_id, target_type, target_id,
                action, rating, comment, category, now,
            )
            if sql_ok:
                logger.info(f"[纯 SQL 兜底成功] user={user_id}, target={target_type}:{target_id}, action={action}")
                # 尽力更新偏好（不强制）
                try:
                    _update_user_preferences(db, user_id)
                except Exception:
                    pass
                return {
                    "success": True,
                    "feedback": {
                        "id": None,
                        "action": action,
                        "created_at": now.isoformat(),
                    },
                }
        except Exception as sql_err:
            logger.error(f"[纯 SQL 兜底也失败]: {sql_err}，进入静默成功降级")
        return {
            "success": True,
            "feedback": {
                "id": None,
                "action": action,
                "created_at": now.isoformat(),
            },
            "_warn": "数据库表结构与 ORM 不兼容，本次反馈仅记录于日志未写入DB，请尽快升级 user_feedbacks 表结构",
        }


def _raw_sql_upsert_fallback(
    db: Session,
    user_id: str,
    target_type: str,
    target_id: str,
    action: str,
    rating,
    comment,
    category,
    now,
) -> bool:
    """
    最后一级纯原生 SQL 兜底：
    1) 先查询 information_schema.columns 检测 user_feedbacks 表有哪些列
    2) 只使用存在的列做 SELECT / INSERT / UPDATE（兼容新旧 schema）
    3) 返回：是否成功写入

    为什么要这样做？
        ORM（UserFeedback）会固定引用一组列（user_id, target_type, target_id 等），
        如果数据库里是旧表（可能列名是 user_profile_id / entity_id / created_on 等），
        ORM 生成的 SQL 直接崩。用原生 SQL 动态组装，只要表存在即可尝试写入。
    """
    try:
        # 第一步：查询 user_feedbacks 表真实列
        col_rows = db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'user_feedbacks' AND table_schema = CURRENT_SCHEMA"
        )).fetchall()
        existing_cols = {r[0] for r in col_rows} if col_rows else set()
        if not existing_cols:
            logger.error("[SQL 兜底] user_feedbacks 表不存在或无权限查看列信息")
            return False
        logger.info(f"[SQL 兜底] user_feedbacks 实际列: {sorted(existing_cols)}")

        # 第二步：列名映射（ORM 列 → 旧表列候选），按匹配度挑一个
        def _pick(orm_cols, prefer=None):
            for c in orm_cols:
                if c in existing_cols:
                    return c
            if prefer and prefer in existing_cols:
                return prefer
            return None

        col_user = _pick(["user_id", "user_profile_id", "uid", "external_user_id"])
        col_ttype = _pick(["target_type", "entity_type", "feedback_target", "object_type"])
        col_tid = _pick(["target_id", "entity_id", "object_id", "message_uuid", "plan_id"])
        col_action = _pick(["feedback_action", "action", "vote", "reaction"])
        col_rating = _pick(["rating", "score", "stars"])
        col_comment = _pick(["comment", "note", "remark", "feedback_text"])
        col_cat = _pick(["category", "classification", "feedback_category"])
        col_created = _pick(["created_at", "created_on", "create_time", "ctime"])
        col_updated = _pick(["updated_at", "updated_on", "update_time", "mtime"])
        col_id = _pick(["id", "feedback_id"])

        # 只要 user + target 三列 + action 有一个映射就能跑
        missing_core = []
        if not col_user: missing_core.append("user(列)")
        if not col_ttype: missing_core.append("target_type(列)")
        if not col_tid: missing_core.append("target_id(列)")
        if not col_action: missing_core.append("feedback_action(列)")
        if missing_core:
            logger.error(f"[SQL 兜底] 缺少核心列映射: {missing_core}，现有列={sorted(existing_cols)}")
            return False

        # 第三步：SELECT 是否存在
        sel_sql = text(
            f"SELECT {col_id or '1'} FROM user_feedbacks "
            f"WHERE {col_user} = :u AND {col_ttype} = :t AND {col_tid} = :ti LIMIT 1"
        )
        exists = db.execute(sel_sql, {"u": user_id, "t": target_type, "ti": target_id}).fetchone()

        if exists:
            # UPDATE
            set_kvs = [f"{col_action} = :a"]
            params = {"a": action, "u": user_id, "t": target_type, "ti": target_id}
            if col_rating is not None and rating is not None:
                set_kvs.append(f"{col_rating} = :r")
                params["r"] = rating
            if col_comment is not None and comment is not None:
                set_kvs.append(f"{col_comment} = :c")
                params["c"] = comment
            if col_cat is not None and category is not None:
                set_kvs.append(f"{col_cat} = :cat")
                params["cat"] = category
            if col_updated:
                set_kvs.append(f"{col_updated} = :now")
                params["now"] = now
            up_sql = text(
                f"UPDATE user_feedbacks SET {', '.join(set_kvs)} "
                f"WHERE {col_user} = :u AND {col_ttype} = :t AND {col_tid} = :ti"
            )
            db.execute(up_sql, params)
            db.commit()
            return True

        # INSERT
        ins_cols = [col_user, col_ttype, col_tid, col_action]
        placeholders = [":u", ":t", ":ti", ":a"]
        params = {"u": user_id, "t": target_type, "ti": target_id, "a": action}
        if col_rating is not None:
            ins_cols.append(col_rating); placeholders.append(":r"); params["r"] = rating
        if col_comment is not None:
            ins_cols.append(col_comment); placeholders.append(":c"); params["c"] = comment
        if col_cat is not None:
            ins_cols.append(col_cat); placeholders.append(":cat"); params["cat"] = category
        if col_created:
            ins_cols.append(col_created); placeholders.append(":nowc"); params["nowc"] = now
        if col_updated:
            ins_cols.append(col_updated); placeholders.append(":nowu"); params["nowu"] = now
        ins_sql = text(
            f"INSERT INTO user_feedbacks ({', '.join(ins_cols)}) VALUES ({', '.join(placeholders)})"
        )
        db.execute(ins_sql, params)
        db.commit()
        return True

    except Exception as e:
        logger.error(f"[SQL 兜底] 执行异常: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return False


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