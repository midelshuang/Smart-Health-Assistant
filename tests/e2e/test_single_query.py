"""快速测试单个查询的完整流程"""
import sys, os, json, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.WARNING)

from src.core.nodes import (
    _extract_age_from_query, _extract_region_from_query,
    _extract_gender_from_query, _extract_weight_from_query,
    _extract_height_from_query, _get_age_group, _get_region_info,
    _build_plan_rag_query, _build_metadata_filter, _rerank_results_by_metadata,
)
from src.repositories.vector_store import VectorStore
from src.core.embedding import get_embedding_function
from src.config import settings

query = "成都25岁男生178cm160斤减肥食养规划"
print(f"阈值: {settings.RAG_SIMILARITY_THRESHOLD}, TOP_K: {settings.RAG_TOP_K}")

age = _extract_age_from_query(query)
region = _extract_region_from_query(query)
gender = _extract_gender_from_query(query)
weight = _extract_weight_from_query(query)
height = _extract_height_from_query(query)

print(f"提取: age={age}, region={region}, gender={gender}, weight={weight}, height={height}")

age_group = _get_age_group(age)
region_info = _get_region_info(region)
print(f"映射: age→{age_group['group'] if age_group else 'N/A'}, region→{region_info['region'] if region_info else 'N/A'}")

profile = {"age": age, "region": region, "gender": gender, "weight": weight, "height": height}
if height and weight:
    profile["bmi"] = round(weight / ((height/100)**2), 1)
    print(f"BMI: {profile['bmi']}")

rag_query = _build_plan_rag_query(profile, query)
print(f"RAG查询: {rag_query}")

# 分析查询与文档的语义匹配
print(f"\n--- 语义分析 ---")
keywords_in_query = set(rag_query.replace(" ", ""))
print(f"查询字符: {set(rag_query)}")

vs = VectorStore()
vs.connect()
embedding_fn = get_embedding_function()

where_filter = {"source_type": {"$in": ["guideline", "expert_media"]}}

# 低阈值检索看有哪些文档
results_all = vs.similarity_search(query_text=rag_query, top_k=20, threshold=0.15, where=where_filter, embedding_function=embedding_fn)
print(f"\n所有命中文档 (threshold=0.15, top_k=20):")
for i, r in enumerate(results_all):
    print(f"  [{i+1}] score={r['score']:.4f} | {r['metadata'].get('file_name','N/A')} | age={r['metadata'].get('age_group','')} | region={r['metadata'].get('region','')}")

# 检查阈值0.5下的结果
results_05 = [r for r in results_all if r['score'] >= 0.5]
print(f"\n阈值0.5下的结果: {len(results_05)} 条")
if not results_05:
    scores = [r['score'] for r in results_all]
    print(f"  最高分: {max(scores):.4f}")
    print(f"  差距: 需要降低阈值或改进查询")

# 改进策略：使用原始查询直接检索
print(f"\n--- 使用原始查询检索 ---")
results_orig = vs.similarity_search(query_text=query, top_k=10, threshold=0.2, where=where_filter, embedding_function=embedding_fn)
for i, r in enumerate(results_orig):
    print(f"  [{i+1}] score={r['score']:.4f} | {r['metadata'].get('file_name','N/A')} | age={r['metadata'].get('age_group','')}")

# 改进策略：使用关键子串检索
print(f"\n--- 使用关键词检索 ---")
for kw in ["成人肥胖", "减肥", "食养", "膳食指南"]:
    r = vs.similarity_search(query_text=kw, top_k=3, threshold=0.2, where=where_filter, embedding_function=embedding_fn)
    if r:
        print(f"  '{kw}' → {len(r)}条: {[x['metadata'].get('file_name','N/A')[:20] for x in r[:2]]}")