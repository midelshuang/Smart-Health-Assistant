"""
RAG 管道节点定义

每个节点函数接收 RAGState（当前状态），返回状态的部分更新（字典）。
节点之间通过 LangGraph 的 StateGraph 编排，形成完整的 RAG 流程。

节点清单：
1. intent_recognizer_node  —— 意图识别（分流：SQL / 向量 / 混合）
2. query_rewriter_node     —— Query 改写（提升检索召回率）
3. sql_retriever_node      —— 结构化数据检索（PostgreSQL）
4. vector_retriever_node   —— 非结构化知识检索（ChromaDB 向量）
5. generator_node          —— 答案生成（LLM + 检索上下文）
6. evaluator_node          —— 检索质量评估（决定是否改写重试）

性能优化（2026-08-10）：
- 第1层：接入节点级缓存（Embedding/改写/安全/食物检索）
- 第2层：跳过不必要的 LLM 调用（简单 query 跳过改写/高质量结果跳过评估）
- 第3层：并行执行（多路 Embedding 并行计算）
"""

from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy.orm import Session
from src.models.db_models import NutritionFact
from src.config import settings
from src.utils.logger import setup_logger

logger = setup_logger(name="RAG_Nodes")


# 缓存模块（延迟导入，避免循环依赖）
def _get_cache_service():
    """获取缓存服务（延迟导入）"""
    from src.services.cache_service import (
        get_cached_embedding, set_cached_embedding,
        get_cached_food_search, set_cached_food_search,
        get_cached_safety, set_cached_safety,
        get_cached_query, set_cached_query,
    )
    return {
        "get_embedding": get_cached_embedding,
        "set_embedding": set_cached_embedding,
        "get_food": get_cached_food_search,
        "set_food": set_cached_food_search,
        "get_safety": get_cached_safety,
        "set_safety": set_cached_safety,
        "get_query": get_cached_query,
        "set_query": set_cached_query,
    }


# ============================================================
# 节点 1：意图识别
# ============================================================

# 食物关键词（命中则走 SQL 路径）
FOOD_NAME_KEYWORDS = [
    "鸡蛋", "牛奶", "米饭", "面条", "馒头", "饺子", "火锅",
    "苹果", "香蕉", "橙子", "葡萄", "西瓜", "草莓", "梨",
    "鸡肉", "猪肉", "牛肉", "羊肉", "鱼肉", "虾", "蟹",
    "米饭", "粥", "包子", "油条", "豆浆", "豆腐",
    "蔬菜", "水果", "肉", "鱼", "虾", "蟹", "豆制品",
]

FOOD_KEYWORDS = [
    "食物", "食品", "热量", "卡路里", "蛋白质", "脂肪", "碳水", "碳水化合物",
    "营养", "成分", "含量", "多少克", "大卡", "kcal",
    "升糖", "gi", "血糖",
] + FOOD_NAME_KEYWORDS

# 计划生成关键词（命中则走计划生成路径，优先级最高）
PLAN_KEYWORDS = [
    "制定计划", "生成计划", "饮食计划", "减肥计划", "减脂计划",
    "增肌计划", "健康计划", "营养计划", "给我一份计划",
    "帮我规划", "帮我制定", "安排饮食", "规划饮食",
    "生成食谱", "制定食谱",
    "饮食规划", "饮食方案", "食谱", "一日三餐", "每周食谱",
    "帮我安排", "给我安排", "餐单", "菜单",
    "规划", "减肥规划", "减脂规划", "减重规划", "增肌规划",
    "食养规划", "养生规划", "减肥食谱", "减脂食谱",
    "给我食谱", "推荐食谱", "饮食安排", "膳食计划",
]

# 指南关键词（命中则走向量检索路径）
GUIDELINE_KEYWORDS = [
    "指南", "建议", "怎么吃", "如何吃", "应该吃", "推荐",
    "肥胖", "超重", "减肥", "减脂", "减重", "瘦",
    "糖尿病", "高血压", "高血脂", "脂肪肝",
    "健康", "饮食", "食疗", "食养", "养生",
    "孕期", "产后", "儿童", "老年人",
    "运动", "锻炼", "健身",
    "能不能", "可不可以", "适合", "禁忌",
    "食谱", "方案",
]


# 追问检测关键词（AI 追问话术的特征词）
_CLARIFICATION_HINTS = [
    "请补充", "请提供", "请告知", "请告诉", "请输入",
    "为了制定", "必要信息",
    "您的年龄", "您的身高", "您的体重", "您的地区",
    "需要补充", "需要您提供",
    "补充以下",
]


def _is_reply_to_clarification(conversation_context: List[Dict[str, str]]) -> bool:
    """
    检测用户当前输入是否是对上一轮 AI 追问的补充回答。

    Args:
        conversation_context: 历史对话上下文 [{role, content}, ...]

    Returns:
        True 表示当前输入是对追问的补充
    """
    if not conversation_context:
        return False

    # 从最新往前找最近的 AI 消息
    for msg in reversed(conversation_context):
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            # 检查是否包含追问话术特征
            if any(hint in content for hint in _CLARIFICATION_HINTS):
                return True
            # 找到最近的 AI 消息但不是追问 → 不是对追问的回答
            return False
    return False


def intent_recognizer_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    意图识别节点

    分析用户原始查询，判断应该走哪条检索路径：
    - food_search  : 纯结构化数据查询（查食物营养成分）→ 仅走 SQL
    - guideline_query : 纯非结构化知识查询（查健康建议/指南）→ 仅走向量
    - hybrid : 混合查询（既要食物数据又要健康建议）→ 两条路都走
    - plan_generation : 个性化饮食计划请求 → 信息收集 + 指南检索 + 计划生成
    - unknown : 无法识别 → 默认走混合

    追问上下文检测：如果历史对话中最近的 AI 消息是追问话术，
    则强制将当前意图设为 plan_generation，确保补充信息走正确的信息收集流程。

    Args:
        state: 当前 RAGState 字典

    Returns:
        包含 intent 字段的状态更新字典
    """
    if state.get("safety_blocked", False):
        logger.info("安全护栏已拦截，跳过意图识别")
        return {"intent": "blocked"}

    original_query = state.get("original_query", "").lower()
    conversation_context = state.get("conversation_context", []) or []

    if not original_query:
        return {"intent": "unknown", "error": "查询为空"}

    # === 追问上下文检测：如果上一轮 AI 在追问，用户当前输入视为补充信息 ===
    if _is_reply_to_clarification(conversation_context):
        logger.info(f"追问上下文检测：上一轮 AI 在追问，当前输入'{original_query}'视为补充信息 → plan_generation")
        return {"intent": "plan_generation", "goal_type": state.get("goal_type", "")}

    plan_hits = sum(1 for kw in PLAN_KEYWORDS if kw in original_query)

    # 规则2: 查询包含年龄/地区/性别等个人信息 + 健康目标 → 视为计划生成
    personal_indicators = 0
    has_age = any(kw in original_query for kw in ["岁", "个月", "年龄"])
    has_gender = any(kw in original_query for kw in ["男", "女", "男性", "女性", "男生", "女生", "男孩", "女孩"])
    has_region_check = any(kw in original_query for kw in ["市", "省", "区", "县"])
    personal_indicators = sum([has_age, has_gender, has_region_check])

    health_goal_terms = ["减肥", "减脂", "减重", "瘦身", "肥胖", "超重", "增肌", "增重",
                         "糖尿病", "高血压", "高血脂", "脂肪肝", "养生", "健康", "饮食"]
    has_health_goal = any(t in original_query for t in health_goal_terms)

    if plan_hits > 0 or (personal_indicators >= 2 and has_health_goal):
        goal_type = "health_maintenance"
        if any(k in original_query for k in ["减肥", "减脂", "减重", "瘦身", "瘦", "肥胖", "超重"]):
            goal_type = "weight_loss"
        elif any(k in original_query for k in ["增肌", "增重", "健身"]):
            goal_type = "muscle_gain"
        elif any(k in original_query for k in ["糖尿病", "血糖"]):
            goal_type = "blood_sugar_control"
        elif any(k in original_query for k in ["高血压"]):
            goal_type = "blood_pressure_control"
        elif any(k in original_query for k in ["养生"]):
            goal_type = "health_maintenance"
        logger.info(f"意图识别完成: intent=plan_generation (goal_type={goal_type}, plan_hits={plan_hits}, personal={personal_indicators})")
        return {"intent": "plan_generation", "goal_type": goal_type}

    food_hits = sum(1 for kw in FOOD_KEYWORDS if kw in original_query)
    guideline_hits = sum(1 for kw in GUIDELINE_KEYWORDS if kw in original_query)

    if food_hits > 0 and guideline_hits > 0:
        intent = "hybrid"
    elif food_hits > 0:
        intent = "food_search"
    elif guideline_hits > 0:
        intent = "guideline_query"
    else:
        intent = "unknown"

    logger.info(
        f"意图识别完成: intent={intent} "
        f"(food_hits={food_hits}, guideline_hits={guideline_hits})"
    )

    return {"intent": intent}


# ============================================================
# 节点 2：Query 改写
# ============================================================

# Query 改写提示词模板
REWRITE_PROMPT = (
    "你是一个专业的搜索查询优化助手。请将用户的原始问题改写为更适合检索的形式。\n"
    "要求：\n"
    "1. 提取核心关键词，补充相关同义词\n"
    "2. 保持原意不变，消除歧义\n"
    "3. 如果是食物查询，补充常见别名\n"
    "4. 仅输出改写后的查询文本，不要解释\n\n"
    "原始问题：{query}\n"
    "改写后："
)


def query_rewriter_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Query 改写节点

    性能优化（2026-08-10）：
    1. 接入改写缓存：相同 query 第二次直接命中缓存
    2. 跳过简单查询：query < 15 字符且无生僻词时，直接用原 query，省掉 LLM 调用

    Args:
        state: 当前 RAGState 字典

    Returns:
        包含 rewritten_query 字段的状态更新字典
    """
    original_query = state.get("original_query", "")
    rewrite_count = state.get("rewrite_count", 0)

    if not original_query:
        return {"rewritten_query": "", "rewrite_count": rewrite_count}

    # ---- 第2层优化：简单查询直接跳过改写 ----
    if len(original_query) < 15:
        logger.info(f"简单查询跳过改写: '{original_query}' (len={len(original_query)})")
        return {
            "rewritten_query": original_query,
            "rewrite_count": rewrite_count + 1,
            "skip_rewrite": True,
        }

    # ---- 第1层优化：改写缓存检查 ----
    cache = _get_cache_service()
    cache_key = f"rewrite:{original_query}"
    cached = cache["get_query"](cache_key)
    if cached and cached.get("rewritten_query"):
        logger.info(f"改写缓存命中: '{original_query}' → '{cached['rewritten_query']}'")
        return {
            "rewritten_query": cached["rewritten_query"],
            "rewrite_count": rewrite_count + 1,
            "_from_cache": True,
        }

    try:
        from src.core.llm_client import get_llm_client
        llm_client = get_llm_client()

        prompt = REWRITE_PROMPT.format(query=original_query)
        rewritten = llm_client.generate(prompt, temperature=0.3, max_tokens=256)

        if rewritten and rewritten.strip():
            logger.info(f"Query 改写: '{original_query}' → '{rewritten.strip()}'")
            # 写入改写缓存
            cache["set_query"](cache_key, {"rewritten_query": rewritten.strip()})
            return {
                "rewritten_query": rewritten.strip(),
                "rewrite_count": rewrite_count + 1,
            }
    except Exception as e:
        logger.warning(f"Query 改写失败，使用原始查询: {e}")

    return {
        "rewritten_query": original_query,
        "rewrite_count": rewrite_count + 1,
    }


