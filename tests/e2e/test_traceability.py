"""
溯源功能端到端测试

验证：
1. VectorStore 能连接 ChromaDB 并检索
2. RAG 管道优先使用知识库数据
3. 无知识库匹配时使用 LLM 生成并标注
"""
import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_vector_store():
    """测试向量库连接和检索"""
    print("\n" + "=" * 60)
    print("测试 1: VectorStore 连接和检索")
    print("=" * 60)
    try:
        from src.repositories.vector_store import VectorStore
        from src.core.embedding import get_embedding_function
        vs = VectorStore()
        vs.connect()
        embedding_fn = get_embedding_function()
        vs.get_or_create_collection(embedding_function=embedding_fn)
        print(f"✓ VectorStore 初始化成功 (collection={vs.collection_name})")

        # 测试搜索
        results = vs.similarity_search(query_text="糖尿病饮食指南", top_k=3, embedding_function=embedding_fn)
        print(f"✓ 搜索返回 {len(results)} 条结果")
        for i, r in enumerate(results, 1):
            meta = r.get("metadata", {})
            print(f"  {i}. {meta.get('file_name', '?')} (score={r.get('score', 0):.3f})")
        return True
    except Exception as e:
        print(f"✗ VectorStore 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_rag_pipeline_kb():
    """测试 RAG 管道 - 知识库命中场景"""
    print("\n" + "=" * 60)
    print("测试 2: RAG 管道 - 知识库命中（糖尿病饮食）")
    print("=" * 60)
    try:
        from src.services.rag_service import RAGPipeline
        pipeline = RAGPipeline()
        result = pipeline.query(
            user_query="糖尿病患者的饮食建议",
            user_id="test_user",
        )
        print(f"✓ 答案来源: {result.get('answer_source', 'N/A')}")
        print(f"✓ 意图: {result.get('intent')}")
        print(f"✓ SQL 结果: {len(result.get('sql_results', []))} 条")
        print(f"✓ 向量结果: {len(result.get('vector_results', []))} 条")
        print(f"✓ 来源数: {len(result.get('sources', []))} 条")
        sources = result.get("sources", [])
        for s in sources[:3]:
            print(f"  - {s.get('source_label', s.get('source_type'))}: {s.get('file_name')}")
        answer = result.get("answer", "")
        print(f"✓ 答案预览: {answer[:150]}...")
        return result.get("answer_source") == "knowledge_base"
    except Exception as e:
        print(f"✗ RAG 管道测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_rag_pipeline_llm():
    """测试 RAG 管道 - LLM 生成场景（无知识库匹配）"""
    print("\n" + "=" * 60)
    print("测试 3: RAG 管道 - 无知识库匹配（冷门问题）")
    print("=" * 60)
    try:
        from src.services.rag_service import RAGPipeline
        pipeline = RAGPipeline()
        # 用一个知识库中肯定没有的冷门问题
        result = pipeline.query(
            user_query="火星土壤种植土豆的营养价值分析",
            user_id="test_user",
        )
        print(f"✓ 答案来源: {result.get('answer_source', 'N/A')}")
        print(f"✓ 意图: {result.get('intent')}")
        print(f"✓ SQL 结果: {len(result.get('sql_results', []))} 条")
        print(f"✓ 向量结果: {len(result.get('vector_results', []))} 条")
        sources = result.get("sources", [])
        print(f"✓ 来源数: {len(sources)} 条")
        for s in sources[:3]:
            print(f"  - {s.get('source_label', s.get('source_type'))}: {s.get('file_name')}")
        answer = result.get("answer", "")
        print(f"✓ 答案预览: {answer[:150]}...")
        return result.get("answer_source") == "llm_generated"
    except Exception as e:
        print(f"✗ RAG 管道 LLM 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_rag_pipeline_food():
    """测试 RAG 管道 - 食物营养查询（SQL 路径）"""
    print("\n" + "=" * 60)
    print("测试 4: RAG 管道 - 食物营养查询（SQL）")
    print("=" * 60)
    try:
        from src.services.rag_service import RAGPipeline
        pipeline = RAGPipeline()
        result = pipeline.query(
            user_query="鸡蛋的营养成分",
            user_id="test_user",
        )
        print(f"✓ 答案来源: {result.get('answer_source', 'N/A')}")
        print(f"✓ 意图: {result.get('intent')}")
        print(f"✓ SQL 结果: {len(result.get('sql_results', []))} 条")
        print(f"✓ 向量结果: {len(result.get('vector_results', []))} 条")
        sources = result.get("sources", [])
        print(f"✓ 来源数: {len(sources)} 条")
        for s in sources[:5]:
            print(f"  - {s.get('source_label', s.get('source_type'))}: {s.get('file_name')}")
        answer = result.get("answer", "")
        print(f"✓ 答案预览: {answer[:200]}...")
        return result.get("answer_source") == "knowledge_base" and len(result.get("sql_results", [])) > 0
    except Exception as e:
        print(f"✗ RAG 管道食物测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("溯源功能端到端测试")
    print("=" * 60)

    results = []
    results.append(("VectorStore", test_vector_store()))
    results.append(("RAG-知识库", test_rag_pipeline_kb()))
    results.append(("RAG-LLM生成", test_rag_pipeline_llm()))
    results.append(("RAG-食物营养", test_rag_pipeline_food()))

    print("\n" + "=" * 60)
    print("测试汇总")
    print("=" * 60)
    for name, passed in results:
        status = "✓ 通过" if passed else "✗ 失败"
        print(f"  {name}: {status}")
