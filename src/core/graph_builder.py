"""
LangGraph 状态机编排

定义 RAG 管道的完整状态流转图，包括：
- 节点注册
- 普通边（线性流转）
- 条件边（意图分流 + 评估循环 + 追问分流）

流程概览：
START → 安全护栏 → 意图识别 → Query改写 → (条件分流)
                                    ├─ food_search  → SQL检索 → 评估
                                    ├─ guideline_query → 向量检索 → 评估
                                    └─ hybrid/unknown → SQL+向量并行 → 评估
                                                                    ↓
                                          (评估通过) → fusion → 答案生成 → END
                                          (评估不通过+可改写) → Query改写 → 循环
                                          (评估不通过+达上限) → 追问节点 → (触发追问) → END
                                                                           → (无需追问) → fusion → 答案生成 → END
"""

from typing import Dict, Any, Literal
from langgraph.graph import StateGraph, END, START
from langgraph.types import Send
from src.core.state import RAGState
from src.core.nodes import (
    intent_recognizer_node,
    query_rewriter_node,
    sql_retriever_node,
    vector_retriever_node,
    evaluator_node,
    generator_node,
    plan_generator_node,
    safety_check_node,
    fusion_node,
    clarification_node,
    info_collector_node,
)
from src.core.tracing_helper import traced_node
from src.utils.logger import setup_logger

logger = setup_logger(name="RAG_Graph")


def _route_after_safety(state: Dict[str, Any]) -> str:
    """
    安全护栏后的条件路由

    - 拦截命中 (safety_blocked=True) → 直接结束（答案已在 state 中）
    - 通过校验 → 进入意图识别
    """
    if state.get("safety_blocked", False):
        logger.info(f"安全护栏拦截 → END (category={state.get('safety_category')})")
        return END
    return "intent_recognizer"


def _route_after_intent(state: Dict[str, Any]) -> Any:
    """
    意图分流条件边

    根据意图识别结果决定走哪条检索路径：
    - plan_generation: 先收集用户信息 → 再走向量检索查指南 → 最后生成计划
    - food_search: 仅走 SQL 检索
    - guideline_query: 仅走向量检索
    - hybrid/unknown: 并行执行 SQL + 向量检索（使用 Send 实现并行）

    Args:
        state: 当前 RAGState 字典

    Returns:
        字符串（单路径）或 Send 列表（并行路径）
    """
    intent = state.get("intent", "unknown")

    if intent == "plan_generation":
        logger.info(f"意图分流: {intent} → info_collector → vector_retriever → plan_generator")
        return "info_collector"

    if intent == "food_search":
        logger.info("意图分流: food_search → sql_retriever + vector_retriever (并行，支持GI数据检索)")
        return [
            Send(node="sql_retriever", arg=state),
            Send(node="vector_retriever", arg=state),
        ]

    if intent == "guideline_query":
        logger.info("意图分流: guideline_query → vector_retriever")
        return "vector_retriever"

    if intent == "unknown":
        # 闲聊/其他 → 直接 LLM 通用回答，不走检索（节省算力，避免拉回无关营养行）
        logger.info("意图分流: unknown → generator (闲聊直答)")
        return "generator"

    # hybrid → 并行 SQL + 向量检索
    logger.info(f"意图分流: {intent} → sql_retriever + vector_retriever (并行)")
    return [
        Send(node="sql_retriever", arg=state),
        Send(node="vector_retriever", arg=state),
    ]


def _route_after_evaluator(state: Dict[str, Any]) -> Literal["query_rewriter", "fusion", "clarification"]:
    """
    评估循环条件边

    根据评估结果决定：
    - 评估通过 → 进入 RRF 融合节点（fusion），再生成答案
    - 评估不通过 + 改写次数未满 → 回到 Query 改写重试
    - 评估不通过 + 改写次数已满 → 进入追问节点（clarification）检查是否需要追问

    Args:
        state: 当前 RAGState 字典

    Returns:
        "query_rewriter" / "fusion" / "clarification"
    """
    eval_passed = state.get("eval_passed", False)
    rewrite_count = state.get("rewrite_count", 0)
    max_rewrite = state.get("max_rewrite_count", 2)
    feedback = state.get("eval_feedback", "")

    if eval_passed:
        logger.info("评估通过 → fusion (RRF 融合)")
        return "fusion"

    if rewrite_count < max_rewrite:
        logger.info(
            f"评估未通过 (rewrite={rewrite_count}/{max_rewrite}) → query_rewriter 重试: {feedback}"
        )
        return "query_rewriter"

    logger.info(
        f"评估未通过但已达最大改写次数 ({max_rewrite}) → clarification 追问检查: {feedback}"
    )
    return "clarification"


