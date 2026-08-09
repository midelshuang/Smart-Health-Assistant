"""
缓存服务模块

提供业务级缓存抽象，封装常见的缓存场景：
1. RAG 查询结果缓存（语义级命中，相同问题直接返回）
2. 嵌入向量缓存（文本 → 向量，避免重复调用 Embedding API）
3. 食物检索缓存（SQL 查询结果，按关键词命中）
4. TDEE/饮食计划缓存（用户档案不变时结果相同）

所有方法均带优雅降级：Redis 不可用时返回 None/False，不阻塞主流程。
"""

import hashlib
from typing import Any, Optional, List, Dict
from src.utils.logger import setup_logger
from src.utils.redis_client import (
    get_redis_client,
    build_key,
    KEY_PREFIX_QUERY,
    KEY_TTL_QUERY,
    KEY_PREFIX_EMBEDDING,
    KEY_TTL_EMBEDDING,
    KEY_PREFIX_FOOD_SEARCH,
    KEY_TTL_FOOD_SEARCH,
    KEY_PREFIX_TDEE,
    KEY_TTL_TDEE,
    KEY_PREFIX_PLAN,
    KEY_TTL_PLAN,
    KEY_PREFIX_SAFETY,
    KEY_TTL_SAFETY,
    clear_user_cache,
)

logger = setup_logger(name="CacheService")


# ============================================================
# 工具函数
# ============================================================

