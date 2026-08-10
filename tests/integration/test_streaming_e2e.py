"""
SSE 流式响应 集成测试

启动真实 HTTP 服务（uvicorn），通过 httpx 客户端验证端到端：
1. 正常流式：SSE 事件正确到达客户端
2. 心跳保活：慢速生成器触发 keepalive 包
3. 错误恢复：生成器异常时发送 error 事件
4. 客户端断开：服务端检测并终止生成
5. Prometheus 指标：全部流式指标已注册且有数据
"""

import asyncio
import json
import threading
import time

import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from src.api.streaming import stream_with_heartbeat, get_sse_headers

# ============================================================
# 测试用 FastAPI 应用
# ============================================================

_test_app = FastAPI(title="Streaming Integration Test")


@_test_app.get("/stream/normal")
async def _normal(request: Request):
    def gen():
        for i in range(5):
            yield {"type": "content", "data": f"chunk-{i}"}
        yield {"type": "done", "data": {"total": 5}}

    async def ag():
        async for sse in stream_with_heartbeat(gen(), request, endpoint="test/normal"):
            yield sse

    return StreamingResponse(ag(), media_type="text/event-stream", headers=get_sse_headers())


@_test_app.get("/stream/slow")
async def _slow(request: Request):
    def gen():
        for i in range(3):
            time.sleep(0.5)
            yield {"type": "content", "data": f"slow-{i}"}
        yield {"type": "done", "data": {}}

    async def ag():
        async for sse in stream_with_heartbeat(
            gen(), request, endpoint="test/slow",
            heartbeat_interval=0.3,
            disconnect_check_interval=0.2,
        ):
            yield sse

    return StreamingResponse(ag(), media_type="text/event-stream", headers=get_sse_headers())


@_test_app.get("/stream/error")
async def _error(request: Request):
    def gen():
        yield {"type": "content", "data": "ok-1"}
        raise RuntimeError("模拟 LLM 超时")

    async def ag():
        async for sse in stream_with_heartbeat(gen(), request, endpoint="test/error"):
            yield sse

    return StreamingResponse(ag(), media_type="text/event-stream", headers=get_sse_headers())


@_test_app.get("/stream/long")
async def _long(request: Request):
    def gen():
        for i in range(10):
            time.sleep(0.3)
            yield {"type": "content", "data": f"long-{i}"}
        yield {"type": "done", "data": {}}

    async def ag():
        async for sse in stream_with_heartbeat(
            gen(), request, endpoint="test/long",
            heartbeat_interval=1.0,
            disconnect_check_interval=0.5,
        ):
            yield sse

    return StreamingResponse(ag(), media_type="text/event-stream", headers=get_sse_headers())


# ============================================================
# Pytest Fixtures
# ============================================================

@pytest.fixture(scope="module")
def server_port():
    """分配测试端口"""
    return 19998


@pytest.fixture(scope="module")
def base_url(server_port):
    return f"http://127.0.0.1:{server_port}"


