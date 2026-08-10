"""
Jaeger 追踪数据上报验证脚本

验证流程：
1. 初始化 OpenTelemetry，使用 OTLP 导出器指向本地 Jaeger (localhost:4317)
2. 启动 FastAPI 应用（注册 TracingMiddleware）
3. 发送多个 HTTP 请求产生 span
4. 刷新 span 到 Jaeger
5. 查询 Jaeger REST API 验证 span 已上报
"""

import time
import json
import urllib.request
import urllib.error

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.tracing import (
    init_tracing,
    shutdown_tracing,
    get_tracer,
    is_tracing_initialized,
)
from src.api.middleware.tracing import TracingMiddleware
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

JAEGER_UI_URL = "http://localhost:16686"
JAEGER_API_URL = "http://localhost:16686/api"
SERVICE_NAME = "healthai-jaeger-test"


def build_test_app() -> FastAPI:
    """创建带 TracingMiddleware 的测试应用"""
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"pong": True}

    @app.get("/echo/{msg}")
    async def echo(msg: str):
        return {"echo": msg}

    @app.get("/items/{item_id}")
    async def get_item(item_id: str):
        return {"item_id": item_id}

    return app


def init_tracing_for_jaeger():
    """初始化追踪，直接使用 OTLP 导出器（SimpleSpanProcessor 即时刷新）"""
    resource = Resource.create({
        "service.name": SERVICE_NAME,
        "service.version": "2.0.0",
        "deployment.environment": "test",
    })
    provider = TracerProvider(resource=resource)
    otlp_exporter = OTLPSpanExporter(
        endpoint="localhost:4317",
        insecure=True,
    )
    provider.add_span_processor(SimpleSpanProcessor(otlp_exporter))
    trace.set_tracer_provider(provider)

    # 更新全局状态
    import src.config.tracing as tracing_mod
    tracing_mod._TRACER_PROVIDER = provider
    tracing_mod._INITIALIZED = True

    print(f"[OK] Tracing 已初始化，OTLP -> localhost:4317, service={SERVICE_NAME}")


def send_test_requests(client: TestClient):
    """发送多个 HTTP 请求产生追踪数据"""
    print("\n--- 发送测试请求 ---")

    # 请求 1: 简单 ping
    r = client.get("/ping")
    trace_id_1 = r.headers.get("X-Trace-Id", "-")
    print(f"GET /ping -> {r.status_code}, trace_id={trace_id_1}")
    assert r.status_code == 200

    # 请求 2: 带入站 traceparent（模拟跨服务传播）
    r = client.get(
        "/echo/hello",
        headers={"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"},
    )
    trace_id_2 = r.headers.get("X-Trace-Id", "-")
    print(f"GET /echo/hello -> {r.status_code}, trace_id={trace_id_2}")
    assert r.status_code == 200

    # 请求 3: 动态路径
    r = client.get("/items/42")
    trace_id_3 = r.headers.get("X-Trace-Id", "-")
    print(f"GET /items/42 -> {r.status_code}, trace_id={trace_id_3}")
    assert r.status_code == 200

    # 额外创建一个带子 span 的请求
    tracer = get_tracer("test.manual_span")
    with tracer.start_as_current_span("manual_operation") as span:
        span.set_attribute("custom.attribute", "jaeger_verification")
        span.set_attribute("test.step", "manual_span_creation")
        r = client.get("/ping")
        trace_id_4 = r.headers.get("X-Trace-Id", "-")
        print(f"GET /ping (with manual span) -> {r.status_code}, trace_id={trace_id_4}")

    print(f"\n共发送 4 个请求，产生 4 条 trace")


def query_jaeger_services() -> list:
    """查询 Jaeger API 获取服务列表"""
    url = f"{JAEGER_API_URL}/services"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", [])
    except Exception as e:
        print(f"[ERROR] 查询 Jaeger services 失败: {e}")
        return []