def _route_after_clarification(state: Dict[str, Any]) -> Literal["fusion", "__end__"]:
    """
    追问节点后的条件路由

    - needs_clarification=True → 直接结束（追问已作为 generated_answer 返回）
    - needs_clarification=False → 进入 RRF 融合继续生成
    """
    if state.get("needs_clarification", False):
        logger.info("追问触发 → END（返回追问话术）")
        return END
    logger.info("无需追问 → fusion 继续")
    return "fusion"


def build_rag_graph():
    """
    构建 RAG 状态机图

    注册所有节点和边，返回可编译的 StateGraph 实例。

    Returns:
        StateGraph 实例（未编译，调用 .compile() 后使用）
    """
    workflow = StateGraph(RAGState)

    # ========================================================
    # 1. 注册所有节点（包装 OpenTelemetry 追踪）
    # ========================================================
    workflow.add_node("safety_check", traced_node("safety_check")(safety_check_node))
    workflow.add_node("intent_recognizer", traced_node("intent_recognizer")(intent_recognizer_node))
    workflow.add_node("query_rewriter", traced_node("query_rewriter")(query_rewriter_node))
    workflow.add_node("info_collector", traced_node("info_collector")(info_collector_node))
    workflow.add_node("sql_retriever", traced_node("sql_retriever")(sql_retriever_node))
    workflow.add_node("vector_retriever", traced_node("vector_retriever")(vector_retriever_node))
    workflow.add_node("evaluator", traced_node("evaluator")(evaluator_node))
    workflow.add_node("clarification", traced_node("clarification")(clarification_node))
    workflow.add_node("fusion", traced_node("fusion")(fusion_node))
    workflow.add_node("generator", traced_node("generator")(generator_node))
    workflow.add_node("plan_generator", traced_node("plan_generator")(plan_generator_node))

    # ========================================================
    # 2. 添加边
    # ========================================================

    # 起点 → 安全护栏（入口门）
    workflow.add_edge(START, "safety_check")

    # 安全护栏 → 条件路由（拦截则直接结束，否则进入意图识别）
    workflow.add_conditional_edges(
        "safety_check",
        _route_after_safety,
        {
            END: END,
            "intent_recognizer": "intent_recognizer",
        },
    )

    # 意图识别 → Query 改写
    workflow.add_edge("intent_recognizer", "query_rewriter")

    # Query 改写 → 条件分流（单路径或并行；unknown 闲聊直走 generator）
    workflow.add_conditional_edges(
        "query_rewriter",
        _route_after_intent,
        {
            "info_collector": "info_collector",
            "sql_retriever": "sql_retriever",
            "vector_retriever": "vector_retriever",
            "generator": "generator",
        },
    )

    # info_collector → 条件路由（信息已收集→检索，未收集→结束追问）
    def _route_after_info_collector(state: Dict[str, Any]) -> str:
        if state.get("needs_clarification", False):
            logger.info("info_collector: 需要用户补充信息 → END")
            return END
        logger.info("info_collector: 信息已收集 → vector_retriever 检索指南")
        return "vector_retriever"

    workflow.add_conditional_edges(
        "info_collector",
        _route_after_info_collector,
        {
            END: END,
            "vector_retriever": "vector_retriever",
        },
    )

    # SQL 检索 → 评估
    workflow.add_edge("sql_retriever", "evaluator")

    # 向量检索 → 条件路由（plan_generation → plan_generator，其他 → evaluator）
    def _route_after_vector(state: Dict[str, Any]) -> str:
        intent = state.get("intent", "unknown")
        if intent == "plan_generation":
            logger.info("vector_retriever → plan_generator (计划生成路径)")
            return "plan_generator"
        logger.info("vector_retriever → evaluator (常规问答路径)")
        return "evaluator"

    workflow.add_conditional_edges(
        "vector_retriever",
        _route_after_vector,
        {
            "plan_generator": "plan_generator",
            "evaluator": "evaluator",
        },
    )

    # 评估 → 条件路由（循环改写 / 进入追问检查 / 进入融合）
    workflow.add_conditional_edges(
        "evaluator",
        _route_after_evaluator,
        {
            "query_rewriter": "query_rewriter",
            "fusion": "fusion",
            "clarification": "clarification",
        },
    )

    # 追问节点 → 条件路由（触发追问则结束，否则进入融合）
    workflow.add_conditional_edges(
        "clarification",
        _route_after_clarification,
        {
            END: END,
            "fusion": "fusion",
        },
    )

    # RRF 融合 → 答案生成
    workflow.add_edge("fusion", "generator")

    # 答案生成 → 终点
    workflow.add_edge("generator", END)

    # 计划生成 → 终点（计划生成是独立路径，直接结束）
    workflow.add_edge("plan_generator", END)

    logger.info("RAG StateGraph 构建完成")
    return workflow


def get_compiled_graph():
    """
    获取已编译的 RAG 图

    编译后的图可直接调用 .invoke() 或 .stream() 执行。

    Returns:
        已编译的 StateGraph
    """
    workflow = build_rag_graph()
    return workflow.compile()