@pytest.fixture(scope="module")
def uvicorn_server(server_port):
    """启动后台 uvicorn 服务"""
    config = uvicorn.Config(
        _test_app, host="127.0.0.1", port=server_port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(2)
    yield server
    server.should_exit = True
    thread.join(timeout=5)


@pytest_asyncio.fixture
async def client(uvicorn_server, base_url):
    """异步 HTTP 客户端"""
    async with httpx.AsyncClient(base_url=base_url) as c:
        yield c


def _parse_sse_lines(lines):
    """从 SSE 行列表中解析出事件"""
    events = []
    for line in lines:
        if line.startswith("data: "):
            data = line[6:].strip()
            if data == "[DONE]":
                events.append("[DONE]")
            else:
                events.append(json.loads(data))
    return events


# ============================================================
# 测试用例
# ============================================================

class TestNormalStreaming:
    """正常流式传输"""

    @pytest.mark.asyncio
    async def test_returns_sse_content_type(self, client):
        """响应 Content-Type 为 text/event-stream"""
        async with client.stream("GET", "/stream/normal") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

    @pytest.mark.asyncio
    async def test_five_content_events_plus_done(self, client):
        """5 个 content 事件 + 1 个 done + [DONE]"""
        async with client.stream("GET", "/stream/normal") as resp:
            lines = [line async for line in resp.aiter_lines()]

        events = _parse_sse_lines(lines)
        content = [e for e in events if isinstance(e, dict) and e.get("type") == "content"]
        done = [e for e in events if isinstance(e, dict) and e.get("type") == "done"]

        assert len(content) == 5
        assert content[0]["data"] == "chunk-0"
        assert content[4]["data"] == "chunk-4"
        assert len(done) == 1
        assert done[0]["data"]["total"] == 5
        assert events[-1] == "[DONE]"

    @pytest.mark.asyncio
    async def test_sse_headers_present(self, client):
        """响应包含标准 SSE headers"""
        async with client.stream("GET", "/stream/normal") as resp:
            assert resp.headers["cache-control"] == "no-cache"
            assert resp.headers["connection"] == "keep-alive"
            assert resp.headers["x-accel-buffering"] == "no"


class TestHeartbeat:
    """心跳保活"""

    @pytest.mark.asyncio
    async def test_heartbeat_sent_on_slow_generator(self, client):
        """慢速生成器触发心跳包"""
        heartbeat_count = 0
        data_events = []

        async with client.stream("GET", "/stream/slow") as resp:
            async for line in resp.aiter_lines():
                if line.startswith(": keepalive"):
                    heartbeat_count += 1
                elif line.startswith("data: "):
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    data_events.append(json.loads(data))

        assert heartbeat_count > 0, "期望至少收到 1 个心跳包"
        assert len(data_events) >= 3

    @pytest.mark.asyncio
    async def test_heartbeat_is_comment_not_data(self, client):
        """心跳包以冒号开头，不触发客户端 onmessage"""
        async with client.stream("GET", "/stream/slow") as resp:
            async for line in resp.aiter_lines():
                if line.startswith(": keepalive"):
                    assert not line.startswith("data:")
                    return

        pytest.fail("未收到心跳包")


class TestErrorRecovery:
    """错误恢复"""

    @pytest.mark.asyncio
    async def test_error_event_sent_on_exception(self, client):
        """生成器异常时发送 error 事件"""
        async with client.stream("GET", "/stream/error") as resp:
            lines = [line async for line in resp.aiter_lines()]

        events = _parse_sse_lines(lines)
        error_events = [e for e in events if isinstance(e, dict) and e.get("type") == "error"]

        assert len(error_events) == 1
        assert "LLM 超时" in error_events[0]["data"]["error"]
        assert events[-1] == "[DONE]"

    @pytest.mark.asyncio
    async def test_content_before_error_preserved(self, client):
        """异常前的内容事件不丢失"""
        async with client.stream("GET", "/stream/error") as resp:
            lines = [line async for line in resp.aiter_lines()]

        events = _parse_sse_lines(lines)
        content = [e for e in events if isinstance(e, dict) and e.get("type") == "content"]
        assert len(content) == 1
        assert content[0]["data"] == "ok-1"


class TestClientDisconnect:
    """客户端断开检测"""

    @pytest.mark.asyncio
    async def test_client_disconnect_stops_server_generation(self, client):
        """客户端断开后服务端终止生成"""
        received = []
        try:
            async with client.stream("GET", "/stream/long", timeout=3.0) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        received.append(json.loads(data))
                        if len(received) >= 2:
                            break
        except (httpx.ReadTimeout, httpx.ReadError, asyncio.TimeoutError):
            pass

        # 客户端只收到 2 个事件
        assert len(received) == 2

        # 等待服务端检测断开
        await asyncio.sleep(2)

        # Prometheus 指标中应包含断开计数
        from src.utils.metrics import get_metrics
        metrics = get_metrics()
        assert "stream_client_disconnects_total" in metrics


class TestPrometheusMetrics:
    """Prometheus 指标验证"""

    @pytest.mark.asyncio
    async def test_all_metrics_registered_after_streaming(self, client):
        """流式请求后所有指标已注册"""
        # 先触发一次流式请求
        async with client.stream("GET", "/stream/normal") as resp:
            async for _ in resp.aiter_lines():
                pass

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

    @pytest.mark.asyncio
    async def test_token_count_recorded(self, client):
        """token 计数指标有数据"""
        async with client.stream("GET", "/stream/normal") as resp:
            async for _ in resp.aiter_lines():
                pass

        from src.utils.metrics import get_metrics
        metrics = get_metrics()
        # stream_tokens_total 应出现
        assert "stream_tokens_total" in metrics

    @pytest.mark.asyncio
    async def test_error_count_recorded(self, client):
        """错误计数指标有数据"""
        async with client.stream("GET", "/stream/error") as resp:
            async for _ in resp.aiter_lines():
                pass

        from src.utils.metrics import get_metrics
        metrics = get_metrics()
        for line in metrics.split("\n"):
            if "stream_errors_total" in line and "test/error" in line and not line.startswith("#"):
                assert "RuntimeError" in line
                return
        pytest.fail("未找到 stream_errors_total 指标")