# ============================================================
# 节点 3：SQL 结构化数据检索
# ============================================================

def sql_retriever_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    SQL 检索节点

    性能优化（2026-08-10）：
    1. 接入食物检索缓存：相同 query 第二次直接命中缓存，跳过 SQL 查询

    Args:
        state: 当前 RAGState 字典

    Returns:
        包含 sql_results 和 sql_query 字段的状态更新字典
    """
    query = state.get("rewritten_query") or state.get("original_query", "")
    user_id = state.get("user_id", "")

    if not query:
        return {"sql_results": [], "sql_query": ""}

    # ---- 第1层优化：食物检索缓存检查 ----
    cache = _get_cache_service()
    cached_results = cache["get_food"](query.lower(), limit=20)
    if cached_results is not None:
        logger.info(f"食物检索缓存命中: query='{query}'")
        return {
            "sql_results": cached_results,
            "sql_query": f"[CACHED] {query}",
            "_from_cache": True,
        }

    from src.repositories.session import SessionLocal
    db = SessionLocal()
    try:
        keywords = [kw.strip() for kw in query.split() if kw.strip()]
        if not keywords:
            keywords = [query.strip()]

        # 中文关键词提取：对无空格的中文查询，提取食物名关键词
        # 优先使用 FOOD_NAME_KEYWORDS，再回退到滑动窗口切分
        if len(keywords) == 1 and len(keywords[0]) > 4:
            cn_query = keywords[0]
            cn_keywords = []
            # 1. 优先尝试 FOOD_NAME_KEYWORDS（食物名）
            for fkw in FOOD_NAME_KEYWORDS:
                if fkw in cn_query and fkw not in cn_keywords:
                    cn_keywords.append(fkw)
            # 2. 若仍无命中，使用滑动窗口（从长到短）尝试所有子串
            if not cn_keywords:
                for window_size in range(min(len(cn_query), 6), 1, -1):
                    for start in range(len(cn_query) - window_size + 1):
                        candidate = cn_query[start:start + window_size]
                        if candidate in ("热量", "营养", "成分", "含量", "卡路里", "kcal", "大卡", "的", "中"):
                            continue
                        if candidate not in cn_keywords:
                            cn_keywords.append(candidate)
                    if cn_keywords:
                        break
            # 3. 限制关键词数量，避免 SQL 过长
            if cn_keywords:
                keywords = cn_keywords[:5]

        conditions = []
        params = {}
        for i, kw in enumerate(keywords):
            conditions.append(f"food_name ILIKE :kw{i}")
            params[f"kw{i}"] = f"%{kw}%"

        sql_query = f"SELECT * FROM nutrition_facts WHERE {' OR '.join(conditions)} LIMIT 20"

        from sqlalchemy import text
        result = db.execute(text(sql_query), params).fetchall()

        sql_results = []
        for row in result:
            sql_results.append({
                "food_name": row.food_name,
                "category": row.category,
                "calories_per_100g": float(row.calories_per_100g or 0),
                "protein": float(row.protein or 0),
                "fat": float(row.fat or 0),
                "carbs": float(row.carbs or 0),
                "data_source": row.data_source,
            })

        logger.info(f"SQL 检索完成: 查询='{query}', 命中 {len(sql_results)} 条结果")

        # ---- 第1层优化：写入食物检索缓存 ----
        if sql_results:
            try:
                cache["set_food"](query.lower(), sql_results, limit=20)
            except Exception as e:
                logger.warning(f"食物检索缓存写入失败: {e}")

        return {
            "sql_results": sql_results,
            "sql_query": sql_query,
        }

    except Exception as e:
        logger.error(f"SQL 检索失败: {e}")
        return {"sql_results": [], "sql_query": "", "error": f"SQL 检索失败: {e}"}

    # ---- [坑点修复] db.close() 必须放在 finally 块 ----
    # 原实现 db.close() 在 try 内部，若 db.execute() 抛异常则跳过 close，
    # 导致连接泄漏 → 连接池耗尽 → 后续请求全部阻塞等待空闲连接。
    finally:
        db.close()


# ============================================================
# 节点 4：向量检索
# ============================================================

def _rerank_results_by_metadata(results: List[Dict[str, Any]], profile_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    根据用户档案信息对检索结果进行重排序
    
    提升匹配用户年龄组和地区的文档的排序
    """
    if not profile_info or not results:
        return results
    
    age = profile_info.get("age")
    region = profile_info.get("region")
    
    # 获取目标年龄组
    target_age_group = None
    if age is not None:
        age_group = _get_age_group(age)
        if age_group:
            target_age_group = age_group["group"]
    
    # 获取目标区域
    target_region = None
    if region:
        region_info = _get_region_info(region)
        if region_info["region"]:
            target_region = region_info["region"]
    
    if not target_age_group and not target_region:
        return results
    
    # 为每个结果计算元数据匹配分数
    for result in results:
        metadata = result.get("metadata", {})
        score_boost = 0.0
        
        # 年龄组匹配
        doc_age_group = metadata.get("age_group", "")
        if doc_age_group and target_age_group:
            if target_age_group in str(doc_age_group).split(","):
                score_boost += 0.1  # 年龄组匹配加分
        
        # 地区匹配
        doc_region = metadata.get("region", "")
        if doc_region and target_region:
            if target_region in str(doc_region).split(","):
                score_boost += 0.05  # 地区匹配加分
            elif "全国" in str(doc_region):
                score_boost += 0.02  # 全国文档少量加分
        
        # 应用分数提升
        if score_boost > 0:
            result["score"] = min(result.get("score", 0) + score_boost, 1.0)
    
    # 按分数重新排序
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    
    return results


