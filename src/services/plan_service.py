"""
个性化饮食计划服务

职责：
1. 读取用户档案（身高/体重/BMI/体脂率/健康目标/省份/过敏史/运动爱好等）
2. 调用 RAG 管道检索相关权威指南（减脂/糖尿病/高血压等）
3. 调用 LLM 基于档案 + 指南生成结构化饮食计划
4. 持久化到 Plan 表（JSONB config）
5. 提供 CRUD + 打卡接口

生成的计划 config 结构（JSONB）：
{
  "summary": "计划摘要",
  "target_calories": 1800,
  "macros": {"protein_g": 90, "fat_g": 60, "carbs_g": 220},
  "meals": [
    {"meal_type": "breakfast", "time": "07:00-08:00",
     "items": [{"food": "全麦面包", "quantity": "2片", "calories": 200}],
     "total_calories": 450}
  ],
  "advice": "总体建议",
  "precautions": "注意事项"
}
"""

from typing import Dict, Any, List, Optional
from datetime import date, datetime
from decimal import Decimal
import json

from sqlalchemy.orm import Session

from src.models.db_models import Plan, PlanCheckIn, UserProfile, FitnessGoal
from src.utils.logger import setup_logger

logger = setup_logger(name="PlanService")


# ============================================================
# 1. 用户档案读取
# ============================================================

def _safe_float(value) -> Optional[float]:
    """安全转换 Decimal/None → float"""
    if value is None:
        return None
    return float(value)


def get_user_profile_dict(db: Session, user_id: str) -> Optional[Dict[str, Any]]:
    """
    读取用户档案，返回用于生成计划的字典

    Args:
        db: 数据库会话
        user_id: 用户 external_id

    Returns:
        用户档案字典，未找到返回 None
    """
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile:
        return None

    goal = db.query(FitnessGoal).filter(
        FitnessGoal.user_id == user_id,
        FitnessGoal.status == "active"
    ).order_by(FitnessGoal.id.desc()).first()

    return {
        "user_id": profile.user_id,
        "nickname": profile.nickname,
        "age": profile.age,
        "gender": profile.gender,
        "height": _safe_float(profile.height),
        "weight": _safe_float(profile.weight),
        "bmi": _safe_float(profile.bmi),
        "body_fat_percentage": _safe_float(profile.body_fat_percentage),
        "health_goal": profile.health_goal,
        "province": profile.province,
        "allergies": profile.allergies,
        "sports_hobby": profile.sports_hobby,
        "health_tags": profile.health_tags if profile.health_tags else [],
        "active_goal": {
            "goal_type": goal.goal_type if goal else None,
            "target_value": _safe_float(goal.target_value) if goal else None,
            "current_value": _safe_float(goal.current_value) if goal else None,
            "target_date": str(goal.target_date) if goal and goal.target_date else None,
        } if goal else None,
    }


# ============================================================
# 2. 计划生成（核心：LLM + RAG + 用户档案）
# ============================================================

# 生成计划用的系统提示词
PLAN_SYSTEM_PROMPT = (
    "你是一位专业的注册营养师。你的任务是严格基于用户档案和权威指南文档，"
    "制定个性化的每日饮食计划。\n\n"
    "【核心要求 - 必须遵守】\n"
    "1. 仔细阅读【权威指南参考】中的每一条指南内容，你的计划必须直接基于这些指南文档的具体建议\n"
    "2. 在你的计划中，必须体现指南中的具体食物、烹饪方式、餐次安排等内容\n"
    "3. 如果指南中包含具体的食谱示例，请直接参考并调整适用\n"
    "4. 必须以 JSON 格式输出，不要输出任何其他文字\n"
    "5. 食物要考虑用户所在地区的饮食习惯\n"
    "6. 必须规避用户的过敏食物\n"
    "7. 如用户有特定疾病（糖尿病/高血压等），需标注相关注意事项\n"
    "8. 如果指南参考内容为空或不足，请基于专业营养学知识生成计划，但需在advice中标注「基于通用营养学知识」\n"
)