def query_jaeger_traces(service: str) -> list:
    """查询 Jaeger API 获取指定服务的 trace 列表"""
    url = f"{JAEGER_API_URL}/traces?service={service}&limit=20"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", [])
    except Exception as e:
        print(f"[ERROR] 查询 Jaeger traces 失败: {e}")
        return []


def verify_traces_in_jaeger() -> bool:
    """验证追踪数据已成功上报到 Jaeger"""
    print("\n--- 验证 Jaeger 中的追踪数据 ---")

    # 等待 span 刷新完成
    print("等待 span 刷新 (3秒)...")
    time.sleep(3)

    # 1. 检查服务是否注册
    services = query_jaeger_services()
    print(f"Jaeger 中的服务列表: {services}")

    if SERVICE_NAME in services:
        print(f"[OK] 服务 '{SERVICE_NAME}' 已在 Jaeger 中注册")
    else:
        print(f"[FAIL] 服务 '{SERVICE_NAME}' 未在 Jaeger 中找到")
        print(f"       已注册的服务: {services}")
        return False

    # 2. 检查 trace 是否存在
    traces = query_jaeger_traces(SERVICE_NAME)
    print(f"服务 '{SERVICE_NAME}' 的 trace 数量: {len(traces)}")

    if len(traces) == 0:
        print("[FAIL] 未找到任何 trace")
        return False

    print(f"[OK] 找到 {len(traces)} 条 trace")

    # 3. 检查 trace 详情
    for i, trace_data in enumerate(traces[:4]):
        spans = trace_data.get("spans", [])
        trace_id = trace_data.get("traceID", "unknown")
        print(f"\n  Trace #{i+1}: id={trace_id}, spans={len(spans)}")

        for span in spans:
            operation = span.get("operationName", "?")
            span_id = span.get("spanID", "?")[:16]
            duration_us = span.get("duration", 0)
            duration_ms = duration_us / 1000
            tags = {t.get("key"): t.get("value") for t in span.get("tags", [])}

            http_method = tags.get("http.method", "")
            http_status = tags.get("http.status_code", "")
            http_url = tags.get("http.url", "")

            detail = f"    span: {operation} ({span_id}, {duration_ms:.1f}ms)"
            if http_method:
                detail += f"  [{http_method} {http_url} -> {http_status}]"
            print(detail)

    return len(traces) > 0


def main():
    print("=" * 60)
    print("Jaeger 追踪数据上报验证")
    print("=" * 60)

    # 1. 初始化追踪
    init_tracing_for_jaeger()

    # 2. 创建应用并注册中间件
    app = build_test_app()
    app.add_middleware(TracingMiddleware)

    # 3. 发送测试请求
    with TestClient(app) as client:
        send_test_requests(client)

    # 4. 刷新所有 span
    print("\n--- 刷新 span ---")
    provider = trace.get_tracer_provider()
    provider.force_flush()
    print("[OK] span 已刷新到 Jaeger")

    # 5. 关闭追踪
    shutdown_tracing()

    # 6. 查询 Jaeger 验证
    success = verify_traces_in_jaeger()

    print("\n" + "=" * 60)
    if success:
        print("✅ 验证成功！追踪数据已正确上报到 Jaeger")
        print(f"\n📋 Jaeger UI: {JAEGER_UI_URL}")
        print(f"   在左侧 Service 下拉框中选择 '{SERVICE_NAME}' 查看追踪详情")
    else:
        print("❌ 验证失败！追踪数据未正确上报到 Jaeger")
        print("\n排查步骤:")
        print("  1. 确认 Jaeger 容器正常运行: docker ps | grep jaeger")
        print("  2. 确认 OTLP 端口可达: curl http://localhost:4317")
        print("  3. 检查 Jaeger 日志: docker logs jaeger")
    print("=" * 60)

    return 0 if success else 1


if __name__ == "__main__":
    exit(main())
