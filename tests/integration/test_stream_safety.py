# -*- coding: utf-8 -*-
"""R03 回归测试：流式路径必须遵守安全拦截终态，绝不进入模型生成。

背景缺陷：stream_answer 在 graph.invoke 后不检查 safety_blocked，
被拦截的查询仍会调用 llm_client.stream_chat 绕过安全护栏。

打桩方式：直接替换 pipeline._graph 为假图；get_llm_client 返回记录调用的假客户端。
"""
from types import SimpleNamespace

from src.services.rag_service import RAGPipeline


def _pipeline_with(monkeypatch, invoke_result):
    """构建假图管道，并 hook 掉 LLM 客户端以记录流式调用。"""
    p = RAGPipeline()
    p._graph = SimpleNamespace(invoke=lambda state: dict(invoke_result))

    llm_calls = []

    class _FakeLLM:
        def stream_chat(self, messages, **kwargs):
            llm_calls.append(messages)
            yield "不应该被调用的内容"

    monkeypatch.setattr("src.core.llm_client.get_llm_client", lambda: _FakeLLM())
    return p, llm_calls


def test_blocked_query_never_reaches_llm(monkeypatch):
    """被拦截的查询：返回拒绝文案事件流，且模型零调用。"""
    p, llm_calls = _pipeline_with(monkeypatch, {
        "safety_blocked": True,
        "generated_answer": "拒绝：涉及处方药，请咨询医生",
        "sources": [],
        "safety_category": "prescription",
        "intent": "blocked",
    })

    events = list(p.stream_answer("帮我开点降压药", user_id=""))
    types_ = [e["type"] for e in events]

    assert types_ == ["sources", "content", "done"]
    assert events[1]["data"] == "拒绝：涉及处方药，请咨询医生"
    done = events[2]["data"]
    assert done["safety_blocked"] is True
    assert done["answer_source"] == "safety_guard"
    assert done["intent"] == "blocked"
    assert llm_calls == [], "被拦截的查询绝不允许调用模型！"


def test_safe_query_still_streams_from_llm(monkeypatch):
    """正常查询：不受安全终态分支影响，仍走模型流式生成。"""
    p, llm_calls = _pipeline_with(monkeypatch, {
        "safety_blocked": False,
        "sources": [{"id": "c1"}],
        "_stream_prompt": "用户问题",
        "_stream_system_prompt": "系统提示词",
        "answer_source": "knowledge_base",
        "intent": "food_query",
    })

    events = list(p.stream_answer("鸡蛋的热量是多少", user_id=""))
    types_ = [e["type"] for e in events]

    assert types_ == ["sources", "answer_source", "content", "done"]
    assert events[2]["data"] == "不应该被调用的内容"  # 内容来自（假）模型
    assert len(llm_calls) == 1


def test_blocked_without_reply_falls_back_to_default_refusal(monkeypatch):
    """拦截但缺拒绝文案时：使用兜底文案，不产出空内容。"""
    p, llm_calls = _pipeline_with(monkeypatch, {
        "safety_blocked": True,
        "generated_answer": "",
        "sources": [],
    })

    events = list(p.stream_answer("危险问题", user_id=""))

    assert events[1]["data"]  # 兜底文案非空
    assert "无法提供回答" in events[1]["data"]
    assert llm_calls == []