# 生成计划用的用户提示词模板
PLAN_USER_PROMPT = (
    "【用户档案】\n{user_profile}\n\n"
    "【权威指南参考 - 必须严格基于以下指南内容生成计划】\n{guideline_context}\n\n"
    "请仔细阅读以上权威指南参考内容，基于用户档案信息，制定一份完整的每日饮食计划。\n"
    "在计划的summary和advice中，请引用指南中的具体建议（如引用了某份指南，请标注「参考XX指南」）。\n\n"
    "输出 JSON 格式（严格如下结构，不要包含 ```json 标记）：\n"
    "{{\n"
    '  "summary": "一句话计划摘要（含目标热量，并注明参考了哪些指南）",\n'
    '  "target_calories": 1800,\n'
    '  "macros": {{"protein_g": 90, "fat_g": 60, "carbs_g": 220}},\n'
    '  "meals": [\n'
    '    {{\n'
    '      "meal_type": "breakfast",\n'
    '      "time": "07:00-08:00",\n'
    '      "items": [\n'
    '        {{"food": "全麦面包", "quantity": "2片(约60g)", "calories": 200}},\n'
    '        {{"food": "鸡蛋", "quantity": "1个(约50g)", "calories": 80}}\n'
    '      ],\n'
    '      "total_calories": 450\n'
    '    }}\n'
    '  ],\n'
    '  "advice": "总体饮食建议（引用指南中的具体建议）",\n'
    '  "precautions": "注意事项（含过敏/疾病相关）"\n'
    "}}\n"
)


def _format_user_profile(profile: Dict[str, Any]) -> str:
    """将用户档案字典格式化为 LLM 易读的文本"""
    lines = []
    lines.append(f"- 昵称: {profile.get('nickname', '未知')}")
    lines.append(f"- 年龄: {profile.get('age', '未知')}")
    lines.append(f"- 性别: {profile.get('gender', '未知')}")
    lines.append(f"- 身高: {profile.get('height', '未知')} cm")
    lines.append(f"- 体重: {profile.get('weight', '未知')} kg")
    lines.append(f"- BMI: {profile.get('bmi', '未知')}")
    lines.append(f"- 体脂率: {profile.get('body_fat_percentage', '未知')} %")
    lines.append(f"- 健康目标: {profile.get('health_goal', '未指定')}")
    lines.append(f"- 所在省份: {profile.get('province', '未知')}")
    lines.append(f"- 过敏史: {profile.get('allergies', '无')}")
    lines.append(f"- 运动爱好: {profile.get('sports_hobby', '未指定')}")
    if profile.get("health_tags"):
        lines.append(f"- 健康标签: {', '.join(profile['health_tags'])}")
    goal = profile.get("active_goal")
    if goal and goal.get("goal_type"):
        lines.append(f"- 当前健身目标: {goal['goal_type']}")
        if goal.get("target_value"):
            lines.append(f"  目标值: {goal['target_value']}")
        if goal.get("current_value"):
            lines.append(f"  当前值: {goal['current_value']}")
    return "\n".join(lines)


def _build_plan_query(profile: Dict[str, Any]) -> str:
    """基于用户档案构建用于 RAG 检索的查询语句"""
    parts = []
    age = profile.get("age")
    gender = profile.get("gender")
    region = profile.get("province") or profile.get("region")
    bmi = profile.get("bmi")
    health_goal = profile.get("health_goal")
    health_tags = profile.get("health_tags", [])

    # 1. 年龄路由
    if age is not None:
        if age < 2:
            parts.append("婴幼儿喂养指南")
        elif age < 6:
            parts.append("学龄前儿童膳食指南")
        elif age < 12:
            parts.append("学龄儿童膳食指南")
        elif age < 18:
            parts.append("儿童青少年肥胖食养指南")
            parts.append("儿童青少年膳食指南")
        elif age < 50:
            if bmi and bmi >= 24:
                parts.append("成人肥胖食养指南")
            else:
                parts.append("中国居民膳食指南")
        elif age < 65:
            parts.append("中老年人营养指南")
        else:
            parts.append("老年人膳食指南")

    # 2. 地区路由
    if region:
        parts.append(f"{region}地区饮食")

    # 3. 健康目标/标签
    if health_goal:
        goal_map = {
            "weight_loss": "肥胖减重饮食",
            "muscle_gain": "增肌营养饮食",
            "health_maintenance": "健康饮食维持",
            "blood_sugar_control": "糖尿病饮食",
            "blood_pressure_control": "高血压饮食",
        }
        mapped = goal_map.get(health_goal, health_goal)
        parts.append(mapped)

    if health_tags:
        for tag in health_tags:
            if tag in ("糖尿病", "高血压", "高血脂", "肥胖", "超重"):
                parts.append(f"{tag}饮食指南")

    # 4. BMI 补充
    if bmi is not None:
        if bmi >= 28:
            parts.append("重度肥胖饮食")
        elif bmi >= 24:
            parts.append("超重肥胖饮食")
        elif bmi < 18.5:
            parts.append("低体重增重饮食")

    if not parts:
        parts.append("健康饮食指南")

    return " ".join(parts)


