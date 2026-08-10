import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.rag_service import RAGPipeline

p = RAGPipeline()

# 先创建一个测试用户档案
print("=" * 60)
print("测试 1: 南京15岁男孩肥胖饮食规划 (无用户档案)")
print("=" * 60)
try:
    result = p.query(user_query="南京15岁男孩肥胖饮食规划", user_id="test_no_profile")
    print(f"意图: {result.get('intent')}")
    print(f"答案来源: {result.get('answer_source')}")
    print(f"答案: {result.get('answer', '')[:500]}")
    print(f"来源: {len(result.get('sources', []))} 条")
    for s in result.get('sources', [])[:3]:
        print(f"  - [{s.get('source_type')}] {s.get('file_name')}")
    print(f"plan_config: {'有' if result.get('plan_config') else '无'}")
except Exception as e:
    print(f"失败: {e}")
    import traceback
    traceback.print_exc()

print()
print("=" * 60)
print("测试 2: 60岁高血压老人饮食建议 (guideline_query)")
print("=" * 60)
try:
    result = p.query(user_query="60岁高血压老人饮食建议", user_id="test_no_profile")
    print(f"意图: {result.get('intent')}")
    print(f"答案来源: {result.get('answer_source')}")
    print(f"答案: {result.get('answer', '')[:500]}")
    print(f"来源: {len(result.get('sources', []))} 条")
    for s in result.get('sources', [])[:3]:
        print(f"  - [{s.get('source_type')}] {s.get('file_name')}")
except Exception as e:
    print(f"失败: {e}")
    import traceback
    traceback.print_exc()

print()
print("=" * 60)
print("测试 3: 食物查询 (food_search)")
print("=" * 60)
try:
    result = p.query(user_query="鸡蛋的热量和蛋白质含量", user_id="test_no_profile")
    print(f"意图: {result.get('intent')}")
    print(f"答案来源: {result.get('answer_source')}")
    print(f"答案: {result.get('answer', '')[:500]}")
    print(f"SQL结果: {len(result.get('sql_results', []))} 条")
except Exception as e:
    print(f"失败: {e}")
    import traceback
    traceback.print_exc()
