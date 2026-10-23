import sys
sys.path.insert(0, '.')

from src.repositories.vector_store import VectorStore
from src.core.embedding import get_embedding_function

vs = VectorStore()
vs.connect()
embedding_fn = get_embedding_function()
collection = vs.get_or_create_collection(embedding_function=embedding_fn)

# 查询 GI 数据的元数据
gi_docs = collection.get(
    where={"source_type": "gi_data"},
    include=["documents", "metadatas"],
    limit=3
)

print("GI 数据元数据结构:")
for i, meta in enumerate(gi_docs["metadatas"][:2]):
    print(f"\n[{i+1}] 元数据字段:")
    for key, value in meta.items():
        print(f"  {key}: {value}")

# 测试向量检索
print("\n\n测试向量检索 '水果升糖慢':")
results = vs.similarity_search(
    query_text="水果升糖慢",
    top_k=5,
    threshold=0.2,
    embedding_function=embedding_fn
)

for i, r in enumerate(results):
    print(f"\n[{i+1}] 分数: {r.get('score', 0):.3f}")
    meta = r.get('metadata', {})
    print(f"  来源类型: {meta.get('source_type', 'N/A')}")
    print(f"  内容预览: {r.get('content', '')[:150]}...")