def _retrieve_guidelines(query: str) -> str:
    """
    直接调用向量+关键词混合检索相关权威指南（不走完整RAG管道）

    Args:
        query: 检索查询语句

    Returns:
        格式化后的指南上下文文本
    """
    try:
        from src.repositories.vector_store import VectorStore
        from src.core.embedding import get_embedding_function
        from src.config import settings

        vs = VectorStore()
        vs.connect()
        embedding_fn = get_embedding_function()
        collection = vs.get_or_create_collection(embedding_function=embedding_fn)

        # 1. 向量检索
        vector_results = vs.similarity_search(
            query_text=query,
            top_k=settings.RAG_TOP_K,
            threshold=0.2,
            where={"source_type": {"$in": ["guideline", "expert_media"]}},
            embedding_function=embedding_fn,
        )

        # 2. 关键词检索
        keyword_results = []
        try:
            import re
            all_data = collection.get(
                include=["documents", "metadatas"],
                where={"source_type": {"$in": ["guideline", "expert_media"]}},
            )
            if all_data and all_data["ids"]:
                query_lower = query.lower()
                keywords = set()
                for token in re.split(r'\s+', query_lower):
                    if len(token) >= 2:
                        keywords.add(token)
                        for i in range(len(token) - 1):
                            keywords.add(token[i:i+2])

                for i, doc_id in enumerate(all_data["ids"]):
                    doc = all_data["documents"][i] or ""
                    meta = all_data["metadatas"][i] or {}
                    doc_lower = doc.lower()
                    score = 0.0
                    for kw in keywords:
                        if kw in doc_lower:
                            score += doc_lower.count(kw) * 0.1
                    file_name = meta.get("file_name", "").lower()
                    for kw in keywords:
                        if kw in file_name:
                            score += 0.5
                    if score > 0:
                        keyword_results.append({
                            "id": doc_id,
                            "content": doc,
                            "score": min(score, 1.0),
                            "metadata": meta,
                        })
                keyword_results.sort(key=lambda x: x["score"], reverse=True)
                keyword_results = keyword_results[:settings.RAG_TOP_K]
        except Exception:
            pass

        # 3. 合并结果
        all_results = vector_results + keyword_results
        if not all_results:
            return "（未检索到相关指南）"

        # 去重（按id）
        seen_ids = set()
        unique_results = []
        for r in all_results:
            rid = r.get("id", "")
            if rid not in seen_ids:
                seen_ids.add(rid)
                unique_results.append(r)

        # 按分数排序
        unique_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        unique_results = unique_results[:8]

        lines = []
        for i, item in enumerate(unique_results, 1):
            meta = item.get("metadata", {})
            source = meta.get("file_name", "未知来源")
            source_type = meta.get("source_type", "guideline")
            content = item.get("content", "")
            if len(content) > 600:
                content = content[:600] + "..."
            lines.append(f"{i}. 【来源: {source}（{source_type}）】{content}")

        context = "\n\n".join(lines)
        logger.info(f"_retrieve_guidelines: 向量{len(vector_results)}条 + 关键词{len(keyword_results)}条 → 去重后{len(unique_results)}条, 上下文{len(context)}字符")
        return context

    except Exception as e:
        logger.warning(f"RAG 检索指南失败，将仅基于档案生成: {e}")
        return "（RAG 检索失败，请基于专业知识生成）"


