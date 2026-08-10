"""
端到端测试：验证 RAG 搜索逻辑重构后的三类意图分流

覆盖场景：
  1. plan_generation（信息齐全）→ info_collector → 两阶段检索 → plan_generator
  2. plan_generation（信息缺失）→ info_collector 分级追问 → END（返回追问话术）
  3. food_search                → sql_retriever → evaluator → fusion → generator
  4. unknown（闲聊）            → generator 直接 LLM 回答
  5. guideline_query            → vector_retriever → evaluator → fusion → generator

每个场景打印：意图、是否触发追问、答案摘要、来源数、SQL/向量命中数。
"""
import os
import sys
import logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# 抑制节点级 INFO 日志，便于阅读测试输出
logging.basicConfig(level=logging.WARNING)

from src.services.rag_service import RAGPipeline
from src.utils.logger import setup_logger

logger = setup_logger(name="E2E_SearchLogic")

# ============================================================
# 测试用例
# ============================================================
TEST_CASES = [
    {
        "name": "1. plan_generation 信息齐全（应走完整两阶段检索+计划生成）",
        "query": "成都25岁男生178cm160斤减肥食养规划",
        "user_id": "guest",
        "expect_intent": "plan_generation",
        "expect_clarify": False,
    },
    {
        "name": "2. plan_generation 信息缺失（应触发分级追问阻断）",
        "query": "帮我规划减肥食谱",
        "user_id": "guest",
        "expect_intent": "plan_generation",
        "expect_clarify": True,
    },
    {
        "name": "3. food_search 营养成分查询（应走 SQL 检索）",
        "query": "鸡蛋的热量是多少",
        "user_id": "guest",
        "expect_intent": "food_search",
        "expect_clarify": False,
    },
    {
        "name": "4. unknown 闲聊（应直走 generator，不走检索）",
        "query": "你好，你是谁",
        "user_id": "guest",
        "expect_intent": "unknown",
        "expect_clarify": False,
    },
    {
        "name": "5. guideline_query 指南咨询（应走向量检索）",
        "query": "糖尿病病人怎么吃比较好",
        "user_id": "guest",
        "expect_intent": "guideline_query",
        "expect_clarify": False,
    },
]


def truncate(s: str, n: int = 200) -> str:
    if not s:
        return ""
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else s[:n] + "..."


def run_one(pipeline: RAGPipeline, case: dict) -> None:
    print("\n" + "=" * 78)
    print(f"【{case['name']}】")
    print(f"  query: {case['query']}")
    print("-" * 78)

    result = pipeline.query(
        user_query=case["query"],
        user_id=case.get("user_id", "guest"),
        conversation_context=None,
        stream=False,
    )

    intent = result.get("intent", "unknown")
    answer = result.get("answer", "") or ""
    sources = result.get("sources", []) or []
    sql_results = result.get("sql_results", []) or []
    vector_results = result.get("vector_results", []) or []
    success = result.get("success", False)
    error = result.get("error", "")

    # 判断是否触发了追问：答案中包含"请输入/请补充/请提供"等追问话术
    clarify_keywords = ["请输入", "请补充", "请提供", "请告诉我", "请告知", "需要您提供"]
    triggered_clarify = any(k in answer for k in clarify_keywords)

    print(f"  intent        = {intent}  (期望: {case['expect_intent']})")
    print(f"  success       = {success}")
    if error:
        print(f"  error         = {error}")
    print(f"  触发追问      = {triggered_clarify}  (期望: {case['expect_clarify']})")
    print(f"  sources 数量  = {len(sources)}")
    print(f"  sql_results   = {len(sql_results)} 条")
    print(f"  vector_results= {len(vector_results)} 条")
    print(f"  answer 摘要  = {truncate(answer, 240)}")

    # 简单断言
    ok_intent = (intent == case["expect_intent"])
    ok_clarify = (triggered_clarify == case["expect_clarify"])
    tag = "✅ PASS" if (ok_intent and ok_clarify and success) else "❌ FAIL"
    print(f"  结果          = {tag}")
    if not ok_intent:
        print(f"     ↳ 意图不符: 实际={intent} 期望={case['expect_intent']}")
    if not ok_clarify:
        print(f"     ↳ 追问状态不符: 实际={triggered_clarify} 期望={case['expect_clarify']}")


def main() -> None:
    print("=" * 78)
    print("RAG 搜索逻辑重构 - 端到端测试")
    print("=" * 78)

    pipeline = RAGPipeline()
    # 预热编译图
    pipeline._ensure_graph()
    print(f"RAG 图已编译: {pipeline._graph is not None}")

    for case in TEST_CASES:
        try:
            run_one(pipeline, case)
        except Exception as e:
            print(f"\n【{case['name']}】 ❌ EXCEPTION: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 78)
    print("测试完成")
    print("=" * 78)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
