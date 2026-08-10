"""
RAG 节点追踪辅助模块

为 LangGraph 中的每个节点提供 OpenTelemetry span 包装，
自动记录节点耗时、输入状态摘要、输出结果统计等关键指标。

使用方式（装饰器）：
    from src.core.tracing_helper import traced_node

    @traced_node("intent_recognizer")
    def intent_recognizer_node(state):
        ...

或在 graph_builder.py 中包装：
    workflow.add_node("intent_recognizer", traced_node("intent_recognizer")(intent_recognizer_node))
"""

import time
import functools
from typing import Callable, Dict, Any

from opentelemetry import trace
from opentelemetry.trace import SpanKind, StatusCode

from src.config.tracing import get_tracer, is_tracing_initialized

_rag_tracer = get_tracer("src.core.rag")


def traced_node(node_name: str):
    """
    为 RAG 节点函数添加追踪的装饰器

    自动创建子 span，记录：
    - 节点输入：意图名、查询长度、改写次数等
    - 节点输出：结果数量、耗时、是否出错
    - 节点耗时（毫秒）

    Args:
        node_name: 节点名称（如 "intent_recognizer"）

    Returns:
        装饰器函数
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(state: Dict[str, Any]) -> Dict[str, Any]:
            if not is_tracing_initialized():
                return func(state)

            span_name = f"rag.node.{node_name}"
            with _rag_tracer.start_as_current_span(
                span_name,
                kind=SpanKind.INTERNAL,
            ) as span:
                start_time = time.time()

                span.set_attribute("rag.node.name", node_name)

                _record_input_attrs(span, node_name, state)

                try:
                    result = func(state)

                    duration_ms = (time.time() - start_time) * 1000
                    span.set_attribute("rag.node.duration_ms", round(duration_ms, 2))

                    _record_output_attrs(span, node_name, result)

                    error = result.get("error")
                    if error:
                        span.set_status(StatusCode.ERROR, str(error))
                    else:
                        span.set_status(StatusCode.OK)

                    return result

                except Exception as exc:
                    duration_ms = (time.time() - start_time) * 1000
                    span.set_attribute("rag.node.duration_ms", round(duration_ms, 2))
                    span.set_status(StatusCode.ERROR, str(exc))
                    span.record_exception(exc)
                    raise

        return wrapper

    return decorator


def _record_input_attrs(span, node_name: str, state: Dict[str, Any]):
    """记录节点输入状态的关键属性"""
    intent = state.get("intent", "unknown")
    span.set_attribute("rag.intent", str(intent))

    original_query = state.get("original_query", "")
    if original_query:
        span.set_attribute("rag.query.length", len(str(original_query)))

    rewrite_count = state.get("rewrite_count", 0)
    span.set_attribute("rag.rewrite_count", int(rewrite_count))

    user_id = state.get("user_id", "")
    if user_id:
        span.set_attribute("rag.user_id", str(user_id))

    if node_name == "clarification":
        max_score = 0.0
        for r in state.get("vector_results", []) or []:
            s = r.get("score", 0)
            if s > max_score:
                max_score = s
        span.set_attribute("rag.clarification.max_score", round(max_score, 3))

    if node_name in ("sql_retriever", "vector_retriever"):
        rewritten = state.get("rewritten_query", "")
        if rewritten:
            span.set_attribute("rag.rewritten_query.length", len(str(rewritten)))


def _record_output_attrs(span, node_name: str, result: Dict[str, Any]):
    """记录节点输出结果的关键属性"""
    if node_name == "intent_recognizer":
        new_intent = result.get("intent")
        if new_intent:
            span.set_attribute("rag.intent.result", str(new_intent))

    elif node_name == "sql_retriever":
        results = result.get("sql_results", []) or []
        span.set_attribute("rag.sql_results.count", len(results))

    elif node_name == "vector_retriever":
        results = result.get("vector_results", []) or []
        span.set_attribute("rag.vector_results.count", len(results))
        if results:
            top_score = max((r.get("score", 0) for r in results), default=0)
            span.set_attribute("rag.vector_results.top_score", round(top_score, 3))

    elif node_name == "evaluator":
        passed = result.get("eval_passed", False)
        span.set_attribute("rag.eval.passed", bool(passed))
        feedback = result.get("eval_feedback", "")
        if feedback:
            span.set_attribute("rag.eval.feedback", str(feedback)[:200])

    elif node_name == "clarification":
        needs = result.get("needs_clarification", False)
        span.set_attribute("rag.clarification.needed", bool(needs))

    elif node_name == "fusion":
        fused = result.get("fused_results", []) or []
        span.set_attribute("rag.fusion.fused_count", len(fused))

    elif node_name == "generator":
        answer = result.get("generated_answer", "") or ""
        span.set_attribute("rag.answer.length", len(str(answer)))
        sources = result.get("sources", []) or []
        span.set_attribute("rag.sources.count", len(sources))

    elif node_name == "plan_generator":
        plan_id = result.get("plan_id")
        if plan_id:
            span.set_attribute("rag.plan.id", str(plan_id))

    elif node_name == "safety_check":
        blocked = result.get("safety_blocked", False)
        span.set_attribute("rag.safety.blocked", bool(blocked))
        category = result.get("safety_category", "")
        if category:
            span.set_attribute("rag.safety.category", str(category))