def _build_metadata_filter(
    profile_info: Dict[str, Any],
    intent: str,
    selected_guidelines: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    两阶段检索阶段2 的元数据过滤条件。

    plan_generation 且 selected_guidelines 非空时：
        (source_type=guideline AND guideline_title ∈ selected) OR (source_type=expert_media)
        —— expert_media chunk 无 guideline_title 字段，须 $or 单独放行，否则被误排除。
        guide_catalog chunk 带 guideline_title 且标题 ∈ selected 时自然入选（决策A 向量补摘要）。
    其他情况退化为 source_type 基础过滤。
    """
    base_types = ["guideline", "expert_media", "gi_data"]
    if intent == "plan_generation" and selected_guidelines:
        return {
            "$or": [
                {"$and": [
                    {"source_type": "guideline"},
                    {"guideline_title": {"$in": selected_guidelines}},
                ]},
                {"source_type": "expert_media"},
            ]
        }
    return {"source_type": {"$in": base_types}}


def _keyword_search(
    collection,
    query: str,
    top_k: int = 10,
    where_filter: Optional[Dict] = None,
) -> List[Dict[str, Any]]:
    """
    基于 PostgreSQL 的关键词检索（BM25 风格）

    优先使用 PostgreSQL 全文检索（高效、支持复杂过滤），
    降级时回退到 ChromaDB 内存过滤。
    """
    if not query or not query.strip():
        return []

    try:
        from src.repositories.chunk_repository import keyword_search as pg_keyword_search
        results = pg_keyword_search(
            query=query,
            top_k=top_k,
            where_filter=where_filter,
        )
        if results:
            logger.info(f"PostgreSQL 关键词检索命中: {len(results)} 条")
            return results
        logger.info("PostgreSQL 关键词检索无结果，回退到 ChromaDB")
    except Exception as e:
        logger.warning(f"PostgreSQL 关键词检索失败，回退到 ChromaDB: {e}")

    return _keyword_search_fallback(collection, query, top_k, where_filter)


def _keyword_search_fallback(
    collection,
    query: str,
    top_k: int = 10,
    where_filter: Optional[Dict] = None,
) -> List[Dict[str, Any]]:
    """
    ChromaDB 降级关键词检索（兼容性回退）
    """
    try:
        import re

        kwargs = {"include": ["documents", "metadatas"]}
        if where_filter:
            kwargs["where"] = where_filter

        all_data = collection.get(**kwargs)
        if not all_data or not all_data["ids"]:
            return []

        query_lower = query.lower()
        keywords = set()
        for token in re.split(r'\s+', query_lower):
            if len(token) >= 2:
                keywords.add(token)
            if len(token) >= 2:
                for i in range(len(token) - 1):
                    keywords.add(token[i:i+2])

        scored_docs = []
        for i, doc_id in enumerate(all_data["ids"]):
            doc = all_data["documents"][i] or ""
            meta = all_data["metadatas"][i] or {}
            doc_lower = doc.lower()

            score = 0.0
            for kw in keywords:
                if kw in doc_lower:
                    freq = doc_lower.count(kw)
                    score += freq * 0.1

            file_name = meta.get("file_name", "").lower()
            for kw in keywords:
                if kw in file_name:
                    score += 0.5

            doc_age = meta.get("age_group", "")
            if doc_age:
                for kw in keywords:
                    if kw in str(doc_age).lower():
                        score += 0.3

            if score > 0:
                scored_docs.append({
                    "id": doc_id,
                    "content": doc,
                    "score": min(score, 1.0),
                    "metadata": meta,
                })

        scored_docs.sort(key=lambda x: x["score"], reverse=True)
        return scored_docs[:top_k]

    except Exception as e:
        logger.warning(f"ChromaDB 降级关键词检索失败: {e}")
        return []


def vector_retriever_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    向量检索节点（向量语义 + 关键词匹配 混合检索 + RRF 融合）

    性能优化（2026-08-10）：
    1. 接入 Embedding 缓存：相同 query 的向量第二次直接命中缓存
    2. 第3层优化：Embedding 计算前先查缓存，减少 API 调用

    Args:
        state: 当前 RAGState 字典

    Returns:
        包含 vector_results 字段的状态更新字典
    """
    original_query = state.get("original_query", "")
    rewritten_query = state.get("rewritten_query") or original_query
    intent = state.get("intent", "unknown")
    profile_info = state.get("user_profile_info", {})

    if not rewritten_query:
        return {"vector_results": []}

    try:
        from src.repositories.vector_store import VectorStore
        from src.core.embedding import get_embedding_function
        from src.core.rrf_fusion import fuse_vector_results

        vector_store = VectorStore()
        vector_store.connect()
        embedding_fn = get_embedding_function()
        vector_store.get_or_create_collection(embedding_function=embedding_fn)
        collection = vector_store.collection

        # ---- 第1层优化：Embedding 缓存 + 预计算 ----
        cache = _get_cache_service()

        def get_cached_embedding(text: str) -> List[float]:
            """带缓存的 query embedding 计算"""
            cached = cache["get_embedding"](text)
            if cached is not None:
                logger.debug(f"Embedding 缓存命中: '{text[:30]}'")
                return cached
            result = embedding_fn.embed_query(text)
            cache["set_embedding"](text, result)
            return result

        # 构建过滤条件（两阶段检索阶段2：用 selected_guidelines 锁定指南）
        where_filter = None
        selected_guidelines = state.get("selected_guidelines") or []
        if intent == "plan_generation" and profile_info:
            where_filter = _build_metadata_filter(profile_info, intent, selected_guidelines)
            logger.info(f"计划生成阶段2: where={where_filter}, selected_guidelines={selected_guidelines}")
        elif intent == "guideline_query":
            where_filter = {"source_type": {"$in": ["guideline", "expert_media"]}}

        # ---- 路径 1: 向量语义检索 ----
        vector_multi_queries = [rewritten_query]
        if original_query and original_query != rewritten_query:
            vector_multi_queries.append(original_query)

        logger.info(f"向量多路检索: {len(vector_multi_queries)} 路, queries={vector_multi_queries}")

        # ---- 第3层优化：多路 Embedding 并行计算 ----
        def compute_embedding_for_query(q: str) -> tuple:
            """为单个 query 计算带缓存的 embedding"""
            emb = get_cached_embedding(q)
            return (q, emb)

        # 使用线程池并行计算多路 embedding（最多 4 并发）
        query_embeddings = {}
        with ThreadPoolExecutor(max_workers=min(4, len(vector_multi_queries))) as executor:
            futures = {
                executor.submit(compute_embedding_for_query, q): q
                for q in vector_multi_queries
            }
            for future in as_completed(futures):
                q, emb = future.result()
                query_embeddings[q] = emb

        # 使用预计算的 embedding 进行检索
        vector_multi_results = []
        for q in vector_multi_queries:
            q_embedding = query_embeddings.get(q)
            results = vector_store.similarity_search(
                query_embedding=q_embedding,
                top_k=settings.RAG_TOP_K,
                threshold=settings.RAG_SIMILARITY_THRESHOLD,
                where=where_filter,
            )
            vector_multi_results.append({"query": q, "results": results})

        if len(vector_multi_results) == 1:
            vector_results = vector_multi_results[0]["results"]
        else:
            vector_results = fuse_vector_results(
                queries_with_results=vector_multi_results,
                k=60,
                top_n=settings.RAG_TOP_K,
            )
        logger.info(f"向量语义检索完成: 命中 {len(vector_results)} 条")

        # ---- 路径 2: 关键词检索（补充） ----
        keyword_query = original_query if original_query else rewritten_query
        keyword_results = _keyword_search(
            collection,
            query=keyword_query,
            top_k=settings.RAG_TOP_K,
            where_filter=where_filter,
        )
        logger.info(f"关键词检索完成: 命中 {len(keyword_results)} 条")

        # ---- RRF 融合两路结果 ----
        if keyword_results:
            all_results = [
                {"query": f"向量检索({rewritten_query})", "results": vector_results},
                {"query": f"关键词检索({keyword_query})", "results": keyword_results},
            ]
            vector_results = fuse_vector_results(
                queries_with_results=all_results,
                k=60,
                top_n=settings.RAG_TOP_K,
            )
            logger.info(f"向量+关键词混合RRF融合完成: 融合后 {len(vector_results)} 条")

        # 根据用户档案信息重排序
        if profile_info and vector_results and (intent == "plan_generation" or intent == "guideline_query"):
            vector_results = _rerank_results_by_metadata(vector_results, profile_info)
            logger.info(f"元数据重排序完成: 已根据用户档案调整排序 (intent={intent})")
        
        return {"vector_results": vector_results}

    except Exception as e:
        logger.error(f"向量检索失败: {e}")
        return {"vector_results": [], "error": f"向量检索失败: {e}"}


# ============================================================
# 节点 5：答案生成
# ============================================================

# 系统提示词 - 知识库有结果时使用
SYSTEM_PROMPT_KB = (
    "你是一个专业的健康饮食助手。你的回答必须：\n"
    "1. 优先基于提供的参考资料回答，不要编造信息\n"
    "2. 回答中如果引用了资料，请标注来源文件名\n"
    "3. 如果资料不足以完全回答问题，可以补充通用健康知识，但需标注「⚠️ 以下为AI通用知识，非知识库数据」\n"
    "4. 使用中文回答，语言简洁专业\n"
    "5. 对于食物营养查询，以表格形式展示更清晰\n"
)

# 系统提示词 - 知识库无结果时使用（LLM 生成）
SYSTEM_PROMPT_LLM = (
    "你是一个专业的健康饮食助手。当前知识库中没有找到与用户问题直接匹配的资料。\n"
    "你的回答必须：\n"
    "1. 基于你的通用健康知识回答用户问题\n"
    "2. 在回答开头标注「📌 本回答由AI根据通用知识生成，非知识库检索结果」\n"
    "3. 明确告知用户该信息未经知识库验证，建议参考专业指南\n"
    "4. 使用中文回答，语言简洁专业\n"
)

# 生成提示词模板 - 知识库有结果
GENERATE_PROMPT_KB = (
    "【用户问题】\n{query}\n\n"
    "【参考资料 - 结构化数据】\n{sql_context}\n\n"
    "【参考资料 - 非结构化知识】\n{vector_context}\n\n"
    "请基于以上资料回答用户问题。"
    "如果引用了参考资料中的内容，请在回答末尾附上来源。"
)

# 生成提示词模板 - 知识库无结果
GENERATE_PROMPT_LLM = (
    "【用户问题】\n{query}\n\n"
    "知识库中未检索到与该问题匹配的资料。"
    "请基于你的通用健康知识回答用户问题，"
    "并在回答开头标注「📌 本回答由AI根据通用知识生成，非知识库检索结果」。"
)


def generator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    答案生成节点

    性能优化（2026-08-10）：
    1. 接入节点级耗时日志，定位 LLM 调用瓶颈

    策略：
    1. 优先使用知识库检索结果（SQL + 向量）生成答案，标注来源
    2. 如果知识库无结果，使用 LLM 通用知识回答，标注「AI生成」
    3. 如果知识库结果不充分，混合回答并区分标注

    Args:
        state: 当前 RAGState 字典

    Returns:
        包含 generated_answer、sources 和 answer_source 字段的状态更新字典
    """
    import time as _time
    _t_node_start = _time.perf_counter()

    query = state.get("original_query", "")
    sql_results = state.get("sql_results", [])
    vector_results = state.get("vector_results", [])
    feedback_directive = state.get("feedback_style_directive", "")
    intent = state.get("intent", "unknown")

    has_kb_data = bool(sql_results or vector_results)
    answer_source = "knowledge_base" if has_kb_data else "llm_generated"

    # ---- 阶段1：上下文格式化 ----
    _t_fmt_start = _time.perf_counter()
    sql_context = _format_sql_context(sql_results)
    vector_context = _format_vector_context(vector_results)
    _fmt_ms = (_time.perf_counter() - _t_fmt_start) * 1000

    # 构建来源列表
    sources = []
    for r in vector_results:
        meta = r.get("metadata", {})
        sources.append({
            "file_name": meta.get("file_name", "未知来源"),
            "source_type": meta.get("source_type", "unknown"),
            "source_label": _source_type_label(meta.get("source_type", "unknown")),
            "score": str(r.get("score", 0)),
        })
    for r in sql_results:
        sources.append({
            "file_name": r.get("food_name", "未知食物"),
            "source_type": "nutrition_fact",
            "source_label": "营养成分表",
            "score": "1.0",
        })

    # 根据知识库结果选择不同的提示词
    if has_kb_data:
        system_prompt = SYSTEM_PROMPT_KB
        if feedback_directive:
            system_prompt = system_prompt + "\n\n" + feedback_directive
        prompt = GENERATE_PROMPT_KB.format(
            query=query,
            sql_context=sql_context,
            vector_context=vector_context,
        )
    else:
        system_prompt = SYSTEM_PROMPT_LLM
        if feedback_directive:
            system_prompt = system_prompt + "\n\n" + feedback_directive
        prompt = GENERATE_PROMPT_LLM.format(query=query)
        sources.append({
            "file_name": "AI 通用知识",
            "source_type": "llm_generated",
            "source_label": "AI生成（非知识库）",
            "score": "-",
        })

    _prompt_len = len(prompt) + len(system_prompt)
    logger.info(
        f"⏱️ [GENERATOR-START] intent={intent} | 来源={answer_source} | "
        f"SQL={len(sql_results)}条 向量={len(vector_results)}条 | "
        f"上下文格式化={_fmt_ms:.1f}ms | prompt长度={_prompt_len}字符"
    )

    # 流式模式：跳过 LLM 调用，由 pipeline 的 stream_answer 接管生成
    if state.get("stream_mode", False):
        logger.info("流式模式: 跳过 generator_node 的 LLM 调用")
        if feedback_directive:
            system_prompt = system_prompt + "\n\n" + feedback_directive
        return {
            "generated_answer": "",
            "sources": sources,
            "answer_source": answer_source,
            "_stream_prompt": prompt,
            "_stream_system_prompt": system_prompt,
        }

    # ---- 阶段2：LLM 调用（主要耗时点）----
    try:
        from src.core.llm_client import get_llm_client
        llm_client = get_llm_client()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        _t_llm_start = _time.perf_counter()
        answer = llm_client.chat(messages, temperature=0.7, max_tokens=settings.LLM_MAX_TOKENS)
        _llm_ms = (_time.perf_counter() - _t_llm_start) * 1000

        _total_ms = (_time.perf_counter() - _t_node_start) * 1000
        logger.info(
            f"⏱️ [GENERATOR-END] 总耗时={_total_ms:.0f}ms | LLM调用={_llm_ms:.0f}ms | "
            f"上下文格式化={_fmt_ms:.1f}ms | 答案长度={len(answer)}字符 | intent={intent}"
        )
        return {
            "generated_answer": answer,
            "sources": sources,
            "answer_source": answer_source,
        }

    except Exception as e:
        logger.error(f"答案生成失败: {e}")

        fallback = _fallback_answer(sql_results, vector_results, query, has_kb_data)
        return {
            "generated_answer": fallback,
            "sources": sources,
            "answer_source": answer_source,
            "error": f"LLM 生成失败，使用降级方案: {e}",
        }


# ============================================================
# 节点 6：评估节点（决定是否重试）
# ============================================================

def evaluator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    评估节点

    性能优化（2026-08-10）：
    1. 跳过高质量结果评估：当 top-3 分数均 > 0.7 时，直接通过，省掉 LLM 调用

    评估标准：
    1. SQL 结果或向量结果至少有一项非空
    2. 向量结果的相似度分数是否达到阈值
    3. 改写次数是否已达上限

    Args:
        state: 当前 RAGState 字典

    Returns:
        包含 eval_passed 和 eval_feedback 字段的状态更新字典
    """
    sql_results = state.get("sql_results", [])
    vector_results = state.get("vector_results", [])
    rewrite_count = state.get("rewrite_count", 0)
    max_rewrite = state.get("max_rewrite_count", settings.RAG_MAX_REWRITE)
    intent = state.get("intent", "unknown")

    # ---- 第2层优化：高质量结果直接跳过评估 ----
    if vector_results and len(vector_results) >= 3:
        top3_scores = [r.get("score", 0) for r in vector_results[:3]]
        if all(s > 0.7 for s in top3_scores):
            logger.info(
                f"高质量结果跳过评估: top3_scores={[round(s, 3) for s in top3_scores]}"
            )
            return {
                "eval_passed": True,
                "eval_feedback": "高质量结果，跳过评估",
                "skip_eval": True,
            }

    eval_passed = True
    feedback = ""

    if intent in ("food_search", "hybrid") and not sql_results:
        eval_passed = False
        feedback = "SQL 检索未命中任何食物数据"

    if intent in ("guideline_query", "hybrid"):
        if not vector_results:
            eval_passed = False
            feedback = "向量检索未命中任何指南文档"
        else:
            low_score_count = sum(
                1 for r in vector_results
                if r.get("score", 0) < settings.RAG_SIMILARITY_THRESHOLD
            )
            if low_score_count == len(vector_results):
                eval_passed = False
                feedback = f"所有向量结果相似度均低于阈值 ({settings.RAG_SIMILARITY_THRESHOLD})"

    if not eval_passed and rewrite_count >= max_rewrite:
        eval_passed = True
        feedback += f"；已达最大改写次数 ({max_rewrite})，不再重试"

    logger.info(
        f"评估完成: passed={eval_passed}, feedback={feedback}, "
        f"rewrite_count={rewrite_count}/{max_rewrite}"
    )

    return {
        "eval_passed": eval_passed,
        "eval_feedback": feedback,
    }


# ============================================================
# 辅助函数
# ============================================================

def _source_type_label(source_type: str) -> str:
    """将 source_type 转为中文标签"""
    mapping = {
        "guideline": "权威指南",
        "expert_experience": "专家经验",
        "nutrition_fact": "营养成分表",
        "gi_data": "GI数据",
        "llm_generated": "AI生成（非知识库）",
        "unknown": "未知来源",
    }
    return mapping.get(source_type, source_type)


def _format_sql_context(sql_results: List[Dict]) -> str:
    if not sql_results:
        return "（无匹配的结构化食物数据）"

    lines = ["以下是从营养成分表中检索到的相关食物数据："]
    for i, item in enumerate(sql_results[:10], 1):
        lines.append(
            f"{i}. {item['food_name']} ({item.get('category', '')}): "
            f"热量={item['calories_per_100g']}kcal/100g, "
            f"蛋白质={item['protein']}g, "
            f"脂肪={item['fat']}g, "
            f"碳水={item['carbs']}g"
        )
    return "\n".join(lines)


def _format_vector_context(vector_results: List[Dict]) -> str:
    if not vector_results:
        return "（无匹配的指南文献内容）"

    lines = ["以下是从权威指南中检索到的相关内容片段："]
    for i, item in enumerate(vector_results[:5], 1):
        meta = item.get("metadata", {})
        source = meta.get("file_name", "未知来源")
        content = item.get("content", "")
        if len(content) > 300:
            content = content[:300] + "..."
        lines.append(f"{i}. 【来源: {source}】{content}")
    return "\n\n".join(lines)


def _fallback_answer(sql_results, vector_results, query, has_kb_data=True):
    if not has_kb_data:
        return (
            f"📌 本回答由AI根据通用知识生成，非知识库检索结果\n\n"
            f"关于「{query}」：\n"
            f"知识库中暂无与此问题匹配的资料。基于AI通用知识，"
            f"建议您参考相关专业医学营养指南，或咨询专业营养师。\n\n"
            f"⚠️ 本信息未经知识库验证，仅供参考。"
        )

    parts = [f"关于「{query}」的查询结果：\n"]

    if sql_results:
        parts.append("📊 食物营养数据：")
        for item in sql_results[:5]:
            parts.append(
                f"  • {item['food_name']}: "
                f"热量 {item['calories_per_100g']}kcal/100g, "
                f"蛋白 {item['protein']}g, 脂肪 {item['fat']}g, 碳水 {item['carbs']}g"
            )

    if vector_results:
        parts.append("\n📖 相关指南：")
        for item in vector_results[:3]:
            meta = item.get("metadata", {})
            parts.append(f"  • 来源: {meta.get('file_name', '未知')} (相关度: {item.get('score', 0)})")

    if len(parts) == 1:
        parts.append("抱歉，未能找到相关信息，请尝试更具体的查询。")

    return "\n".join(parts)


# ============================================================
# 节点 0：安全护栏（入口检查，在意图识别之前）
# ============================================================

def safety_check_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    安全护栏节点

    性能优化（2026-08-10）：
    1. 接入安全缓存：相同 query 第二次直接命中缓存，跳过正则+LLM双重校验

    Args:
        state: 当前 RAGState 字典

    Returns:
        包含 safety_blocked 字段的状态更新字典
    """
    from src.core.safety_checker import check_safety, build_safety_reply

    query = state.get("original_query", "")
    logger.info(f"安全护栏检查: query='{query[:50]}'")

    # ---- 第1层优化：安全缓存检查 ----
    cache = _get_cache_service()
    cached_safety = cache["get_safety"](query)
    if cached_safety is not None:
        logger.info(f"安全缓存命中: query='{query[:50]}', blocked={cached_safety.get('is_safe')}")
        if not cached_safety.get("is_safe", True):
            reply = cached_safety.get("reply", "")
            return {
                "safety_blocked": True,
                "safety_category": cached_safety.get("category", "other"),
                "safety_reason": cached_safety.get("reason", ""),
                "generated_answer": reply,
                "sources": [],
                "intent": "blocked",
                "_from_cache": True,
            }
        return {
            "safety_blocked": False,
            "safety_category": "none",
            "_from_cache": True,
        }

    # ========== 前置放行：计划生成类请求 ==========
    # 减肥/减脂/增肌/健身计划是健康生活咨询，不属于医疗行为
    # 在这里快速识别并放行，避免被 LLM 语义校验误判
    plan_kw = [
        "减肥计划", "减脂计划", "增肌计划", "健身计划", "饮食计划",
        "减肥方案", "减脂方案", "健身方案", "增肌方案",
        "体重管理", "帮我制定", "帮我规划", "生成计划", "制定计划",
        "减肥", "减脂", "减重", "瘦身",
    ]
    if any(kw in query for kw in plan_kw):
        logger.info(f"前置放行: 命中计划生成关键词，query='{query[:50]}'")
        # 写入安全缓存（放行结果）
        cache["set_safety"](query, {"is_safe": True, "category": "plan_gen"})
        return {
            "safety_blocked": False,
            "safety_category": "none",
        }

    # ========== 前置放行：食物营养查询类请求 ==========
    # 查询食物热量/蛋白质/脂肪等营养成分是纯数据查询，不涉及医疗建议
    # 在这里快速识别并放行，跳过 LLM 慢审（可省 16 秒）
    food_nutrition_kw = [
        "热量", "蛋白质", "脂肪", "碳水", "碳水化合物", "营养", "营养成分",
        "卡路里", "大卡", "kcal", "含量", "多少克", "升糖", "gi", "血糖",
        "膳食纤维", "维生素", "矿物质", "钠", "钙", "铁", "锌",
    ]
    if any(kw in query for kw in food_nutrition_kw):
        logger.info(f"前置放行: 命中食物营养关键词，query='{query[:50]}'")
        cache["set_safety"](query, {"is_safe": True, "category": "food_nutrition"})
        return {
            "safety_blocked": False,
            "safety_category": "none",
        }

    # 执行双重校验
    safety_result = check_safety(query, use_llm=True)

    if not safety_result.get("is_safe", True):
        # 命中拦截
        reply = build_safety_reply(safety_result, query)
        logger.warning(
            f"安全拦截触发: category={safety_result.get('category')}, "
            f"method={safety_result.get('method')}"
        )
        # 写入安全缓存（拦截结果）
        cache["set_safety"](query, {
            "is_safe": False,
            "category": safety_result.get("category", "other"),
            "reason": safety_result.get("reason", ""),
            "reply": reply,
        })
        return {
            "safety_blocked": True,
            "safety_category": safety_result.get("category", "other"),
            "safety_reason": safety_result.get("reason", ""),
            "generated_answer": reply,
            "sources": [],
            "intent": "blocked",
        }

    # 通过安全校验
    logger.info("安全校验通过")
    # 写入安全缓存（放行结果）
    cache["set_safety"](query, {"is_safe": True, "category": "none"})
    return {
        "safety_blocked": False,
        "safety_category": "none",
    }


# ============================================================
# 节点 6.4：追问节点（ClarificationNode）
# ============================================================

# 追问生成提示词
CLARIFICATION_PROMPT = (
    "你是一个专业的健康饮食助手。用户的问题在指南库中匹配度较低，"
    "可能是表述模糊或缺少关键细节。\n"
    "请基于用户原始问题，生成一个温和的追问，帮助用户补充必要信息。\n\n"
    "要求：\n"
    "1. 仅输出一句话的追问，不要解释或重复用户问题\n"
    "2. 针对食物查询，可询问具体种类、份量或场景\n"
    "3. 针对健康建议，可询问具体身体状况、目标或限制\n"
    "4. 语气友好专业，避免让用户感到被质疑\n\n"
    "触发原因：{reason}\n"
    "用户问题：{query}\n"
    "追问："
)

# 默认追问话术（LLM 不可用时降级）
DEFAULT_CLARIFICATION = (
    "您的问题在指南库中匹配度较低，能否补充更多细节？"
    "例如：具体食物种类、份量、用餐场景或个人健康目标。"
)


def clarification_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    追问节点（ClarificationNode）

    当向量检索结果质量较差（最高相似度低于 CLARIFICATION_THRESHOLD）时触发，
    由 LLM 生成针对性追问话术，引导用户补充关键信息，而非强行猜测回答。

    触发条件：
    1. 意图属于 guideline_query 或 hybrid（需要向量检索的意图）
    2. 且 vector_results 为空，或最高相似度 < RAG_CLARIFICATION_THRESHOLD

    不触发的情况：
    - food_search 意图（纯 SQL 查询，不依赖向量相似度）
    - plan_generation 意图（计划生成不依赖检索质量）
    - 向量检索结果质量良好（最高相似度 >= 阈值）

    Args:
        state: 当前 RAGState 字典

    Returns:
        - 触发追问：needs_clarification=True + clarification_question + generated_answer
        - 不触发：needs_clarification=False（继续到 fusion）
    """
    vector_results = state.get("vector_results", []) or []
    intent = state.get("intent", "unknown")
    original_query = state.get("original_query", "")

    # 只有需要向量检索的意图才考虑追问
    if intent not in ("guideline_query", "hybrid", "unknown"):
        logger.info(f"追问节点跳过: intent={intent} 不依赖向量检索")
        return {"needs_clarification": False}

    # 检查是否需要追问
    threshold = settings.RAG_CLARIFICATION_THRESHOLD
    needs_clarify = False
    reason = ""

    if not vector_results:
        needs_clarify = True
        reason = "向量检索未命中任何指南文档"
    else:
        max_score = max((r.get("score", 0) for r in vector_results), default=0.0)
        if max_score < threshold:
            needs_clarify = True
            reason = f"最高相似度 {max_score:.2f} 低于阈值 {threshold}"

    if not needs_clarify:
        logger.info("追问节点跳过: 向量检索质量良好，无需追问")
        return {"needs_clarification": False}

    # 用 LLM 生成追问话术
    question = DEFAULT_CLARIFICATION
    try:
        from src.core.llm_client import get_llm_client
        llm = get_llm_client()
        prompt = CLARIFICATION_PROMPT.format(query=original_query, reason=reason)
        generated = llm.generate(prompt, temperature=0.5, max_tokens=128).strip()
        if generated:
            question = generated
    except Exception as e:
        logger.warning(f"追问生成失败，使用默认话术: {e}")

    logger.info(f"触发追问: reason={reason}, question={question[:50]}...")

    return {
        "needs_clarification": True,
        "clarification_question": question,
        "generated_answer": question,  # 直接作为最终回复
        "sources": [],
    }


# ============================================================
# 节点 6.5：RRF 融合（SQL + 向量跨源统一排序）
# ============================================================

def fusion_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    RRF 融合节点

    在 evaluator 通过之后、generator 之前执行：
    - 输入：sql_results + vector_results（两路异构检索结果）
    - 处理：调用 RRF 融合算法，按统一相关性排序
    - 输出：fused_results（按 RRF 分数排序的统一列表）

    融合后的结果保留各自原始字段，但增加 rrf_score 字段。
    generator 可优先使用 fused_results 中的 top-K，提升答案质量。

    Args:
        state: 当前 RAGState 字典

    Returns:
        包含 fused_results 字段的状态更新字典
    """
    from src.core.rrf_fusion import fuse_cross_source_results

    sql_results = state.get("sql_results", []) or []
    vector_results = state.get("vector_results", []) or []

    # 若两路都为空，跳过融合
    if not sql_results and not vector_results:
        logger.info("RRF 融合跳过: 两路检索结果均为空")
        return {"fused_results": []}

    # 若只有一路有结果，无需融合
    if not sql_results or not vector_results:
        logger.info(
            f"RRF 融合跳过: 仅单路有结果 (sql={len(sql_results)}, vector={len(vector_results)})"
        )
        # 合并到 fused_results 但不排序
        merged = []
        for r in sql_results:
            item = r.copy()
            item["source_type"] = "nutrition_fact"
            item["rrf_score"] = 0.0
            merged.append(item)
        for r in vector_results:
            item = r.copy()
            if "source_type" not in item:
                meta = r.get("metadata", {})
                item["source_type"] = meta.get("source_type", "guide")
            item["rrf_score"] = 0.0
            merged.append(item)
        return {"fused_results": merged}

    # 两路都有结果，执行 RRF 融合
    try:
        fused = fuse_cross_source_results(
            sql_results=sql_results,
            vector_results=vector_results,
            k=60,
            top_n=10,  # 取 top 10 喂给 generator
        )
        logger.info(
            f"RRF 跨源融合完成: sql={len(sql_results)}, vector={len(vector_results)}, "
            f"融合后 {len(fused)} 条"
        )
        return {"fused_results": fused}

    except Exception as e:
        logger.error(f"RRF 融合异常: {e}，降级为简单合并")
        merged = []
        for r in sql_results:
            item = r.copy()
            item["source_type"] = "nutrition_fact"
            merged.append(item)
        for r in vector_results:
            merged.append(r.copy())
        return {"fused_results": merged}


# ============================================================
# 节点 7：计划生成（plan_generation 意图专属）
# ============================================================

def plan_generator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    个性化饮食计划生成节点

    当意图识别为 plan_generation 时执行：
    1. 从 state 读取 user_id 和 goal_type
    2. 调用 plan_service.generate_plan 生成计划
       （内部会读取用户档案 → RAG 检索指南 → LLM 生成 JSON）
    3. 将生成的 plan_config 写入 state
    4. 将计划摘要格式化为 generated_answer，供聊天窗口展示

    Returns:
        包含 plan_config 和 generated_answer 的状态更新字典
    """
    user_id = state.get("user_id", "") or "guest_user"
    goal_type = state.get("goal_type", "health_maintenance")
    original_query = state.get("original_query", "")
    plan_style_directive = state.get("plan_style_directive", "")

    try:
        from src.repositories.session import SessionLocal
        from src.services.plan_service import generate_plan, get_user_profile_dict

        try:
            db = SessionLocal()
        except Exception as e:
            logger.error(f"数据库连接失败: {e}")
            return {
                "plan_config": {},
                "generated_answer": "数据库连接失败，无法生成计划。请稍后重试或联系管理员。",
                "sources": [],
                "error": str(e),
            }

        try:
            # 1. 读取用户档案（用于生成回答时展示）
            profile = get_user_profile_dict(db, user_id) if user_id and user_id != "guest_user" else None

            # 2. 使用 info_collector 收集的信息（优先）或档案信息
            user_profile_info = state.get("user_profile_info", {})
            if user_profile_info:
                if not profile:
                    profile = {}
                for key in ("age", "gender", "weight", "height", "region", "bmi", "province"):
                    if key in user_profile_info and user_profile_info.get(key) is not None:
                        profile[key] = user_profile_info[key]
                if user_profile_info.get("health_goal"):
                    profile["health_goal"] = user_profile_info["health_goal"]
                if user_profile_info.get("health_tags"):
                    profile["health_tags"] = user_profile_info["health_tags"]

            if not profile or not any(profile.get(k) for k in ("age", "gender", "weight", "height")):
                return {
                    "plan_config": {},
                    "generated_answer": (
                        "未找到足够的用户信息来生成饮食计划。"
                        "请在查询中提供年龄、性别、身高、体重等信息。"
                    ),
                    "sources": [],
                }

            # 3. 使用 state 中已检索的向量结果（来自主 RAG 管道）
            vector_results = state.get("vector_results", [])
            guideline_context = ""
            if vector_results:
                lines = []
                for i, item in enumerate(vector_results[:8], 1):
                    meta = item.get("metadata", {})
                    source = meta.get("file_name", "未知来源")
                    source_type = meta.get("source_type", "guideline")
                    content = item.get("content", "")
                    if len(content) > 600:
                        content = content[:600] + "..."
                    lines.append(f"{i}. 【来源: {source}（{source_type}）】{content}")
                guideline_context = "\n\n".join(lines)
                logger.info(f"plan_generator: 使用主 RAG 管道的 {len(vector_results)} 条检索结果，上下文长度 {len(guideline_context)} 字符")
            else:
                # 回退：内部 RAG 检索
                logger.info("plan_generator: 主 RAG 无结果，回退到内部检索")
                from src.services.plan_service import _retrieve_guidelines, _build_plan_query
                rag_query = _build_plan_query(profile)
                guideline_context = _retrieve_guidelines(rag_query)

            # 4. 调用 generate_plan（已封装 LLM + 持久化）
            result = generate_plan(db, user_id=user_id, goal_type=goal_type,
                                   profile_override=profile,
                                   guideline_context_override=guideline_context,
                                   style_directive=plan_style_directive or None)
        finally:
            db.close()

        if not result.get("success", False):
            return {
                "plan_config": {},
                "generated_answer": f"计划生成失败: {result.get('error', '未知错误')}",
                "sources": [],
            }

        plan_config = result.get("config", {})
        plan_id = result.get("plan_id")

        # 3. 将计划格式化为聊天窗口可展示的文本
        answer = _format_plan_for_chat(plan_config, plan_id, goal_type)

        # 3.1 追加软提示（地区缺失/疾病默认无等，不阻断流程，由 info_collector 产出）
        soft = state.get("soft_clarifications") or []
        if soft and answer:
            answer = answer + "\n\n💡 补充提示：\n- " + "\n- ".join(soft)

        # 4. 收集来源信息
        sources = state.get("sources", [])
        if not sources and vector_results:
            sources = []
            for v in vector_results[:5]:
                meta = v.get("metadata", {})
                sources.append({
                    "file_name": meta.get("file_name", "未知"),
                    "source_type": meta.get("source_type", "guideline"),
                    "source_label": "知识库",
                })

        return {
            "plan_config": plan_config,
            "generated_answer": answer,
            "sources": sources,
            "answer_source": "knowledge_base",
        }

    except Exception as e:
        logger.error(f"计划生成节点异常: {e}")
        return {
            "plan_config": {},
            "generated_answer": f"计划生成异常: {e}",
            "sources": [],
            "error": str(e),
        }


def _format_plan_for_chat(plan_config: Dict[str, Any], plan_id: int, goal_type: str) -> str:
    """将 plan_config 格式化为聊天窗口可读的文本"""
    parts = []

    summary = plan_config.get("summary", "")
    target_cal = plan_config.get("target_calories", "")
    parts.append(f"🎯 已为您生成饮食计划（计划ID: {plan_id}，目标: {goal_type}）\n")
    if summary:
        parts.append(f"📋 {summary}")
    if target_cal:
        parts.append(f"每日目标热量: {target_cal} kcal")

    macros = plan_config.get("macros", {})
    if macros:
        parts.append(
            f"宏量营养素: 蛋白质 {macros.get('protein_g', '?')}g, "
            f"脂肪 {macros.get('fat_g', '?')}g, "
            f"碳水 {macros.get('carbs_g', '?')}g"
        )

    meals = plan_config.get("meals", [])
    if meals:
        parts.append("\n🍽️ 每日餐次安排:")
        meal_name_map = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}
        for meal in meals:
            meal_type = meal_name_map.get(meal.get("meal_type", ""), meal.get("meal_type", ""))
            time = meal.get("time", "")
            parts.append(f"\n  [{meal_type}] {time}")
            for item in meal.get("items", []):
                parts.append(
                    f"    • {item.get('food', '?')} "
                    f"{item.get('quantity', '')} "
                    f"({item.get('calories', '?')} kcal)"
                )
            if meal.get("total_calories"):
                parts.append(f"    小计: {meal['total_calories']} kcal")

    advice = plan_config.get("advice", "")
    if advice:
        parts.append(f"\n💡 建议: {advice}")

    precautions = plan_config.get("precautions", "")
    if precautions:
        parts.append(f"⚠️ 注意: {precautions}")

    parts.append(
        "\n\n您可以在「饮食计划」模块查看并修改这份计划，"
        "或调用 PUT /api/v1/plans/{plan_id} 更新计划内容。"
    )

    return "\n".join(parts)


