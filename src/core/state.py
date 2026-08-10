from typing import TypedDict, List, Dict, Any, Optional, Literal, Annotated


def _merge_errors(existing: str, updates: str) -> str:
    if not existing:
        return updates
    if not updates:
        return existing
    return f"{existing}; {updates}"


def _merge_lists(existing: list, updates: list) -> list:
    if not existing:
        return updates
    if not updates:
        return existing
    return existing + updates


class RAGState(TypedDict, total=False):
    original_query: str
    conversation_context: List[Dict[str, str]]
    intent: Literal["food_search", "guideline_query", "hybrid", "plan_generation", "unknown", "blocked"]
    rewritten_query: str
    sql_results: Annotated[List[Dict[str, Any]], _merge_lists]
    sql_query: str
    vector_results: Annotated[List[Dict[str, Any]], _merge_lists]
    query_embedding: List[float]
    retrieved_docs: Annotated[List[Dict[str, Any]], _merge_lists]
    eval_passed: bool
    eval_feedback: str
    rewrite_count: int
    max_rewrite_count: int
    generated_answer: str
    sources: Annotated[List[Dict[str, str]], _merge_lists]
    answer_source: str  # knowledge_base | llm_generated
    error: Annotated[str, _merge_errors]
    user_id: str
    stream_mode: bool
    _stream_prompt: str
    _stream_system_prompt: str
    plan_config: Dict[str, Any]
    goal_type: str
    safety_blocked: bool
    safety_category: str
    safety_reason: str
    fused_results: List[Dict[str, Any]]
    needs_clarification: bool
    clarification_question: str
    user_profile_info: Dict[str, Any]
    info_collected: bool
    profile_used: bool
    # 🆕 两阶段检索与分级追问相关
    selected_guidelines: List[str]      # 阶段1 锁定的指南标题集合
    diseases: List[str]                 # 归一化后的 disease_tag 列表
    soft_clarifications: List[str]      # 软提示（地区缺失/疾病默认无等），不阻断流程
    # 🆕 用户反馈驱动的风格调整
    feedback_style_directive: str       # 回答风格指令（注入系统提示词）
    plan_style_directive: str           # 计划风格指令