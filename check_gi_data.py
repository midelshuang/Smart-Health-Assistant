import sys
sys.path.insert(0, '.')

from src.repositories.vector_store import VectorStore
from src.core.embedding import get_embedding_function

vs = VectorStore()
vs.connect()
embedding_fn = get_embedding_function()
collection = vs.get_or_create_collection(embedding_function=embedding_fn)

# 统计各类型数据
try:
    # 按 source_type 统计
    results = collection.get(include=["metadatas"])
    metadatas = results["metadatas"]
    
    from collections import Counter
    source_types = Counter()
    gi_count = 0
    
    for meta in metadatas:
        st = meta.get("source_type", "unknown")
        source_types[st] += 1
        if meta.get("source_type") == "gi_data":
            gi_count += 1
    
    print(f"总文档数: {len(metadatas)}")
    print(f"\n按来源类型统计:")
    for st, count in source_types.most_common():
        print(f"  {st}: {count} 条")
    
    print(f"\nGI 数据条数: {gi_count}")
    
    # 检查 GI 数据内容
    if gi_count > 0:
        gi_docs = collection.get(
            where={"source_type": "gi_data"},
            include=["documents", "metadatas"],
            limit=3
        )
        print(f"\nGI 数据示例:")
        for i, (doc, meta) in enumerate(zip(gi_docs["documents"][:3], gi_docs["metadatas"][:3])):
            print(f"\n  [{i+1}] {meta.get('food_group', 'N/A')}")
            print(f"  内容预览: {doc[:200]}...")
    else:
        print("\n⚠️  GI 数据为空，需要重新入库！")
        
except Exception as e:
    print(f"查询失败: {e}")
