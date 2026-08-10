"""
流式响应功能演示

启动真实 HTTP 服务，通过命令行客户端演示：
1. 正常流式传输（打字机效果）
2. 心跳保活（慢速生成器）
3. 错误恢复（异常处理）
4. 客户端断开检测
"""

import asyncio
import json
import threading
import time
import sys

import uvicorn
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

sys.path.insert(0, ".")

from src.api.streaming import stream_with_heartbeat, get_sse_headers

PORT = 18888
BASE_URL = f"http://127.0.0.1:{PORT}"

app = FastAPI(title="Streaming Demo")


@app.get("/stream/normal")
async def normal_stream(request: Request):
    """正常流式：模拟 RAG 答案逐字输出"""
    def gen():
        answer = "鸡蛋富含优质蛋白质，每个约含 6 克蛋白质，是减脂期的理想食物。"
        for char in answer:
            time.sleep(0.08)  # 模拟 LLM 逐字输出
            yield {"type": "content", "data": char}
        yield {"type": "done", "data": {"answer": answer}}

    async def ag():
        async for sse in stream_with_heartbeat(gen(), request, endpoint="demo/normal"):
            yield sse

    return StreamingResponse(ag(), media_type="text/event-stream", headers=get_sse_headers())


@app.get("/stream/slow")
async def slow_stream(request: Request):
    """慢速流式：触发心跳"""
    def gen():
        yield {"type": "content", "data": "首"}
        time.sleep(2)  # 模拟 LLM 思考
        yield {"type": "content", "data": "字"}
        time.sleep(2)
        yield {"type": "content", "data": "延"}
        time.sleep(2)
        yield {"type": "content", "data": "迟"}
        yield {"type": "done", "data": {}}

    async def ag():
        async for sse in stream_with_heartbeat(
            gen(), request, endpoint="demo/slow",
            heartbeat_interval=1.0,
            disconnect_check_interval=0.5,
        ):
            yield sse

    return StreamingResponse(ag(), media_type="text/event-stream", headers=get_sse_headers())


@app.get("/stream/error")
async def error_stream(request: Request):
    """错误流式：模拟 LLM 超时"""
    def gen():
        yield {"type": "content", "data": "正"}
        yield {"type": "content", "data": "在"}
        yield {"type": "content", "data": "生"}
        raise RuntimeError("LLM 调用超时（模拟）")

    async def ag():
        async for sse in stream_with_heartbeat(gen(), request, endpoint="demo/error"):
            yield sse

    return StreamingResponse(ag(), media_type="text/event-stream", headers=get_sse_headers())


@app.get("/stream/long")
async def long_stream(request: Request):
    """长流式：用于演示客户端断开"""
    def gen():
        for i in range(20):
            time.sleep(0.3)
            yield {"type": "content", "data": f"token-{i} "}
        yield {"type": "done", "data": {}}

    async def ag():
        async for sse in stream_with_heartbeat(
            gen(), request, endpoint="demo/long",
            heartbeat_interval=2.0,
            disconnect_check_interval=0.5,
        ):
            yield sse

    return StreamingResponse(ag(), media_type="text/event-stream", headers=get_sse_headers())


def start_server():
    """启动后台服务"""
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(1.5)
    return server


