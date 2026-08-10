import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.rag_service import RAGPipeline

p = RAGPipeline()

test_cases = [
    {
        "query": "南京15岁男孩肥胖饮食规划",
        "user_id": "test_no_profile",
        "desc": "儿童青少年肥胖 + 南京地区（无档案）",
    },
    {
        "query": "60岁高血压老人饮食建议",
        "user_id": "test_no_profile",
        "desc": "老年人 + 高血压",
    },
    {
        "query": "孕妇糖尿病饮食",
        "user_id": "test_no_profile",
        "desc": "孕期 + 糖尿病",
    },
]

for tc in test_cases:
    print("=" * 60)
    print(f"测试: {tc['desc']}")
    print(f"查询: {tc['query']}")
    print("=" * 60)
    try:
        result = p.query(user_query=tc["query"], user_id=tc["user_id"])
        print(f"意图: {result.get('intent')}")
        print(f"答案来源: {result.get('answer_source')}")
        print(f"答案: {result.get('answer', '')[:300]}...")
        sources = result.get("sources", [])
        print(f"来源 ({len(sources)} 条):")
        for s in sources[:5]:
            print(f"  - [{s.get('source_type')}] {s.get('file_name')} ({s.get('source_label')})")
        vec_results = result.get("vector_results", [])
        print(f"向量检索结果: {len(vec_results)} 条")
        for v in vec_results[:3]:
            meta = v.get("metadata", {})
            print(f"  - {meta.get('file_name')} (dist={v.get('distance', '?')})")
        print()
    except Exception as e:
        print(f"✗ 失败: {e}")
        import traceback
        traceback.print_exc()
        print()