# ============================================================
# 节点 7：信息收集（info_collector）
# ============================================================

# 地区关键词映射（城市/省份 → 省份）
REGION_KEYWORDS = {
    # ============================================================
    # 直辖市
    # ============================================================
    "北京": "北京", "上海": "上海", "天津": "天津", "重庆": "重庆",

    # ============================================================
    # 江苏省 (13地级市)
    # ============================================================
    "南京": "江苏", "苏州": "江苏", "无锡": "江苏", "常州": "江苏",
    "徐州": "江苏", "南通": "江苏", "扬州": "江苏", "镇江": "江苏",
    "盐城": "江苏", "淮安": "江苏", "连云港": "江苏", "泰州": "江苏",
    "宿迁": "江苏",

    # ============================================================
    # 浙江省 (11地级市)
    # ============================================================
    "杭州": "浙江", "宁波": "浙江", "温州": "浙江", "嘉兴": "浙江",
    "绍兴": "浙江", "金华": "浙江", "台州": "浙江", "湖州": "浙江",
    "丽水": "浙江", "舟山": "浙江", "衢州": "浙江",

    # ============================================================
    # 广东省 (21地级市)
    # ============================================================
    "广州": "广东", "深圳": "广东", "东莞": "广东", "佛山": "广东",
    "珠海": "广东", "中山": "广东", "惠州": "广东", "汕头": "广东",
    "湛江": "广东", "江门": "广东", "肇庆": "广东", "茂名": "广东",
    "阳江": "广东", "清远": "广东", "韶关": "广东", "揭阳": "广东",
    "潮州": "广东", "梅州": "广东", "河源": "广东", "云浮": "广东",
    "汕尾": "广东",

    # ============================================================
    # 四川省 (21地级市/州)
    # ============================================================
    "成都": "四川", "绵阳": "四川", "德阳": "四川", "乐山": "四川",
    "宜宾": "四川", "南充": "四川", "自贡": "四川", "泸州": "四川",
    "内江": "四川", "达州": "四川", "广元": "四川", "遂宁": "四川",
    "广安": "四川", "巴中": "四川", "雅安": "四川", "眉山": "四川",
    "资阳": "四川", "攀枝花": "四川", "凉山": "四川", "阿坝": "四川",
    "甘孜": "四川",

    # ============================================================
    # 湖北省 (13地级市/州)
    # ============================================================
    "武汉": "湖北", "宜昌": "湖北", "襄阳": "湖北", "黄冈": "湖北",
    "荆州": "湖北", "十堰": "湖北", "荆门": "湖北", "孝感": "湖北",
    "咸宁": "湖北", "黄石": "湖北", "鄂州": "湖北", "随州": "湖北",
    "恩施": "湖北",

    # ============================================================
    # 湖南省 (14地级市/州)
    # ============================================================
    "长沙": "湖南", "株洲": "湖南", "湘潭": "湖南", "衡阳": "湖南",
    "岳阳": "湖南", "常德": "湖南", "郴州": "湖南", "永州": "湖南",
    "邵阳": "湖南", "益阳": "湖南", "怀化": "湖南", "娄底": "湖南",
    "张家界": "湖南", "湘西": "湖南",

    # ============================================================
    # 陕西省 (10地级市)
    # ============================================================
    "西安": "陕西", "咸阳": "陕西", "宝鸡": "陕西", "渭南": "陕西",
    "延安": "陕西", "榆林": "陕西", "汉中": "陕西", "安康": "陕西",
    "商洛": "陕西", "铜川": "陕西",

    # ============================================================
    # 河南省 (17地级市 + 济源)
    # ============================================================
    "郑州": "河南", "洛阳": "河南", "开封": "河南", "南阳": "河南",
    "新乡": "河南", "信阳": "河南", "安阳": "河南", "濮阳": "河南",
    "焦作": "河南", "许昌": "河南", "漯河": "河南", "商丘": "河南",
    "周口": "河南", "驻马店": "河南", "平顶山": "河南", "三门峡": "河南",
    "鹤壁": "河南", "济源": "河南",

    # ============================================================
    # 山东省 (16地级市)
    # ============================================================
    "济南": "山东", "青岛": "山东", "烟台": "山东", "潍坊": "山东",
    "淄博": "山东", "威海": "山东", "临沂": "山东", "济宁": "山东",
    "泰安": "山东", "日照": "山东", "德州": "山东", "聊城": "山东",
    "滨州": "山东", "菏泽": "山东", "枣庄": "山东", "东营": "山东",

    # ============================================================
    # 福建省 (9地级市)
    # ============================================================
    "厦门": "福建", "福州": "福建", "泉州": "福建", "漳州": "福建",
    "莆田": "福建", "三明": "福建", "龙岩": "福建", "南平": "福建",
    "宁德": "福建",

    # ============================================================
    # 辽宁省 (14地级市)
    # ============================================================
    "沈阳": "辽宁", "大连": "辽宁", "鞍山": "辽宁", "抚顺": "辽宁",
    "丹东": "辽宁", "锦州": "辽宁", "营口": "辽宁", "阜新": "辽宁",
    "辽阳": "辽宁", "盘锦": "辽宁", "铁岭": "辽宁", "朝阳": "辽宁",
    "葫芦岛": "辽宁", "本溪": "辽宁",

    # ============================================================
    # 吉林省 (9地级市/州)
    # ============================================================
    "长春": "吉林", "吉林市": "吉林", "四平": "吉林", "延边": "吉林",
    "通化": "吉林", "白山": "吉林", "白城": "吉林", "松原": "吉林",
    "辽源": "吉林",

    # ============================================================
    # 黑龙江省 (13地级市/地区)
    # ============================================================
    "哈尔滨": "黑龙江", "齐齐哈尔": "黑龙江", "大庆": "黑龙江",
    "牡丹江": "黑龙江", "佳木斯": "黑龙江", "绥化": "黑龙江",
    "鸡西": "黑龙江", "鹤岗": "黑龙江", "双鸭山": "黑龙江",
    "伊春": "黑龙江", "七台河": "黑龙江", "黑河": "黑龙江",
    "大兴安岭": "黑龙江",

    # ============================================================
    # 安徽省 (16地级市)
    # ============================================================
    "合肥": "安徽", "芜湖": "安徽", "蚌埠": "安徽", "黄山": "安徽",
    "安庆": "安徽", "马鞍山": "安徽", "阜阳": "安徽", "宿州": "安徽",
    "滁州": "安徽", "六安": "安徽", "宣城": "安徽", "池州": "安徽",
    "亳州": "安徽", "铜陵": "安徽", "淮南": "安徽", "淮北": "安徽",

    # ============================================================
    # 河北省 (11地级市)
    # ============================================================
    "石家庄": "河北", "唐山": "河北", "保定": "河北", "秦皇岛": "河北",
    "邯郸": "河北", "张家口": "河北", "廊坊": "河北", "沧州": "河北",
    "邢台": "河北", "衡水": "河北", "承德": "河北",

    # ============================================================
    # 山西省 (11地级市)
    # ============================================================
    "太原": "山西", "大同": "山西", "运城": "山西", "临汾": "山西",
    "长治": "山西", "晋城": "山西", "忻州": "山西", "吕梁": "山西",
    "阳泉": "山西", "朔州": "山西", "晋中": "山西",

    # ============================================================
    # 江西省 (11地级市)
    # ============================================================
    "南昌": "江西", "九江": "江西", "赣州": "江西", "景德镇": "江西",
    "吉安": "江西", "上饶": "江西", "抚州": "江西", "宜春": "江西",
    "萍乡": "江西", "新余": "江西", "鹰潭": "江西",

    # ============================================================
    # 云南省 (16地级市/州)
    # ============================================================
    "昆明": "云南", "大理": "云南", "丽江": "云南", "西双版纳": "云南",
    "曲靖": "云南", "玉溪": "云南", "昭通": "云南", "保山": "云南",
    "普洱": "云南", "临沧": "云南", "红河": "云南", "文山": "云南",
    "楚雄": "云南", "德宏": "云南", "迪庆": "云南", "怒江": "云南",

    # ============================================================
    # 贵州省 (9地级市/州)
    # ============================================================
    "贵阳": "贵州", "遵义": "贵州", "六盘水": "贵州", "安顺": "贵州",
    "毕节": "贵州", "铜仁": "贵州", "黔东南": "贵州", "黔南": "贵州",
    "黔西南": "贵州",

    # ============================================================
    # 广西壮族自治区 (14地级市)
    # ============================================================
    "南宁": "广西", "桂林": "广西", "柳州": "广西", "北海": "广西",
    "梧州": "广西", "钦州": "广西", "贵港": "广西", "玉林": "广西",
    "百色": "广西", "贺州": "广西", "河池": "广西", "来宾": "广西",
    "崇左": "广西", "防城港": "广西",

    # ============================================================
    # 海南省 (4地级市)
    # ============================================================
    "海口": "海南", "三亚": "海南", "三沙": "海南", "儋州": "海南",

    # ============================================================
    # 甘肃省 (14地级市/州)
    # ============================================================
    "兰州": "甘肃", "天水": "甘肃", "酒泉": "甘肃", "白银": "甘肃",
    "庆阳": "甘肃", "平凉": "甘肃", "定西": "甘肃", "陇南": "甘肃",
    "武威": "甘肃", "张掖": "甘肃", "金昌": "甘肃", "嘉峪关": "甘肃",
    "临夏": "甘肃", "甘南": "甘肃",

    # ============================================================
    # 内蒙古自治区 (12地级市/盟)
    # ============================================================
    "呼和浩特": "内蒙古", "包头": "内蒙古", "鄂尔多斯": "内蒙古",
    "赤峰": "内蒙古", "通辽": "内蒙古", "呼伦贝尔": "内蒙古",
    "巴彦淖尔": "内蒙古", "乌兰察布": "内蒙古", "乌海": "内蒙古",
    "兴安盟": "内蒙古", "锡林郭勒": "内蒙古", "阿拉善": "内蒙古",

    # ============================================================
    # 新疆维吾尔自治区 (14地级市/州/地区)
    # ============================================================
    "乌鲁木齐": "新疆", "喀什": "新疆", "克拉玛依": "新疆", "吐鲁番": "新疆",
    "哈密": "新疆", "阿克苏": "新疆", "和田": "新疆", "塔城": "新疆",
    "阿勒泰": "新疆", "克州": "新疆", "博州": "新疆", "昌吉": "新疆",
    "伊犁": "新疆", "石河子": "新疆",

    # ============================================================
    # 宁夏回族自治区 (5地级市)
    # ============================================================
    "银川": "宁夏", "石嘴山": "宁夏", "吴忠": "宁夏", "固原": "宁夏",
    "中卫": "宁夏",

    # ============================================================
    # 青海省 (8地级市/州/地区)
    # ============================================================
    "西宁": "青海", "海东": "青海", "海北": "青海", "黄南": "青海",
    "海南州": "青海", "果洛": "青海", "玉树": "青海", "海西": "青海",

    # ============================================================
    # 西藏自治区 (7地级市/地区)
    # ============================================================
    "拉萨": "西藏", "日喀则": "西藏", "昌都": "西藏", "林芝": "西藏",
    "山南": "西藏", "那曲": "西藏", "阿里": "西藏",

    # ============================================================
    # 港澳台
    # ============================================================
    "香港": "香港", "澳门": "澳门", "台北": "台湾", "高雄": "台湾",
    "台中": "台湾", "台南": "台湾",
}

