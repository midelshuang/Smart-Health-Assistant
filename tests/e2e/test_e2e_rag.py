"""
端到端集成测试 - 定位RAG流程所有断点
一次性测试多个场景，暴露所有问题
"""
import sys, os, json, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING)

from src.core.nodes import (
    _extract_age_from_query,
    _extract_region_from_query,
    _extract_gender_from_query,
    _extract_weight_from_query,
    _extract_height_from_query,
    _get_age_group,
    _get_region_info,
    _build_plan_rag_query,
    _build_metadata_filter,
    _rerank_results_by_metadata,
    FOOD_NAME_KEYWORDS,
)
from src.repositories.vector_store import VectorStore
from src.core.embedding import get_embedding_function
from src.config import settings

TEST_QUERIES = [
    {
        "name": "青年男性减肥",
        "query": "成都25岁男生178cm160斤减肥食养规划",
    },
    {
        "name": "青少年肥胖",
        "query": "南京15岁男孩肥胖饮食规划",
    },
    {
        "name": "中年女性糖尿病",
        "query": "郑州50岁女性糖尿病饮食",
    },
    {
        "name": "青年男性减重",
        "query": "广州35岁男性减重",
    },
    {
        "name": "老年养生",
        "query": "哈尔滨70岁老人养生",
    },
]

print("=" * 70)
print(f"RAG配置: THRESHOLD={settings.RAG_SIMILARITY_THRESHOLD}, TOP_K={settings.RAG_TOP_K}")
print("=" * 70)

# 初始化向量库
vs = VectorStore()
vs.connect()
embedding_fn = get_embedding_function()
collection = vs.get_or_create_collection(embedding_function=embedding_fn)

# 查看库中文档数
stats = collection.count()
print(f"向量库文档总数: {stats}")

# 获取所有文档元数据概览
all_docs = collection.get(include=["metadatas"])
source_types = {}
for m in all_docs["metadatas"]:
    st = m.get("source_type", "unknown")
    source_types[st] = source_types.get(st, 0) + 1
print(f"来源类型分布: {source_types}")

print("\n" + "=" * 70)
print("逐个查询测试")
print("=" * 70)

for test in TEST_QUERIES:
    print(f"\n{'='*70}")
    print(f"测试: {test['name']}")
    print(f"查询: {test['query']}")
    print(f"{'='*70}")
    
    q = test["query"]
    
    # Step 1: 信息提取
    age = _extract_age_from_query(q)
    region = _extract_region_from_query(q)
    gender = _extract_gender_from_query(q)
    weight = _extract_weight_from_query(q)
    height = _extract_height_from_query(q)
    
    print(f"\n  [提取] 年龄={age}, 地区={region}, 性别={gender}, 体重={weight}, 身高={height}")
    
    if age is None:
        print(f"  ❌ 年龄提取失败!")
    if region is None:
        print(f"  ❌ 地区提取失败!")
    if weight is None:
        print(f"  ⚠️  体重提取失败")
    if height is None:
        print(f"  ⚠️  身高提取失败")
    
    # Step 2: 映射
    age_group = _get_age_group(age) if age else None
    region_info = _get_region_info(region) if region else None
    
    print(f"  [映射] 年龄→{age_group['label'] if age_group else 'N/A'}({age_group['group'] if age_group else 'N/A'})")
    print(f"  [映射] 地区→{region_info['region'] if region_info and region_info.get('region') else 'N/A'}")
    
    # Step 3: 构建profile
    profile = {"age": age, "region": region, "gender": gender, "weight": weight, "height": height}
    if height and weight and height > 0:
        profile["bmi"] = round(weight / ((height / 100) ** 2), 1)
        print(f"  [计算] BMI={profile['bmi']}")
    
    # Step 4: 构建RAG查询
    rag_query = _build_plan_rag_query(profile, q)
    print(f"  [RAG查询] {rag_query}")
    
    # Step 5: 构建元数据过滤
    where_filter = _build_metadata_filter(profile, "plan_generation")
    print(f"  [过滤条件] {json.dumps(where_filter, ensure_ascii=False)}")
    
    # Step 6: 向量检索（使用实际阈值）
    print(f"\n  --- 向量检索 (threshold={settings.RAG_SIMILARITY_THRESHOLD}) ---")
    results = vs.similarity_search(
        query_text=rag_query,
        top_k=settings.RAG_TOP_K,
        threshold=settings.RAG_SIMILARITY_THRESHOLD,
        where=where_filter,
        embedding_function=embedding_fn,
    )
    print(f"  命中: {len(results)} 条")
    for i, r in enumerate(results):
        print(f"  [{i+1}] score={r['score']:.4f} | {r['metadata'].get('file_name','N/A')} | age={r['metadata'].get('age_group','')} | region={r['metadata'].get('region','')}")
    
    # Step 7: 同样查询，使用低阈值0.2（诊断用）
    print(f"\n  --- 诊断检索 (threshold=0.2) ---")
    results_low = vs.similarity_search(
        query_text=rag_query,
        top_k=10,
        threshold=0.2,
        where=where_filter,
        embedding_function=embedding_fn,
    )
    print(f"  命中: {len(results_low)} 条")
    for i, r in enumerate(results_low):
        print(f"  [{i+1}] score={r['score']:.4f} | {r['metadata'].get('file_name','N/A')} | age={r['metadata'].get('age_group','')} | region={r['metadata'].get('region','')}")
    
    # Step 8: 重排序
    if results_low:
        reranked = _rerank_results_by_metadata(results_low, profile)
        print(f"\n  --- 重排序后 ---")
        for i, r in enumerate(reranked):
            print(f"  [{i+1}] score={r['score']:.4f} | {r['metadata'].get('file_name','N/A')}")
    
    # 总结
    print(f"\n  [结论] ", end="")
    if len(results) > 0:
        print(f"✅ 阈值{settings.RAG_SIMILARITY_THRESHOLD}下可命中 {len(results)} 条")
        top = results[0]
        print(f"     首条: {top['metadata'].get('file_name','N/A')} (score={top['score']:.4f})")
    elif len(results_low) > 0:
        print(f"❌ 阈值{settings.RAG_SIMILARITY_THRESHOLD}下无结果，但0.2下有{len(results_low)}条 → 阈值太高!")
    else:
        print(f"❌ 两个阈值下都无结果 → 查询或文档问题!")

print("\n" + "=" * 70)
print("测试完成")
print("=" * 70)