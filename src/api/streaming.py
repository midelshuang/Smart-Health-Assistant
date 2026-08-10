"""
SSE (Server-Sent Events) 流式响应工具

提供：
1. SSE 事件格式化（data / error / done）
2. 心跳保活机制（防止代理/网关超时断开）
3. 客户端断开检测（避免浪费 LLM 生成资源）
4. 同步生成器 → 异步生成器转换（不阻塞事件循环）
5. 流式指标自动采集（TTFB / token 数 / 总耗时）
"""

import asyncio
import json
import time
from typing import Any, AsyncGenerator, Callable, Generator, Optional

from fastapi import Request
from src.utils.logger import setup_logger
from src.utils.metrics import (
    record_stream_ttfb,
    record_stream_duration,
    record_stream_tokens,
    record_stream_error,
    record_stream_disconnect,
    stream_connection_start,
    stream_connection_end,
)

logger = setup_logger(name="SSEStreaming")

# 心跳间隔（秒）：在此间隔内无数据则发送 keepalive 注释
HEARTBEAT_INTERVAL = 15.0

# 断开检测间隔（秒）：每隔多久检查一次客户端是否断开
DISCONNECT_CHECK_INTERVAL = 5.0


def format_sse(data: Any) -> str:
    """格式化 SSE data 事件"""
    if isinstance(data, str):
        payload = data
    else:
        payload = json.dumps(data, ensure_ascii=False)
    return f"data: {payload}\n\n"


def format_sse_done() -> str:
    """格式式化 SSE [DONE] 标记"""
    return "data: [DONE]\n\n"


def format_sse_heartbeat() -> str:
    """格式化 SSE 心跳注释（不触发客户端 onmessage，仅保活）"""
    return ": keepalive\n\n"


async def stream_with_heartbeat(
    sync_generator: Generator,
    request: Request,
    endpoint: str,
    heartbeat_interval: float = HEARTBEAT_INTERVAL,
    disconnect_check_interval: float = DISCONNECT_CHECK_INTERVAL,
) -> AsyncGenerator[str, None]:
    """
    将同步生成器包装为异步流式生成器

    功能：
    1. 在独立线程中运行同步生成器，不阻塞事件循环
    2. 定期发送心跳保活，防止代理超时
    3. 检测客户端断开，提前终止生成
    4. 自动采集 TTFB / token 数 / 总耗时指标

    Args:
        sync_generator: 同步生成器（如 pipeline.stream_answer()）
        request: FastAPI Request 对象（用于检测客户端断开）
        endpoint: 端点标识（用于指标标签）
        heartbeat_interval: 心跳间隔秒数
        disconnect_check_interval: 断开检测间隔秒数

    Yields:
        str: SSE 格式的事件字符串
    """
    stream_connection_start()
    start_time = time.time()
    ttfb_recorded = False
    token_count = 0

    try:
        # 在线程池中运行同步生成器
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        sentinel = object()
        generator_exhausted = False
        generator_error: Optional[Exception] = None

        async def _run_generator():
            """在线程池中运行同步生成器，结果放入队列"""
            def _run():
                try:
                    for item in sync_generator:
                        # put_nowait 会在队列满时阻塞（但在线程中是安全的）
                        while True:
                            try:
                                queue.put_nowait(item)
                                break
                            except asyncio.QueueFull:
                                time.sleep(0.01)
                except Exception as e:
                    return e
                return None

            error = await loop.run_in_executor(None, _run)
            if error is not None:
                await queue.put(error)
            await queue.put(sentinel)

        # 启动生成器任务
        gen_task = asyncio.create_task(_run_generator())

        last_data_time = time.time()
        last_disconnect_check = time.time()

        while True:
            # 计算下次心跳的超时
            now = time.time()
            time_since_data = now - last_data_time
            timeout = max(0.01, heartbeat_interval - time_since_data)

            try:
                item = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                # 超时：发送心跳保活
                yield format_sse_heartbeat()
                last_data_time = time.time()

                # 顺便检查客户端断开
                if await request.is_disconnected():
                    logger.info(f"[{endpoint}] 客户端断开，终止流式生成")
                    record_stream_disconnect(endpoint)
                    gen_task.cancel()
                    break
                continue

            # 检查生成器结束
            if item is sentinel:
                break

            # 检查生成器异常
            if isinstance(item, Exception):
                generator_error = item
                break

            # 记录首字延迟
            if not ttfb_recorded:
                ttfb = time.time() - start_time
                record_stream_ttfb(endpoint, ttfb)
                ttfb_recorded = True
                logger.info(f"[{endpoint}] TTFB={ttfb:.3f}s")

            # 记录 token 计数
            if isinstance(item, dict) and item.get("type") == "content":
                token_count += 1

            last_data_time = time.time()
            yield format_sse(item)

            # 定期检查客户端断开
            if time.time() - last_disconnect_check > disconnect_check_interval:
                last_disconnect_check = time.time()
                if await request.is_disconnected():
                    logger.info(f"[{endpoint}] 客户端断开，终止流式生成")
                    record_stream_disconnect(endpoint)
                    gen_task.cancel()
                    break

        # 等待生成器任务完成
        if not gen_task.done():
            gen_task.cancel()
            try:
                await gen_task
            except asyncio.CancelledError:
                pass

        # 处理生成器异常
        if generator_error:
            record_stream_error(endpoint, type(generator_error).__name__)
            yield format_sse({
                "type": "error",
                "data": {"error": str(generator_error)},
            })

        # 发送 [DONE]
        yield format_sse_done()

        # 记录指标
        total_duration = time.time() - start_time
        record_stream_duration(endpoint, total_duration)
        if token_count > 0:
            record_stream_tokens(endpoint, token_count)

        logger.info(
            f"[{endpoint}] 流式完成: duration={total_duration:.2f}s, "
            f"tokens={token_count}, ttfb={'%.3f' % (time.time() - start_time - total_duration + start_time) if ttfb_recorded else 'N/A'}"
        )

    except asyncio.CancelledError:
        logger.info(f"[{endpoint}] 流式生成被取消")
        raise
    except Exception as e:
        logger.error(f"[{endpoint}] 流式生成异常: {e}")
        record_stream_error(endpoint, type(e).__name__)
        yield format_sse({
            "type": "error",
            "data": {"error": str(e)},
        })
        yield format_sse_done()
    finally:
        stream_connection_end()
        total = time.time() - start_time
        logger.info(f"[{endpoint}] 流式连接结束, 总耗时={total:.2f}s")


def get_sse_headers() -> dict:
    """获取 SSE 响应的标准 headers"""
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Content-Type": "text/event-stream",
    }
