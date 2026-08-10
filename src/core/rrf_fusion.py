"""
RRF (Reciprocal Rank Fusion) 融合策略

将多路检索结果按排名倒数加权融合，得到统一排序。
适用于：
1. 多路向量检索（原 query + 改写 query + 同义词扩展）融合
2. 跨源检索（SQL 结构化 + 向量非结构化）统一排序

公式：RRF_score(d) = Σ_i 1 / (k + rank_i(d))
- k: 平滑常数（默认 60），避免 top-1 过度主导
- rank_i(d): 文档 d 在第 i 路结果中的排名（从 1 开始）
- 未出现在某路结果中的文档，该路贡献为 0

特点：
- 无需归一化分数（不同检索器分数不可比，RRF 只用排名）
- 对 outlier 不敏感
- 简单高效，工业界常用
"""

from typing import List, Dict, Any, Optional, Callable
from src.utils.logger import setup_logger

logger = setup_logger(name="RRF_Fusion")


# 默认 RRF 平滑常数
DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    result_lists: List[List[Dict[str, Any]]],
    key_fn: Callable[[Dict[str, Any]], str],
    k: int = DEFAULT_RRF_K,
    top_n: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    通用 RRF 融合函数

    Args:
        result_lists: 多路检索结果列表，每路是一个 dict 列表（按已排好序）
        key_fn: 从结果项提取唯一标识的函数（如 doc_id / content hash）
        k: RRF 平滑常数，默认 60
        top_n: 返回前 N 条，None 表示返回全部

    Returns:
        融合并排序后的结果列表，每项额外包含 rrf_score 字段
    """
    if not result_lists:
        return []

    # 累计每个文档的 RRF 分数 + 原始文档引用
    rrf_scores: Dict[str, float] = {}
    doc_store: Dict[str, Dict[str, Any]] = {}

    for list_idx, results in enumerate(result_lists):
        if not results:
            continue
        for rank, doc in enumerate(results, start=1):
            doc_key = key_fn(doc)
            if doc_key is None:
                continue

            score_contribution = 1.0 / (k + rank)
            rrf_scores[doc_key] = rrf_scores.get(doc_key, 0.0) + score_contribution

            # 保留首次出现的文档（多路结果中文档内容应一致）
            if doc_key not in doc_store:
                doc_store[doc_key] = doc.copy()

    # 按 RRF 分数降序排序
    sorted_keys = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

    if top_n is not None:
        sorted_keys = sorted_keys[:top_n]

    # 给每个文档注入 rrf_score 字段
    fused = []
    for doc_key, score in sorted_keys:
        doc = doc_store[doc_key]
        doc = doc.copy()
        doc["rrf_score"] = round(score, 6)
        # 记录该文档被多少路检索命中（用于调试/可观测）
        doc["rrf_hit_lists"] = sum(
            1 for results in result_lists
            if any(key_fn(d) == doc_key for d in results)
        )
        fused.append(doc)

    logger.info(
        f"RRF 融合: 输入 {len(result_lists)} 路, "
        f"总文档 {sum(len(r) for r in result_lists)}, "
        f"融合后 {len(fused)} 条唯一文档"
    )
    return fused


# ============================================================
# 文档 key 提取器（针对不同检索源）
# ============================================================

def vector_doc_key(doc: Dict[str, Any]) -> str:
    """
    向量文档的唯一标识：优先用 doc_id，回退到 content 哈希
    """
    if doc.get("doc_id"):
        return str(doc["doc_id"])
    content = doc.get("content", "")
    return f"vhash_{abs(hash(content))}"


def sql_doc_key(doc: Dict[str, Any]) -> str:
    """
    SQL 食物数据的唯一标识：用 food_name（数据库中已规范化）
    """
    return f"sql_{doc.get('food_name', '')}"


def unified_doc_key(doc: Dict[str, Any]) -> str:
    """
    跨源统一标识：根据 source_type 区分
    """
    source = doc.get("source_type", doc.get("data_source", ""))
    if source == "nutrition_fact" or "food_name" in doc:
        return f"sql_{doc.get('food_name', '')}"
    return vector_doc_key(doc)


# ============================================================
# 场景 1：多路向量检索融合
# ============================================================

def fuse_vector_results(
    queries_with_results: List[Dict[str, Any]],
    k: int = DEFAULT_RRF_K,
    top_n: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    对多路向量检索结果做 RRF 融合

    Args:
        queries_with_results: 列表，每项形如：
            {"query": "...", "results": [{"doc_id": ..., "content": ..., "score": ...}]}
        k: RRF 平滑常数
        top_n: 返回前 N 条

    Returns:
        融合后的结果列表
    """
    result_lists = [item.get("results", []) for item in queries_with_results]
    query_desc = " / ".join(item.get("query", "")[:30] for item in queries_with_results)
    logger.info(f"向量多路融合: queries=[{query_desc}]")

    return reciprocal_rank_fusion(
        result_lists=result_lists,
        key_fn=vector_doc_key,
        k=k,
        top_n=top_n,
    )


# ============================================================
# 场景 2：SQL + 向量跨源融合
# ============================================================

def fuse_cross_source_results(
    sql_results: List[Dict[str, Any]],
    vector_results: List[Dict[str, Any]],
    k: int = DEFAULT_RRF_K,
    top_n: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    对 SQL 食物数据 + 向量指南文档做跨源 RRF 融合

    注意：两类结果异构，融合后按 RRF 排序，但保留各自原始字段。
    用于在 generator 之前提供统一的相关性排序。

    Args:
        sql_results: SQL 检索结果
        vector_results: 向量检索结果
        k: RRF 平滑常数
        top_n: 返回前 N 条

    Returns:
        融合并排序后的结果列表（每项含 source_type 和 rrf_score）
    """
    # 给 SQL 结果打上 source_type 标记
    sql_marked = []
    for r in sql_results:
        item = r.copy()
        item["source_type"] = "nutrition_fact"
        sql_marked.append(item)

    # 给向量结果打上 source_type 标记
    vector_marked = []
    for r in vector_results:
        item = r.copy()
        if "source_type" not in item:
            meta = r.get("metadata", {})
            item["source_type"] = meta.get("source_type", "guide")
        vector_marked.append(item)

    return reciprocal_rank_fusion(
        result_lists=[sql_marked, vector_marked],
        key_fn=unified_doc_key,
        k=k,
        top_n=top_n,
    )
