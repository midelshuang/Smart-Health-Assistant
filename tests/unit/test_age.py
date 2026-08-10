import sys
sys.path.insert(0, '.')
from src.repositories.vector_store import VectorStore
from src.core.embedding import get_embedding_function
from src.core.guideline_registry import select_guidelines_by_profile

# 模拟用户profile
profile = {'age': 25, 'diseases': ['肥胖']}
selected = select_guidelines_by_profile(profile)
print(f"选中指南: {selected}")

# 构造 where 过滤器
where_filter = {
    "$or": [
        {"$and": [
            {"source_type": "guideline"},
            {"guideline_title": {"$in": selected}},
        ]},
        {"source_type": "expert_media"},
    ]
}
print(f"where 过滤器: {where_filter}")

# 查询 ChromaDB 验证
vs = VectorStore()
vs.connect()
vs.get_or_create_collection(get_embedding_function())

results = vs.similarity_search(
    query_text="25岁超重如何食疗",
    top_k=10,
    threshold=0.3,
    where=where_filter,
    embedding_function=get_embedding_function(),
)

print(f"\n检索结果数: {len(results)}")
for i, r in enumerate(results[:5]):
    meta = r.get('metadata', {})
    print(f"  [{i}] guideline_title={meta.get('guideline_title')}, source_type={meta.get('source_type')}, distance={r.get('distance', 'N/A')}")