# 省份 → 区域映射（用于选择对应的地区饮食指南）
PROVINCE_TO_REGION = {
    # 华东地区
    "上海": "华东", "江苏": "华东", "浙江": "华东", "安徽": "华东",
    "福建": "华东", "江西": "华东", "山东": "华东", "台湾": "华东",
    # 华北地区
    "北京": "华北", "天津": "华北", "河北": "华北", "山西": "华北",
    "内蒙古": "华北",
    # 华南地区
    "广东": "华南", "广西": "华南", "海南": "华南", "香港": "华南",
    "澳门": "华南",
    # 华中地区
    "河南": "华中", "湖北": "华中", "湖南": "华中",
    # 西南地区
    "重庆": "西南", "四川": "西南", "贵州": "西南", "云南": "西南",
    "西藏": "西南",
    # 西北地区
    "陕西": "西北", "甘肃": "西北", "青海": "西北", "宁夏": "西北",
    "新疆": "西北",
    # 东北地区
    "辽宁": "东北", "吉林": "东北", "黑龙江": "东北",
}

# 区域 → 地区饮食指南关键词映射
REGION_GUIDELINE_KEYWORDS = {
    "华东": ["江南", "华东", "江浙沪", "长三角", "江南地区"],
    "华北": ["华北", "北方", "京津塘", "华北地区"],
    "华南": ["华南", "岭南", "广东", "粤式", "华南地区"],
    "华中": ["华中", "两湖", "华中地区"],
    "西南": ["西南", "巴蜀", "川渝", "西南地区"],
    "西北": ["西北", "秦晋", "西北地区"],
    "东北": ["东北", "关外", "东北地区"],
    "江南": ["江南", "江南地区"],
    "南方": ["南方", "江南", "岭南"],
    "北方": ["北方", "华北", "关外"],
}

