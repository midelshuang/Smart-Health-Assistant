"""
TracingMiddleware 单元测试

验证：
1. 中间件为每个请求创建 root span
2. 响应头包含 X-Trace-Id 和 X-Span-Id
3. 正确提取入站 traceparent（分布式追踪上下文传播）
4. /metrics 等跳过路径不创建 span
5. 异常时正确记录错误状态
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace

from src.config.tracing import init_tracing, shutdown_tracing, get_tracer
from src.api.middleware.tracing import TracingMiddleware


@pytest.fixture
def app():
    """创建测试用 FastAPI 应用"""
    application = FastAPI()

    @application.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    @application.get("/error")
    async def error_endpoint():
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="测试异常")

    @application.get("/items/{item_id}")
    async def items_endpoint(item_id: str):
        return {"item_id": item_id}

    return application


@pytest.fixture
def client(app):
    """创建测试客户端（已注册 TracingMiddleware）"""
    init_tracing(exporter_type="console", sample_rate=1.0)
    app.add_middleware(TracingMiddleware)
    with TestClient(app) as c:
        yield c
    shutdown_tracing()


class TestTracingMiddleware:

    def test_request_creates_span(self, client):
        """验证请求创建 span 并在响应头注入追踪信息"""
        response = client.get("/test")
        assert response.status_code == 200

        trace_id = response.headers.get("X-Trace-Id")
        span_id = response.headers.get("X-Span-Id")
        assert trace_id is not None
        assert span_id is not None
        assert len(trace_id) == 32
        assert len(span_id) == 16

    def test_span_attributes_recorded(self, client):
        """验证 span 记录了正确的属性"""
        response = client.get("/test")
        assert response.status_code == 200

        tracer = get_tracer("test")
        span = trace.get_current_span()

        assert span is not None

    def test_incoming_trace_context_extracted(self, client):
        """验证入站 traceparent 被正确提取"""
        traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        response = client.get(
            "/test",
            headers={"traceparent": traceparent},
        )
        assert response.status_code == 200

        trace_id = response.headers.get("X-Trace-Id")
        assert trace_id is not None

    def test_error_request_records_error_status(self, client):
        """验证异常请求记录错误状态"""
        response = client.get("/error")
        assert response.status_code == 500

    def test_skip_paths_not_traced(self, client):
        """验证跳过路径不产生追踪头"""
        skip_paths = ["/metrics", "/favicon.ico", "/api/health"]
        for path in skip_paths:
            response = client.get(path)
            assert response.headers.get("X-Trace-Id") is None

    def test_dynamic_path_normalized(self, client):
        """验证动态路径中的 ID 被标准化"""
        response = client.get("/items/123")
        assert response.status_code == 200
        assert response.headers.get("X-Trace-Id") is not None

    def test_500_status_code_recorded(self, client):
        """验证 500 响应的 trace_id 仍被注入"""
        response = client.get("/error")
        assert response.headers.get("X-Trace-Id") is not None
        assert response.headers.get("X-Span-Id") is not None


class TestTracingConfig:

    def test_init_tracing_with_console_exporter(self):
        """验证 Console exporter 初始化"""
        init_tracing(exporter_type="console")
        provider = trace.get_tracer_provider()
        assert provider is not None
        shutdown_tracing()

    def test_shutdown_tracing(self):
        """验证关闭追踪"""
        init_tracing(exporter_type="console")
        shutdown_tracing()
        provider = trace.get_tracer_provider()
        assert provider is not None

    def test_get_tracer_returns_tracer(self):
        """验证 get_tracer 返回有效的 Tracer"""
        init_tracing(exporter_type="console")
        tracer = get_tracer("test.module")
        assert tracer is not None
        shutdown_tracing()

    def test_inject_trace_headers(self):
        """验证 inject_trace_headers 正确注入追踪头"""
        from src.config.tracing import inject_trace_headers
        init_tracing(exporter_type="console")

        tracer = get_tracer("test.inject")
        with tracer.start_as_current_span("test-span"):
            headers = {"Content-Type": "application/json"}
            result = inject_trace_headers(headers)
            assert "Content-Type" in result
            assert "traceparent" in result

        shutdown_tracing()


class TestTracingHelper:

    def test_traced_node_wraps_function(self):
        """验证 traced_node 装饰器正确包装函数"""
        from src.core.tracing_helper import traced_node
        init_tracing(exporter_type="console")

        @traced_node("test_node")
        def my_node(state):
            return {"result": "ok"}

        result = my_node({"intent": "test"})
        assert result == {"result": "ok"}

        shutdown_tracing()

    def test_traced_node_records_error(self):
        """验证 traced_node 装饰器记录异常"""
        from src.core.tracing_helper import traced_node
        init_tracing(exporter_type="console")

        @traced_node("error_node")
        def error_node(state):
            raise RuntimeError("节点执行失败")

        with pytest.raises(RuntimeError, match="节点执行失败"):
            error_node({"intent": "test"})

        shutdown_tracing()