def _hash_text(text: str) -> str:
    """对文本做 MD5 哈希，用作缓存 Key（避免原文本过长）"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def _normalize_query(query: str) -> str:
    """
    查询归一化（提升缓存命中率）

    规则：
    - 去除首尾空白
    - 转小写
    - 去除多余空格
    - 去除常见标点差异（中英文标点统一）
    """
    if not query:
        return ""
    q = query.strip().lower()
    # 中英文标点统一
    table = str.maketrans({
        "？": "?", "。": ".", "，": ",", "：": ":",
        "（": "(", "）": ")", "！": "!", "；": ";",
        "“": '"', "”": '"', "'": "'",
    })
    q = q.translate(table)
    # 去多余空格
    q = " ".join(q.split())
    return q


# ============================================================
# 1. RAG 查询缓存
# ============================================================

def get_cached_query(user_query: str, user_id: str = "") -> Optional[Dict[str, Any]]:
    """
    读取 RAG 查询缓存

    Args:
        user_query: 用户原始查询
        user_id: 用户ID（不同用户的上下文不同，避免串答）

    Returns:
        缓存结果（含 answer, sources 等），未命中返回 None
    """
    client = get_redis_client()
    if not client.is_available():
        return None

    key = build_key(KEY_PREFIX_QUERY, _hash_text(_normalize_query(user_query)), user_id)
    cached = client.get_json(key)
    if cached:
        logger.info(f"查询缓存命中: query='{user_query[:30]}'")
        cached["_from_cache"] = True
        # 监控埋点：缓存命中
        try:
            from src.utils.metrics import record_cache_hit
            record_cache_hit(cache_type="query", hit=True)
        except Exception:
            pass
    else:
        # 监控埋点：缓存未命中
        try:
            from src.utils.metrics import record_cache_hit
            record_cache_hit(cache_type="query", hit=False)
        except Exception:
            pass
    return cached


def set_cached_query(
    user_query: str,
    result: Dict[str, Any],
    user_id: str = "",
) -> bool:
    """
    写入 RAG 查询缓存

    Args:
        user_query: 用户原始查询
        result: RAG 结果（answer, sources 等）
        user_id: 用户ID
    """
    client = get_redis_client()
    if not client.is_available():
        return False

    # 失败结果不缓存
    if not result.get("success", True) or result.get("error"):
        return False

    # 答案过短不缓存（避免低质量答案被复用）
    answer = result.get("answer", "")
    if len(answer) < 20:
        return False

    key = build_key(KEY_PREFIX_QUERY, _hash_text(_normalize_query(user_query)), user_id)
    # 移除不可序列化的字段
    safe_result = {k: v for k, v in result.items() if k != "_stream_prompt"}
    return client.set_json(key, safe_result, KEY_TTL_QUERY)


# ============================================================
# 2. 嵌入向量缓存
# ============================================================

def _record_cache(cache_type: str, hit: bool) -> None:
    """统一的缓存监控埋点（try/except 包裹，避免影响主流程）"""
    try:
        from src.utils.metrics import record_cache_hit
        record_cache_hit(cache_type=cache_type, hit=hit)
    except Exception:
        pass


def get_cached_embedding(text: str) -> Optional[List[float]]:
    """
    读取嵌入向量缓存

    Args:
        text: 待向量化的文本

    Returns:
        向量列表，未命中返回 None
    """
    client = get_redis_client()
    if not client.is_available():
        _record_cache("embedding", hit=False)
        return None

    key = build_key(KEY_PREFIX_EMBEDDING, _hash_text(text))
    cached = client.get_json(key)
    if cached:
        logger.debug(f"嵌入缓存命中: text='{text[:30]}'")
        _record_cache("embedding", hit=True)
    else:
        _record_cache("embedding", hit=False)
    return cached


def set_cached_embedding(text: str, embedding: List[float]) -> bool:
    """
    写入嵌入向量缓存

    Args:
        text: 文本
        embedding: 向量
    """
    client = get_redis_client()
    if not client.is_available():
        return False

    key = build_key(KEY_PREFIX_EMBEDDING, _hash_text(text))
    return client.set_json(key, embedding, KEY_TTL_EMBEDDING)


# ============================================================
# 3. 食物检索缓存（SQL）
# ============================================================

def get_cached_food_search(keyword: str, limit: int = 10) -> Optional[List[Dict[str, Any]]]:
    """
    读取食物检索缓存

    Args:
        keyword: 食物名称关键词
        limit: 限制数量

    Returns:
        食物列表，未命中返回 None
    """
    client = get_redis_client()
    if not client.is_available():
        _record_cache("food", hit=False)
        return None

    key = build_key(KEY_PREFIX_FOOD_SEARCH, _hash_text(keyword.lower()), str(limit))
    cached = client.get_json(key)
    if cached:
        logger.info(f"食物检索缓存命中: keyword='{keyword}'")
        _record_cache("food", hit=True)
    else:
        _record_cache("food", hit=False)
    return cached


def set_cached_food_search(
    keyword: str,
    results: List[Dict[str, Any]],
    limit: int = 10,
) -> bool:
    """
    写入食物检索缓存

    Args:
        keyword: 食物名称关键词
        results: 检索结果列表
        limit: 限制数量
    """
    client = get_redis_client()
    if not client.is_available():
        return False

    key = build_key(KEY_PREFIX_FOOD_SEARCH, _hash_text(keyword.lower()), str(limit))
    return client.set_json(key, results, KEY_TTL_FOOD_SEARCH)


# ============================================================
# 4. TDEE 计算缓存
# ============================================================

def get_cached_tdee(
    user_id: str,
    weight_kg: float,
    height_cm: float,
    age: int,
    gender: str,
    activity_level: str,
    goal_type: str,
    body_fat_percentage: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """读取 TDEE 计算结果缓存"""
    client = get_redis_client()
    if not client.is_available():
        _record_cache("tdee", hit=False)
        return None

    # 用输入参数构造 Key（参数相同则结果相同）
    params = f"{weight_kg}_{height_cm}_{age}_{gender}_{activity_level}_{goal_type}_{body_fat_percentage or 0}"
    key = build_key(KEY_PREFIX_TDEE, user_id, _hash_text(params))
    cached = client.get_json(key)
    if cached:
        logger.info(f"TDEE 缓存命中: user_id={user_id}")
        _record_cache("tdee", hit=True)
    else:
        _record_cache("tdee", hit=False)
    return cached


def set_cached_tdee(
    user_id: str,
    weight_kg: float,
    height_cm: float,
    age: int,
    gender: str,
    activity_level: str,
    goal_type: str,
    result: Dict[str, Any],
    body_fat_percentage: Optional[float] = None,
) -> bool:
    """写入 TDEE 计算结果缓存"""
    client = get_redis_client()
    if not client.is_available():
        return False

    params = f"{weight_kg}_{height_cm}_{age}_{gender}_{activity_level}_{goal_type}_{body_fat_percentage or 0}"
    key = build_key(KEY_PREFIX_TDEE, user_id, _hash_text(params))
    return client.set_json(key, result, KEY_TTL_TDEE)


# ============================================================
# 5. 饮食计划缓存
# ============================================================

def get_cached_plan(user_id: str, goal_type: str, daily_calories: float) -> Optional[Dict[str, Any]]:
    """读取饮食计划缓存"""
    client = get_redis_client()
    if not client.is_available():
        _record_cache("plan", hit=False)
        return None

    key = build_key(KEY_PREFIX_PLAN, user_id, goal_type, str(int(daily_calories)))
    cached = client.get_json(key)
    if cached:
        logger.info(f"计划缓存命中: user_id={user_id}")
        _record_cache("plan", hit=True)
    else:
        _record_cache("plan", hit=False)
    return cached


def set_cached_plan(
    user_id: str,
    goal_type: str,
    daily_calories: float,
    plan: Dict[str, Any],
) -> bool:
    """写入饮食计划缓存"""
    client = get_redis_client()
    if not client.is_available():
        return False

    key = build_key(KEY_PREFIX_PLAN, user_id, goal_type, str(int(daily_calories)))
    return client.set_json(key, plan, KEY_TTL_PLAN)


# ============================================================
# 6. 安全护栏 LLM 判断缓存
# ============================================================

def get_cached_safety(user_query: str) -> Optional[Dict[str, Any]]:
    """读取安全护栏判断缓存（避免相同查询重复调用 LLM）"""
    client = get_redis_client()
    if not client.is_available():
        _record_cache("safety", hit=False)
        return None

    key = build_key(KEY_PREFIX_SAFETY, _hash_text(_normalize_query(user_query)))
    cached = client.get_json(key)
    if cached:
        logger.debug(f"安全判断缓存命中: query='{user_query[:30]}'")
        _record_cache("safety", hit=True)
    else:
        _record_cache("safety", hit=False)
    return cached


def set_cached_safety(user_query: str, result: Dict[str, Any]) -> bool:
    """写入安全护栏判断缓存"""
    client = get_redis_client()
    if not client.is_available():
        return False

    key = build_key(KEY_PREFIX_SAFETY, _hash_text(_normalize_query(user_query)))
    return client.set_json(key, result, KEY_TTL_SAFETY)


# ============================================================
# 缓存管理
# ============================================================

def invalidate_user_cache(user_id: str) -> int:
    """
    清空指定用户的所有缓存（用户档案更新时调用）

    Args:
        user_id: 用户 ID

    Returns:
        删除的 Key 数量
    """
    return clear_user_cache(user_id)


def get_cache_info() -> Dict[str, Any]:
    """获取缓存总体状态（用于健康检查接口）"""
    from src.utils.redis_client import get_cache_stats
    return get_cache_stats()