def _parse_plan_json(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    从 LLM 响应中提取并解析 JSON 计划

    LLM 有时会包裹 ```json 标记，需清理。
    """
    text = raw_text.strip()
    # 去除可能的 ```json ... ``` 标记
    if text.startswith("```"):
        lines = text.split("\n")
        # 去掉首行 ```json 和末行 ```
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 尝试提取第一个 { ... } 块
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        logger.error(f"无法解析 LLM 返回的 JSON: {raw_text[:200]}")
        return None


def generate_plan(
    db: Session,
    user_id: str,
    goal_type: str = "weight_loss",
    start_date: date = None,
    end_date: date = None,
    profile_override: Dict[str, Any] = None,
    guideline_context_override: str = None,
    style_directive: str = None,
) -> Dict[str, Any]:
    """
    生成个性化饮食计划（核心入口）

    流程：
    1. 读取用户档案（或使用传入的 profile_override）
    2. 基于档案构建检索查询
    3. RAG 检索相关权威指南（或使用传入的 guideline_context_override）
    4. 调用 LLM 生成结构化计划 JSON
    5. 持久化到 Plan 表

    Args:
        db: 数据库会话
        user_id: 用户 external_id
        goal_type: 目标类型 weight_loss / muscle_gain / health_maintenance
        start_date: 计划开始日期（默认今天）
        end_date: 计划结束日期（可选）
        profile_override: 从 info_collector 传入的覆盖档案
        guideline_context_override: 从主 RAG 管道传入的指南上下文

    Returns:
        包含 plan_id 和 config 的结果字典
    """
    # 1. 读取用户档案（或使用覆盖）
    if profile_override:
        profile = profile_override
    else:
        profile = get_user_profile_dict(db, user_id)
        if not profile:
            return {"success": False, "error": f"未找到用户档案: {user_id}"}

    logger.info(f"开始为用户 {user_id} 生成饮食计划 (goal={goal_type})")

    # 3. RAG 检索权威指南（使用覆盖或内部检索）
    if guideline_context_override is not None:
        guideline_context = guideline_context_override
        logger.info(f"使用外部传入的指南上下文，长度: {len(guideline_context)} 字符")
    else:
        rag_query = _build_plan_query(profile)
        guideline_context = _retrieve_guidelines(rag_query)
        logger.info(f"RAG 检索完成，上下文长度: {len(guideline_context)} 字符")

    # 4. 调用 LLM 生成计划
    from src.core.llm_client import get_llm_client
    llm_client = get_llm_client()

    system_content = PLAN_SYSTEM_PROMPT
    if style_directive:
        system_content = system_content + "\n\n" + style_directive

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": PLAN_USER_PROMPT.format(
            user_profile=_format_user_profile(profile),
            guideline_context=guideline_context,
        )},
    ]

    raw_response = llm_client.chat(messages, temperature=0.7, max_tokens=2048)

    if not raw_response:
        return {"success": False, "error": "LLM 返回为空，无法生成计划"}

    # 5. 解析 JSON
    plan_config = _parse_plan_json(raw_response)
    if not plan_config:
        return {
            "success": False,
            "error": "LLM 返回格式无法解析为 JSON",
            "raw_response": raw_response,
        }

    # 6. 持久化到数据库（如果用户存在）
    try:
        from src.models.db_models import User
        user_exists = db.query(User).filter(User.external_id == user_id).first()
        if user_exists:
            new_plan = Plan(
                user_id=user_id,
                goal_type=goal_type,
                config=plan_config,
                start_date=start_date or date.today(),
                end_date=end_date,
                status="active",
            )
            db.add(new_plan)
            db.commit()
            db.refresh(new_plan)
            logger.info(f"计划生成成功: plan_id={new_plan.id}")
            return {
                "success": True,
                "plan_id": new_plan.id,
                "config": plan_config,
                "goal_type": goal_type,
                "start_date": str(new_plan.start_date),
                "end_date": str(new_plan.end_date) if new_plan.end_date else None,
            }
        else:
            logger.info(f"用户 {user_id} 不存在，跳过持久化，仅返回生成结果")
            return {
                "success": True,
                "plan_id": None,
                "config": plan_config,
                "goal_type": goal_type,
                "start_date": str(start_date or date.today()),
                "end_date": str(end_date) if end_date else None,
            }
    except Exception as e:
        logger.warning(f"计划持久化失败（返回生成结果）: {e}")
        return {
            "success": True,
            "plan_id": None,
            "config": plan_config,
            "goal_type": goal_type,
            "start_date": str(start_date or date.today()),
            "end_date": str(end_date) if end_date else None,
        }


# ============================================================
# 3. 计划 CRUD
# ============================================================

def list_plans(db: Session, user_id: str, status: str = None) -> Dict[str, Any]:
    """列出用户的所有计划"""
    query = db.query(Plan).filter(Plan.user_id == user_id)
    if status:
        query = query.filter(Plan.status == status)
    plans = query.order_by(Plan.created_at.desc()).all()

    return {
        "success": True,
        "count": len(plans),
        "data": [_plan_to_dict(p) for p in plans],
    }