# 年龄 → 群体映射（用于选择对应的指南类型）
AGE_GROUP_MAPPING = {
    "infant": {"min": 0, "max": 2, "label": "婴幼儿", "guideline_keywords": ["婴幼儿喂养指南", "婴儿膳食指南", "婴幼儿营养"]},
    "preschool": {"min": 3, "max": 5, "label": "学龄前儿童", "guideline_keywords": ["学龄前儿童膳食指南", "学龄前儿童营养"]},
    "child": {"min": 6, "max": 12, "label": "学龄儿童", "guideline_keywords": ["学龄儿童膳食指南", "儿童膳食指南", "儿童肥胖食养指南"]},
    "adolescent": {"min": 13, "max": 17, "label": "青少年", "guideline_keywords": ["儿童青少年肥胖食养指南", "儿童青少年膳食指南", "青少年营养"]},
    "youth": {"min": 18, "max": 35, "label": "青年", "guideline_keywords": ["中国居民膳食指南", "成人肥胖食养指南", "青年膳食指南"]},
    "middle_aged": {"min": 36, "max": 59, "label": "中年", "guideline_keywords": ["中国居民膳食指南", "中老年人营养指南", "慢病饮食"]},
    "middle_aged_plus": {"min": 60, "max": 64, "label": "中老年", "guideline_keywords": ["中老年人营养指南", "老年人膳食指南"]},
    "elderly": {"min": 65, "max": 120, "label": "老年人", "guideline_keywords": ["老年人膳食指南", "老年人营养"]},
}

