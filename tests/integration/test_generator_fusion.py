# -*- coding: utf-8 -*-
"""R10 回归测试：generator_node 优先消费 RRF 融合排序结果。

背景缺陷：fusion 节点产出 fused_results（RRF 跨源排序 top-K），
但 generator_node 直接读 sql_results/vector_results 原始两路，
融合排序结果被丢弃（排序白算 + 上下文可能混入低相关项）。

打桩方式：LLM 客户端捕获收到的 prompt，不依赖真实服务。
"""
from src.core.nodes import generator_node


class _PromptCapturingLLM:
    def __init__(self):
        self.prompts = []

    def chat(self, messages, **kwargs):
        self.prompts.append(messages[-1]["content"])
        return "基于检索结果的回答"


def _stub_llm(monkeypatch):
    llm = _PromptCapturingLLM()
    monkeypatch.setattr("src.core.llm_client.get_llm_client", lambda: llm)
    return llm


def _sql_item(name):
    return {
        "source_type": "nutrition_fact",
        "food_name": name,
        "category": "谷物",
        "calories_per_100g": 116,
        "protein": 2.6,
        "fat": 0.3,
        "carbs": 25.9,
    }


def _vector_item(content):
    return {
        "source_type": "guideline",
        "content": content,
        "score": 0.8,
        "metadata": {"source_file": "guide.pdf", "chunk_id": content[:8]},
    }


def test_generator_uses_fused_results_over_raw_lists(monkeypatch):
    """有 fused_results 时：上下文只含融合 top-K，原始列表中被淘汰项不得出现。"""
    llm = _stub_llm(monkeypatch)

    sql_raw = [_sql_item("白米饭"), _sql_item("糯米饭")]
    vector_raw = [
        _vector_item("低相关的向量段落A"),
        _vector_item("低相关的向量段落B"),
    ]
    fused = [
        _vector_item("融合后最相关的指南段落"),
        _sql_item("白米饭"),
        _vector_item("融合后次相关的段落"),
    ]

    state = {
        "original_query": "米饭的热量",
        "intent": "food_search",
        "sql_results": sql_raw,
        "vector_results": vector_raw,
        "fused_results": fused,
    }

    result = generator_node(state)
    prompt = llm.prompts[-1]

    assert "融合后最相关的指南段落" in prompt, "融合结果必须进入生成上下文"
    assert "糯米饭" not in prompt, "被融合排序淘汰的 SQL 项不得进入上下文"
    assert "低相关的向量段落B" not in prompt, "被融合排序淘汰的向量项不得进入上下文"
    assert result["generated_answer"] == "基于检索结果的回答"


def test_generator_falls_back_without_fused_results(monkeypatch):
    """无 fused_results（plan/unknown 等路径）：维持原始两路行为。"""
    llm = _stub_llm(monkeypatch)

    state = {
        "original_query": "帮我制定减肥计划",
        "intent": "plan_generation",
        "sql_results": [_sql_item("白米饭")],
        "vector_results": [_vector_item("计划参考指南段落")],
    }

    result = generator_node(state)
    prompt = llm.prompts[-1]

    assert "白米饭" in prompt
    assert "计划参考指南段落" in prompt
    assert result["generated_answer"]
