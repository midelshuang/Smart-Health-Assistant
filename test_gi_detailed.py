import sys
sys.path.insert(0, '.')

from src.services.rag_service import RAGPipeline

pipeline = RAGPipeline()

# 测试查询
query = "有什么水果升糖慢的吗？"
print(f"查询: {query}")
print("-" * 50)

result = pipeline.query(
    user_query=query,
    user_id="test_user",
)

print(f"状态: {'成功' if result.get('success') else '失败'}")
print(f"意图: {result.get('intent')}")
print(f"答案来源: {result.get('answer_source')}")
print(f"\nSQL 结果数: {len(result.get('sql_results', []))}")
for i, sql in enumerate(result.get('sql_results', [])[:3]):
    print(f"  [{i+1}] {sql.get('food_name')}: {sql.get('calories_per_100g', 0)} kcal")

print(f"\n向量结果数: {len(result.get('vector_results', []))}")
for i, vec in enumerate(result.get('vector_results', [])[:3]):
    meta = vec.get('metadata', {})
    print(f"  [{i+1}] source_type={meta.get('source_type')}, score={vec.get('score', 0):.3f}")
    print(f"      内容预览: {vec.get('content', '')[:100]}...")

print(f"\n来源数: {len(result.get('sources', []))}")
for i, src in enumerate(result.get('sources', [])[:3]):
    print(f"  [{i+1}] {src.get('file_name')} ({src.get('source_type')})")

print(f"\n答案预览: {result.get('answer', '')[:300]}")