# 性别关键词
GENDER_KEYWORDS = {
    "男": "male", "男性": "male", "男生": "male", "男孩": "male",
    "女": "female", "女性": "female", "女生": "female", "女孩": "female",
}


def _get_age_group(age: int) -> Optional[Dict[str, Any]]:
    """根据年龄返回群体信息"""
    if age is None:
        return None
    for group_key, group_info in AGE_GROUP_MAPPING.items():
        if group_info["min"] <= age <= group_info["max"]:
            return {
                "group": group_key,
                "label": group_info["label"],
                "guideline_keywords": group_info["guideline_keywords"],
            }
    return None


def _get_region_info(province: str) -> Dict[str, Any]:
    """根据省份返回区域信息"""
    if not province:
        return {"region": None, "guideline_keywords": []}
    region = PROVINCE_TO_REGION.get(province)
    keywords = REGION_GUIDELINE_KEYWORDS.get(region, []) if region else []
    return {
        "province": province,
        "region": region,
        "guideline_keywords": keywords,
    }


def _extract_age_from_query(query: str) -> Optional[int]:
    """从用户查询中提取年龄"""
    import re
    patterns = [
        r'(\d{1,2})\s*岁',
        r'(\d{1,2})\s*岁男孩',
        r'(\d{1,2})\s*岁女孩',
        r'(\d{1,2})\s*岁男',
        r'(\d{1,2})\s*岁女',
        r'(\d{1,2})\s*个月',
        # 追问补充时用户可能只输入数字（如 "25"），作为兜底模式
        r'^(\d{1,2})$',
    ]
    for pattern in patterns:
        match = re.search(pattern, query.strip())
        if match:
            age = int(match.group(1))
            if 0 < age < 120:
                return age
    return None


def _extract_region_from_query(query: str) -> Optional[str]:
    """从用户查询中提取地区"""
    for kw, province in REGION_KEYWORDS.items():
        if kw in query:
            return province
    return None


def _extract_gender_from_query(query: str) -> Optional[str]:
    """从用户查询中提取性别"""
    for kw, gender in GENDER_KEYWORDS.items():
        if kw in query:
            return gender
    return None


def _extract_weight_from_query(query: str) -> Optional[float]:
    """从用户查询中提取体重"""
    import re
    # 先尝试 "kg/公斤/千克" 等明确单位
    match = re.search(r'(\d{2,3}(?:\.\d+)?)\s*(?:kg|公斤|千克|kilo)', query)
    if match:
        weight = float(match.group(1))
        if 20 < weight < 300:
            return weight
    # 再尝试单独的 "斤"（避免匹配 "公斤"/"千克" 中的 "斤"）
    match = re.search(r'(\d{2,3}(?:\.\d+)?)\s*斤(?!\s*[克/公/千])', query)
    if match:
        weight = float(match.group(1)) / 2.0
        if 20 < weight < 300:
            return weight
    return None


def _extract_height_from_query(query: str) -> Optional[float]:
    """从用户查询中提取身高"""
    import re
    match = re.search(r'(\d{2,3}(?:\.\d+)?)\s*(?:cm|厘米|公分|米)', query)
    if match:
        height = float(match.group(1))
        if '米' in query and height < 3:
            height = height * 100
        if 50 < height < 250:
            return height
    return None