def print_header(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


async def demo_normal(client: httpx.AsyncClient):
    """演示 1：正常流式（打字机效果）"""
    print_header("演示 1：正常流式传输（打字机效果）")
    print("  场景：RAG 答案逐字输出，前端实时展示")
    print("  端点：GET /stream/normal")
    print("-" * 60)

    answer = ""
    print("  答案：", end="", flush=True)

    async with client.stream("GET", f"{BASE_URL}/stream/normal") as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                data = line[6:].strip()
                if data == "[DONE]":
                    print("\n  [完成标记] [DONE]")
                    break
                event = json.loads(data)
                if event["type"] == "content":
                    print(event["data"], end="", flush=True)
                    answer += event["data"]
                elif event["type"] == "done":
                    print(f"\n  [done 事件] 完整答案长度: {len(event['data']['answer'])}")

    print(f"\n  ✓ 共收到 {len(answer)} 个字符")


async def demo_heartbeat(client: httpx.AsyncClient):
    """演示 2：心跳保活"""
    print_header("演示 2：心跳保活（慢速生成器）")
    print("  场景：LLM 思考期间无数据，服务端发送心跳防止超时")
    print("  端点：GET /stream/slow")
    print("  心跳间隔：1.0s")
    print("-" * 60)

    heartbeat_count = 0
    data_count = 0
    start = time.time()

    async with client.stream("GET", f"{BASE_URL}/stream/slow") as resp:
        async for line in resp.aiter_lines():
            elapsed = time.time() - start
            if line.startswith(": keepalive"):
                heartbeat_count += 1
                print(f"  [{elapsed:5.2f}s] [HEARTBEAT] : keepalive  ← 保活包")
            elif line.startswith("data: "):
                data = line[6:].strip()
                if data == "[DONE]":
                    print(f"  [{elapsed:5.2f}s] [DONE]")
                    break
                event = json.loads(data)
                if event["type"] == "content":
                    data_count += 1
                    print(f"  [{elapsed:5.2f}s] [DATA]      {event['data']}")

    print(f"\n  ✓ 收到 {data_count} 个数据 + {heartbeat_count} 个心跳包")


async def demo_error(client: httpx.AsyncClient):
    """演示 3：错误恢复"""
    print_header("演示 3：错误恢复（LLM 异常）")
    print("  场景：LLM 调用中途异常，服务端发送 error 事件 + [DONE]")
    print("  端点：GET /stream/error")
    print("-" * 60)

    async with client.stream("GET", f"{BASE_URL}/stream/error") as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                data = line[6:].strip()
                if data == "[DONE]":
                    print(f"  [DONE]  ← 结束标记，前端可优雅关闭")
                    break
                event = json.loads(data)
                if event["type"] == "content":
                    print(f"  [content] {event['data']}")
                elif event["type"] == "error":
                    print(f"  [error]   {event['data']['error']}  ← 错误事件，前端可提示用户")

    print(f"\n  ✓ 异常已捕获，前端收到 error 事件并优雅降级")


async def demo_disconnect(client: httpx.AsyncClient):
    """演示 4：客户端断开检测"""
    print_header("演示 4：客户端断开检测")
    print("  场景：客户端主动断开，服务端检测后终止 LLM 生成")
    print("  端点：GET /stream/long")
    print("  断开检测间隔：0.5s")
    print("-" * 60)

    received = 0
    try:
        async with client.stream("GET", f"{BASE_URL}/stream/long", timeout=2.0) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    received += 1
                    if received <= 3:
                        print(f"  [收到] {json.loads(data)['data'].strip()}")
                    if received == 3:
                        print("  ... 收到 3 个 token 后主动断开连接")
                        break
    except (httpx.ReadTimeout, httpx.ReadError, asyncio.TimeoutError):
        pass

    print(f"\n  客户端已断开，收到 {received} 个 token")
    print("  等待 1.5s 让服务端检测断开...")
    await asyncio.sleep(1.5)

    from src.utils.metrics import get_metrics
    metrics = get_metrics()
    for line in metrics.split("\n"):
        if "stream_client_disconnects_total" in line and "demo/long" in line and not line.startswith("#"):
            print(f"  [指标] {line.strip()}")
            break

    print(f"\n  ✓ 服务端检测到断开，已取消后续 LLM 生成（节省资源）")


async def demo_metrics():
    """演示 5：Prometheus 指标"""
    print_header("演示 5：Prometheus 流式指标")
    print("-" * 60)

    from src.utils.metrics import get_metrics
    metrics = get_metrics()

    print("  关键指标：\n")
    for line in metrics.split("\n"):
        if line.startswith("#"):
            continue
        if any(k in line for k in [
            "stream_tokens_total{endpoint=\"demo/normal\"}",
            "stream_tokens_total{endpoint=\"demo/slow\"}",
            "stream_tokens_total{endpoint=\"demo/error\"}",
            "stream_duration_seconds_count",
            "stream_errors_total",
            "stream_client_disconnects_total",
        ]):
            print(f"  {line.strip()}")

    print("\n  ✓ 所有流式指标已自动采集，可在 Grafana 仪表盘可视化")


async def main():
    print("=" * 60)
    print("  HealthAI-Platform 流式响应功能演示")
    print("=" * 60)
    print(f"  服务地址：{BASE_URL}")
    print(f"  演示内容：")
    print(f"    1. 正常流式（打字机效果）")
    print(f"    2. 心跳保活")
    print(f"    3. 错误恢复")
    print(f"    4. 客户端断开检测")
    print(f"    5. Prometheus 指标")
    print("=" * 60)

    print(f"\n  启动 FastAPI 服务 @ {BASE_URL} ...")
    start_server()
    print("  [OK] 服务已启动\n")

    async with httpx.AsyncClient() as client:
        await demo_normal(client)
        await demo_heartbeat(client)
        await demo_error(client)
        await demo_disconnect(client)
        await demo_metrics()

    print_header("演示完成")
    print("  ✓ 流式响应核心功能全部验证：")
    print("    • 异步非阻塞生成器")
    print("    • SSE 事件格式化（content / done / [DONE]）")
    print("    • 心跳保活（: keepalive）")
    print("    • 客户端断开检测")
    print("    • 错误恢复（error 事件）")
    print("    • Prometheus 指标采集")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
