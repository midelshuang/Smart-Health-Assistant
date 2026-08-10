"""
SSE 流式响应 单元测试

覆盖：
1. SSE 格式化函数（data / done / heartbeat / headers）
2. stream_with_heartbeat 核心逻辑（正常 / 空生成器 / 异常 / 断开 / 心跳）
3. Prometheus 流式指标记录函数
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.streaming import (
    format_sse,
    format_sse_done,
    format_sse_heartbeat,
    get_sse_headers,
    stream_with_heartbeat,
)


# ============================================================
# 辅助函数
# ============================================================

def _make_request(disconnected: bool = False) -> MagicMock:
    """构造 mock Request，控制 is_disconnected 返回值"""
    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=disconnected)
    return request


async def _collect(async_gen) -> list:
    """收集异步生成器的所有输出"""
    result = []
    async for item in async_gen:
        result.append(item)
    return result


def _parse_sse_chunks(chunks: list) -> list:
    """将 SSE 字符串列表解析为事件对象列表"""
    events = []
    for chunk in chunks:
        for line in chunk.strip().split("\n"):
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                events.append("[DONE]")
            else:
                events.append(json.loads(data))
    return events


# ============================================================
# 1. SSE 格式化
# ============================================================

class TestSSEFormatting:

    def test_format_sse_dict(self):
        """字典格式化为 SSE data 事件"""
        result = format_sse({"type": "content", "data": "hello"})
        assert result == 'data: {"type": "content", "data": "hello"}\n\n'

    def test_format_sse_string(self):
        """字符串直接作为 SSE data"""
        result = format_sse("plain text")
        assert result == "data: plain text\n\n"

    def test_format_sse_done(self):
        """[DONE] 标记格式正确"""
        assert format_sse_done() == "data: [DONE]\n\n"

    def test_format_sse_heartbeat(self):
        """心跳注释格式正确（以冒号开头，不触发客户端 onmessage）"""
        result = format_sse_heartbeat()
        assert result == ": keepalive\n\n"
        assert not result.startswith("data:")

    def test_get_sse_headers(self):
        """SSE headers 包含全部必要字段"""
        headers = get_sse_headers()
        assert headers["Content-Type"] == "text/event-stream"
        assert headers["Cache-Control"] == "no-cache"
        assert headers["Connection"] == "keep-alive"
        assert headers["X-Accel-Buffering"] == "no"

    def test_format_sse_chinese_not_escaped(self):
        """中文内容不被 Unicode 转义"""
        result = format_sse({"type": "content", "data": "你好世界"})
        assert "你好世界" in result
        assert "\\u" not in result

    def test_format_sse_nested_dict(self):
        """嵌套字典正确序列化"""
        data = {"type": "done", "data": {"answer": "ok", "sources": [{"title": "doc"}]}}
        result = format_sse(data)
        parsed = json.loads(result[6:].strip())
        assert parsed["data"]["sources"][0]["title"] == "doc"


# ============================================================
# 2. stream_with_heartbeat 核心逻辑
# ============================================================

class TestStreamBasic:

    @pytest.mark.asyncio
    async def test_normal_streaming(self):
        """正常流式：多个事件 + [DONE]"""
        def gen():
            yield {"type": "sources", "data": [{"title": "doc1"}]}
            yield {"type": "content", "data": "hello"}
            yield {"type": "done", "data": {"answer": "hello"}}

        chunks = await _collect(
            stream_with_heartbeat(gen(), _make_request(), endpoint="test")
        )

        events = _parse_sse_chunks(chunks)
        assert len(events) == 4  # sources + content + done + [DONE]
        assert events[0]["type"] == "sources"
        assert events[1]["type"] == "content"
        assert events[2]["type"] == "done"
        assert events[-1] == "[DONE]"

    @pytest.mark.asyncio
    async def test_empty_generator(self):
        """空生成器也能正常结束并发送 [DONE]"""
        def gen():
            return
            yield  # make it a generator

        chunks = await _collect(
            stream_with_heartbeat(gen(), _make_request(), endpoint="test")
        )
        assert any("[DONE]" in c for c in chunks)

    @pytest.mark.asyncio
    async def test_single_event(self):
        """只有一个事件也能正常工作"""
        def gen():
            yield {"type": "done", "data": {}}

        chunks = await _collect(
            stream_with_heartbeat(gen(), _make_request(), endpoint="test")
        )
        events = _parse_sse_chunks(chunks)
        assert len(events) == 2  # done + [DONE]
        assert events[0]["type"] == "done"

    @pytest.mark.asyncio
    async def test_five_content_events(self):
        """5 个 content 事件全部正确到达"""
        def gen():
            for i in range(5):
                yield {"type": "content", "data": f"chunk-{i}"}
            yield {"type": "done", "data": {"total": 5}}

        chunks = await _collect(
            stream_with_heartbeat(gen(), _make_request(), endpoint="test")
        )
        events = _parse_sse_chunks(chunks)
        content_events = [e for e in events if isinstance(e, dict) and e.get("type") == "content"]
        assert len(content_events) == 5
        assert content_events[0]["data"] == "chunk-0"
        assert content_events[4]["data"] == "chunk-4"


class TestStreamHeartbeat:

    @pytest.mark.asyncio
    async def test_heartbeat_sent_on_idle(self):
        """空闲时发送心跳包"""
        def gen():
            yield {"type": "content", "data": "1"}
            time.sleep(0.2)
            yield {"type": "content", "data": "2"}

        chunks = await _collect(
            stream_with_heartbeat(
                gen(), _make_request(), endpoint="test",
                heartbeat_interval=0.05,
            )
        )
        assert any("keepalive" in c for c in chunks)

    @pytest.mark.asyncio
    async def test_no_heartbeat_when_fast(self):
        """快速生成器不触发心跳"""
        def gen():
            for i in range(5):
                yield {"type": "content", "data": f"fast-{i}"}

        chunks = await _collect(
            stream_with_heartbeat(
                gen(), _make_request(), endpoint="test",
                heartbeat_interval=10.0,  # 很长的心跳间隔
            )
        )
        assert not any("keepalive" in c for c in chunks)


class TestStreamError:

    @pytest.mark.asyncio
    async def test_generator_error_sends_error_event(self):
        """生成器抛异常时发送 error 事件 + [DONE]"""
        def gen():
            yield {"type": "content", "data": "ok"}
            raise RuntimeError("LLM 超时")

        chunks = await _collect(
            stream_with_heartbeat(gen(), _make_request(), endpoint="test")
        )
        events = _parse_sse_chunks(chunks)

        error_events = [e for e in events if isinstance(e, dict) and e.get("type") == "error"]
        assert len(error_events) == 1
        assert "LLM 超时" in error_events[0]["data"]["error"]
        assert events[-1] == "[DONE]"

    @pytest.mark.asyncio
    async def test_error_on_first_yield(self):
        """第一个 yield 就抛异常"""
        def gen():
            raise ValueError("初始化失败")
            yield  # make it a generator

        chunks = await _collect(
            stream_with_heartbeat(gen(), _make_request(), endpoint="test")
        )
        combined = "".join(chunks)
        assert "error" in combined
        assert "初始化失败" in combined
        assert "[DONE]" in combined


class TestStreamDisconnect:

    @pytest.mark.asyncio
    async def test_client_disconnect_stops_generation(self):
        """客户端断开后终止生成，后续事件不产出"""
        def gen():
            yield {"type": "content", "data": "1"}
            yield {"type": "content", "data": "2"}
            yield {"type": "content", "data": "3"}
            yield {"type": "content", "data": "4"}  # 不应到达

        request = MagicMock()
        call_count = [0]
        async def mock_disconnected():
            call_count[0] += 1
            return call_count[0] > 1
        request.is_disconnected = mock_disconnected

        chunks = await _collect(
            stream_with_heartbeat(
                gen(), request, endpoint="test",
                heartbeat_interval=0.05,
                disconnect_check_interval=0.01,
            )
        )
        combined = "".join(chunks)
        # 不应包含第 4 个数据
        assert '"4"' not in combined or "[DONE]" in combined


class TestStreamMetrics:

    @pytest.mark.asyncio
    async def test_ttfb_recorded(self):
        """TTFB（首字延迟）被记录"""
        def gen():
            time.sleep(0.05)  # 模拟 LLM 首字延迟，确保 TTFB > 0
            yield {"type": "content", "data": "hello"}

        with patch("src.api.streaming.record_stream_ttfb") as mock_ttfb:
            await _collect(stream_with_heartbeat(gen(), _make_request(), endpoint="test"))
            mock_ttfb.assert_called_once()
            assert mock_ttfb.call_args[0][0] == "test"
            assert mock_ttfb.call_args[0][1] > 0

    @pytest.mark.asyncio
    async def test_token_count_recorded(self):
        """content 事件被正确计数"""
        def gen():
            yield {"type": "sources", "data": []}
            yield {"type": "content", "data": "a"}
            yield {"type": "content", "data": "b"}
            yield {"type": "content", "data": "c"}
            yield {"type": "done", "data": {}}

        with patch("src.api.streaming.record_stream_tokens") as mock_tokens:
            await _collect(stream_with_heartbeat(gen(), _make_request(), endpoint="test"))
            mock_tokens.assert_called_once_with("test", 3)

    @pytest.mark.asyncio
    async def test_duration_recorded(self):
        """总耗时被记录"""
        def gen():
            yield {"type": "content", "data": "x"}

        with patch("src.api.streaming.record_stream_duration") as mock_dur:
            await _collect(stream_with_heartbeat(gen(), _make_request(), endpoint="test"))
            mock_dur.assert_called_once()
            assert mock_dur.call_args[0][1] > 0

    @pytest.mark.asyncio
    async def test_error_recorded_on_exception(self):
        """生成器异常时错误指标被记录"""
        def gen():
            raise RuntimeError("test error")
            yield

        with patch("src.api.streaming.record_stream_error") as mock_err:
            await _collect(stream_with_heartbeat(gen(), _make_request(), endpoint="test"))
            mock_err.assert_called_once_with("test", "RuntimeError")

    @pytest.mark.asyncio
    async def test_connection_start_end_balance(self):
        """连接开始/结束指标成对调用"""
        def gen():
            yield {"type": "content", "data": "x"}

        with patch("src.api.streaming.stream_connection_start") as mock_start, \
             patch("src.api.streaming.stream_connection_end") as mock_end:
            await _collect(stream_with_heartbeat(gen(), _make_request(), endpoint="test"))
            mock_start.assert_called_once()
            mock_end.assert_called_once()


# ============================================================
# 3. Prometheus 指标记录函数
# ============================================================

class TestStreamingMetricsFunctions:

    def test_record_stream_ttfb(self):
        from src.utils.metrics import record_stream_ttfb
        record_stream_ttfb("test", 0.5)

    def test_record_stream_duration(self):
        from src.utils.metrics import record_stream_duration
        record_stream_duration("test", 2.0)

    def test_record_stream_tokens(self):
        from src.utils.metrics import record_stream_tokens
        record_stream_tokens("test", 10)

    def test_record_stream_error(self):
        from src.utils.metrics import record_stream_error
        record_stream_error("test", "RuntimeError")

    def test_record_stream_disconnect(self):
        from src.utils.metrics import record_stream_disconnect
        record_stream_disconnect("test")

    def test_stream_connection_lifecycle(self):
        from src.utils.metrics import stream_connection_start, stream_connection_end
        stream_connection_start()
        stream_connection_end()

    def test_all_metrics_registered(self):
        """所有流式指标已在 Prometheus 注册"""
        from src.utils.metrics import get_metrics
        metrics = get_metrics()
        for name in [
            "stream_ttfb_seconds",
            "stream_duration_seconds",
            "stream_tokens_total",
            "stream_active_connections",
            "stream_errors_total",
            "stream_client_disconnects_total",
        ]:
            assert name in metrics, f"指标 {name} 未注册"