def _build_plan_rag_query(profile_info: Dict[str, Any], original_query: str) -> str:
    """基于用户信息构建精确的 RAG 检索查询（自然语言风格，提升 embedding 质量）"""
    parts = []
    age = profile_info.get("age")
    gender = profile_info.get("gender")
    region = profile_info.get("region")
    weight = profile_info.get("weight")
    height = profile_info.get("height")
    health_goal = profile_info.get("health_goal")
    health_tags = profile_info.get("health_tags", [])
    bmi = profile_info.get("bmi")

    # 1. 构建自然语言查询（比关键词堆砌更利于 embedding）
    query_parts = []

    # 年龄 + 性别构成主体
    if age is not None:
        age_group = _get_age_group(age)
        if age_group:
            query_parts.append(age_group["label"])

    if gender == "female":
        query_parts.append("女性")
    elif gender == "male":
        query_parts.append("男性")

    # 健康目标作为核心意图
    if health_goal:
        goal_map = {
            "weight_loss": "减肥饮食",
            "muscle_gain": "增肌饮食",
            "health_maintenance": "健康饮食",
            "blood_sugar_control": "糖尿病饮食",
            "blood_pressure_control": "高血压饮食",
        }
        mapped = goal_map.get(health_goal, "")
        if mapped:
            query_parts.append(mapped)

    # 地区
    if region:
        region_info = _get_region_info(region)
        if region_info["region"]:
            query_parts.append(region_info["region"] + "膳食")
        query_parts.append(region + "饮食")

    # 关键指南类型
    if age is not None:
        age_group = _get_age_group(age)
        if age_group:
            for kw in age_group["guideline_keywords"]:
                query_parts.append(kw)

    if health_goal == "weight_loss" and bmi and bmi >= 24:
        query_parts.append("肥胖食养指南")
        query_parts.append("减重科学方法")

    # 疾病名（where 已按 selected_guidelines 锁定指南，这里用疾病名定位该指南内食谱内容）
    diseases = profile_info.get("diseases") or []
    for d in diseases:
        query_parts.append(f"{d}饮食")
    # 兜底：diseases 为空时从原始查询补疾病关键词
    if not diseases:
        if "糖尿病" in original_query or "血糖" in original_query:
            query_parts.append("糖尿病饮食")
        if "高血压" in original_query:
            query_parts.append("高血压饮食")
    if "养生" in original_query:
        query_parts.append("养生膳食")

    if not query_parts:
        query_parts.append("健康饮食指南")

    seen = set()
    unique_parts = []
    for p in query_parts:
        if p not in seen:
            seen.add(p)
            unique_parts.append(p)

    return " ".join(unique_parts)


def _extract_diseases_from_query(query: str) -> List[str]:
    """从用户查询中提取疾病 disease_tag 列表。"""
    from src.core.guideline_registry import extract_disease_tags
    return extract_disease_tags(query or "")


def _accumulate_profile_from_context(
    conversation_context: List[Dict[str, str]],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """
    多轮累积（解决 guest 无 DB profile 的多轮追问信息丢失）：
    扫描历史 user 消息，补全 age/height/weight/region/gender/disease。
    仅在提取值非 None 且当前 profile 中不存在（或为 None）时才写入。
    当前轮已提取的值优先（由调用方保证先于上下文累积写入）。
    """
    if not conversation_context:
        return profile
    for msg in conversation_context:
        if msg.get("role") != "user":
            continue
        text = msg.get("content", "")
        if not text:
            continue
        # 仅在 profile 中不存在或为 None 时才填充，且提取值必须非 None
        for key, extract_fn in [
            ("age", _extract_age_from_query),
            ("height", _extract_height_from_query),
            ("weight", _extract_weight_from_query),
            ("region", _extract_region_from_query),
            ("gender", _extract_gender_from_query),
        ]:
            if key not in profile or profile.get(key) is None:
                val = extract_fn(text)
                if val is not None:
                    profile[key] = val
        for tag in _extract_diseases_from_query(text):
            diseases = profile.setdefault("diseases", [])
            if tag not in diseases:
                diseases.append(tag)
    return profile


def info_collector_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    信息收集节点 —— 收集用户年龄、地区、性别、身高、体重、疾病等信息

    流程：
    1. 从用户查询中提取可用信息（年龄、地区、性别、身高、体重、疾病）
    2. 尝试从数据库读取用户档案（含 health_tags/medical_conditions）
    3. guest 无档案时，从对话上下文多轮累积
    4. 调 select_guidelines_by_profile 选指南（阶段1）
    5. 分级追问：年龄/身高/体重缺失→硬阻断；地区/疾病缺失→软提示

    Returns:
        更新后的状态字典
    """
    from src.core.guideline_registry import (
        select_guidelines_by_profile,
        normalize_diseases,
        get_diseases_without_guideline_pdf,
    )

    user_id = state.get("user_id", "")
    original_query = state.get("original_query", "")
    ctx = state.get("conversation_context", []) or []

    profile_info: Dict[str, Any] = {}

    # 1. 从查询中提取信息（含疾病）
    if (a := _extract_age_from_query(original_query)) is not None:
        profile_info["age"] = a
    if (r := _extract_region_from_query(original_query)):
        profile_info["region"] = r
    if (g := _extract_gender_from_query(original_query)):
        profile_info["gender"] = g
    if (w := _extract_weight_from_query(original_query)) is not None:
        profile_info["weight"] = w
    if (h := _extract_height_from_query(original_query)) is not None:
        profile_info["height"] = h
    q_diseases = _extract_diseases_from_query(original_query)
    if q_diseases:
        profile_info["diseases"] = q_diseases

    # 2. 从数据库读取用户档案
    db_profile = None
    if user_id and user_id != "guest":
        try:
            from src.repositories.session import SessionLocal
            from src.services.plan_service import get_user_profile_dict
            db = SessionLocal()
            try:
                db_profile = get_user_profile_dict(db, user_id)
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"读取用户档案失败: {e}")

    # 3. 合并：DB 档案为基础，查询提取优先
    if db_profile:
        profile_info.setdefault("age", db_profile.get("age"))
        profile_info.setdefault("gender", db_profile.get("gender"))
        profile_info.setdefault("weight", db_profile.get("weight"))
        profile_info.setdefault("height", db_profile.get("height"))
        profile_info.setdefault("health_goal", db_profile.get("health_goal"))
        profile_info.setdefault("health_tags", db_profile.get("health_tags", []))
        profile_info.setdefault("bmi", db_profile.get("bmi"))
        profile_info.setdefault("region", db_profile.get("province"))
        if db_profile.get("active_goal"):
            profile_info.setdefault("active_goal", db_profile["active_goal"])
        # 🆕 疾病来源：medical_conditions(V4 JSONB) + health_tags
        mc = db_profile.get("medical_conditions")
        mc_list = mc if isinstance(mc, list) else []
        db_diseases = normalize_diseases(
            (db_profile.get("health_tags") or []) + mc_list
        )
        if db_diseases:
            existing = profile_info.setdefault("diseases", [])
            for d in db_diseases:
                if d not in existing:
                    existing.append(d)

    # 3.1 guest 无 DB 档案：从对话上下文多轮累积
    if not db_profile:
        profile_info = _accumulate_profile_from_context(ctx, profile_info)

    # 4. goal_type → health_goal（来自意图识别器）
    goal_type = state.get("goal_type", "")
    if goal_type and not profile_info.get("health_goal"):
        profile_info["health_goal"] = goal_type
        logger.info(f"info_collector: 将 goal_type={goal_type} 映射为 health_goal")
    if not profile_info.get("health_goal"):
        profile_info["health_goal"] = "health_maintenance"  # 默认目标，不阻断

    # 5. BMI
    h = profile_info.get("height")
    w = profile_info.get("weight")
    if h and w and h > 0:
        profile_info["bmi"] = round(w / ((h / 100) ** 2), 1)

    # 6. 阶段1：选指南（决策A）
    diseases = profile_info.get("diseases") or []
    profile_info["diseases"] = diseases  # 确保键存在
    selected = select_guidelines_by_profile(profile_info)
    profile_info["selected_guidelines"] = selected

    # 7. 分级追问（决策B）
    soft: List[str] = []
    missing_hard: List[str] = []
    if profile_info.get("age") is None:
        missing_hard.append("年龄")
    if profile_info.get("height") is None:
        missing_hard.append("身高")
    if profile_info.get("weight") is None:
        missing_hard.append("体重")
    if not profile_info.get("region"):
        soft.append("所在地区（如：南京，可跳过，将按通用食谱生成）")
    if not diseases:
        soft.append("未识别到您的慢性病信息，按一般人群处理；若有糖尿病/高血压/高尿酸/肾病等请告知")
    else:
        for d in get_diseases_without_guideline_pdf(diseases):
            soft.append(f"您提到「{d}」，暂无对应食养指南，将参考《中国居民膳食指南》基础并提示注意事项")

    # 8. 构建 RAG 检索查询
    rag_query = _build_plan_rag_query(profile_info, original_query)
    logger.info(f"info_collector: 构建 RAG 查询: {rag_query}")

    state_updates: Dict[str, Any] = {
        "user_profile_info": profile_info,
        "info_collected": True,
        "profile_used": db_profile is not None,
        "rewritten_query": rag_query,
        "selected_guidelines": selected,
        "diseases": diseases,
        "soft_clarifications": soft,
    }

    # 9. 硬阻断：年龄/身高/体重 缺失 → END 追问
    if missing_hard:
        q_parts = []
        if "年龄" in missing_hard:
            q_parts.append("您的年龄是多少？")
        if "身高" in missing_hard:
            q_parts.append("您的身高(cm)？")
        if "体重" in missing_hard:
            q_parts.append("您的体重(kg)？")
        clar = (
            "为了制定精准的饮食计划，请补充以下必要信息：\n\n"
            + "\n".join(q_parts)
            + "\n\n（您也可以一次性提供，例如：28岁 男 175cm 80kg 想减肥）"
        )
        state_updates["needs_clarification"] = True
        state_updates["clarification_question"] = clar
        state_updates["generated_answer"] = clar
        state_updates["info_collected"] = False
        logger.info(f"info_collector: 硬阻断追问 ({missing_hard})")
        return state_updates

    logger.info(f"info_collector: 信息收集完成, selected={selected}, diseases={diseases}, soft={soft}")
    return state_updates