def get_plan_detail(db: Session, user_id: str, plan_id: int) -> Dict[str, Any]:
    """获取单个计划详情"""
    plan = db.query(Plan).filter(
        Plan.id == plan_id,
        Plan.user_id == user_id,
    ).first()
    if not plan:
        return {"success": False, "error": f"未找到计划 {plan_id}"}
    return {"success": True, "data": _plan_to_dict(plan)}


def update_plan_config(
    db: Session,
    user_id: str,
    plan_id: int,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """更新计划的 config（用户在 UI 上修改后调用）"""
    plan = db.query(Plan).filter(
        Plan.id == plan_id,
        Plan.user_id == user_id,
    ).first()
    if not plan:
        return {"success": False, "error": f"未找到计划 {plan_id}"}

    plan.config = config
    db.commit()
    db.refresh(plan)

    logger.info(f"计划 {plan_id} 的 config 已更新")
    return {"success": True, "data": _plan_to_dict(plan)}


def delete_plan(db: Session, user_id: str, plan_id: int) -> Dict[str, Any]:
    """删除计划"""
    plan = db.query(Plan).filter(
        Plan.id == plan_id,
        Plan.user_id == user_id,
    ).first()
    if not plan:
        return {"success": False, "error": f"未找到计划 {plan_id}"}

    db.delete(plan)
    db.commit()
    logger.info(f"计划 {plan_id} 已删除")
    return {"success": True, "message": f"计划 {plan_id} 已删除"}


# ============================================================
# 4. 计划打卡
# ============================================================

def checkin_plan(
    db: Session,
    user_id: str,
    plan_id: int,
    checkin_date_val: date = None,
    actual_calories: float = None,
    notes: str = None,
) -> Dict[str, Any]:
    """
    计划打卡（记录当日执行情况）

    同一天同一计划只能打卡一次（数据库唯一约束保证）。
    """
    # 校验计划归属
    plan = db.query(Plan).filter(
        Plan.id == plan_id,
        Plan.user_id == user_id,
    ).first()
    if not plan:
        return {"success": False, "error": f"未找到计划 {plan_id}"}

    target_date = checkin_date_val or date.today()

    # 检查是否已打卡
    existing = db.query(PlanCheckIn).filter(
        PlanCheckIn.plan_id == plan_id,
        PlanCheckIn.checkin_date == target_date,
    ).first()
    if existing:
        return {
            "success": False,
            "error": f"计划 {plan_id} 在 {target_date} 已打卡",
            "existing_checkin_id": existing.id,
        }

    new_checkin = PlanCheckIn(
        plan_id=plan_id,
        checkin_date=target_date,
        status="completed",
        actual_calories=actual_calories,
        notes=notes,
    )
    db.add(new_checkin)
    db.commit()
    db.refresh(new_checkin)

    logger.info(f"计划 {plan_id} 打卡成功: checkin_id={new_checkin.id}")

    return {
        "success": True,
        "checkin_id": new_checkin.id,
        "plan_id": plan_id,
        "checkin_date": str(new_checkin.checkin_date),
        "actual_calories": _safe_float(new_checkin.actual_calories),
    }


def list_checkins(db: Session, user_id: str, plan_id: int) -> Dict[str, Any]:
    """列出某计划的所有打卡记录"""
    plan = db.query(Plan).filter(
        Plan.id == plan_id,
        Plan.user_id == user_id,
    ).first()
    if not plan:
        return {"success": False, "error": f"未找到计划 {plan_id}"}

    checkins = db.query(PlanCheckIn).filter(
        PlanCheckIn.plan_id == plan_id,
    ).order_by(PlanCheckIn.checkin_date.desc()).all()

    return {
        "success": True,
        "count": len(checkins),
        "data": [
            {
                "id": c.id,
                "checkin_date": str(c.checkin_date),
                "status": c.status,
                "actual_calories": _safe_float(c.actual_calories),
                "notes": c.notes,
            }
            for c in checkins
        ],
    }


# ============================================================
# 辅助函数
# ============================================================

def _plan_to_dict(plan: Plan) -> Dict[str, Any]:
    """将 Plan ORM 对象转为字典"""
    return {
        "id": plan.id,
        "user_id": plan.user_id,
        "goal_type": plan.goal_type,
        "config": plan.config,
        "start_date": str(plan.start_date) if plan.start_date else None,
        "end_date": str(plan.end_date) if plan.end_date else None,
        "status": plan.status,
        "created_at": str(plan.created_at) if plan.created_at else None,
    }
