"""
端到端测试 - 直接调用LangGraph pipeline
暴露所有流程断点，一次性定位问题
"""
import sys, os, json, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.WARNING)

from src.services.rag_service import RAGPipeline
from src.config import settings

# 降低阈值以便测试
print(f"当前配置: THRESHOLD={settings.RAG_SIMILARITY_THRESHOLD}, TOP_K={settings.RAG_TOP_K}")

test_queries = [
    "成都25岁男生178cm160斤减肥食养规划",
    "南京15岁男孩肥胖饮食规划",
    "郑州50岁女性糖尿病饮食",
    "广州35岁男性减重",
    "哈尔滨70岁老人养生",
]

service = RAGPipeline()

# 清除Redis缓存
try:
    import redis
    r = redis.Redis(host='localhost', port=6379, db=0)
    r.flushdb()
    print("Redis缓存已清除")
except Exception as e:
    print(f"清除缓存失败: {e}")

for query in test_queries:
    print(f"\n{'='*70}")
    print(f"查询: {query}")
    print(f"{'='*70}")
    
    try:
        result = service.query(user_query=query, user_id="test_user")
        
        answer = result.get("answer", "")
        sources = result.get("sources", [])
        intent = result.get("intent", "unknown")
        answer_source = result.get("answer_source", "")
        vector_results = result.get("vector_results", [])
        sql_results = result.get("sql_results", [])
        success = result.get("success", False)
        error = result.get("error", "")
        
        print(f"  意图: {intent}")
        print(f"  来源: {answer_source}")
        print(f"  成功: {success}")
        if error:
            print(f"  错误: {error}")
        print(f"  SQL结果: {len(sql_results)} 条")
        print(f"  向量结果: {len(vector_results)} 条")
        print(f"  引用来源: {len(sources)} 条")
        for s in sources[:3]:
            print(f"    - {s.get('title', s.get('source_id', 'N/A'))} (score={s.get('score', 'N/A')})")
        
        print(f"\n  回答摘要: {answer[:200]}...")
        
        # 诊断
        if intent not in ("plan_generation", "guideline_query", "food_search", "hybrid"):
            print(f"\n  ⚠️  意图识别异常: {intent}")
        
        if intent == "plan_generation" and len(vector_results) == 0:
            print(f"\n  ❌ plan_generation意图但向量结果为空!")
            print(f"     可能原因: 阈值过高 / 查询不匹配 / 文档缺失")
        
        if answer_source == "llm_generated":
            print(f"\n  ⚠️  未检索到知识库内容，使用LLM生成")
        elif answer_source == "knowledge_base":
            print(f"\n  ✅ 使用了知识库内容")
            
    except Exception as e:
        print(f"  ❌ 异常: {e}")
        import traceback
        traceback.print_exc()

print(f"\n{'='*70}")
print("测试完成")