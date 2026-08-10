"""
分布式追踪中间件

为每个 HTTP 请求创建 OpenTelemetry root span，实现：
1. 自动提取入站 Trace Context（支持从其他服务透传的 traceparent）
2. 记录请求方法、路径、状态码、耗时等关键属性
3. 注入 X-Trace-Id / X-Span-Id 到响应头（便于前端关联）
4. 将当前 Span 注入 request.state，供下游 handler 创建子 span
5. 对 /metrics /health 等路径跳过追踪
"""

import time
import logging
from typing import Callable, Awaitable, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from opentelemetry import trace
from opentelemetry import context as otel_context
from opentelemetry.trace import StatusCode, SpanKind

from src.config.tracing import (
    get_tracer,
    extract_trace_context,
    is_tracing_initialized,
)

logger = logging.getLogger(__name__)

_TRACER = get_tracer("src.api.middleware.tracing")

_SKIP_PATHS = {
    "/metrics",
    "/favicon.ico",
    "/api/health",
    "/api/v1/health",
    "/api/v2/health",
}

_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
}


class TracingMiddleware(BaseHTTPMiddleware):
    """
    OpenTelemetry 分布式追踪中间件

    为每个 HTTP 请求创建一个 root span，记录：
    - http.method, http.url, http.route
    - http.status_code, http.response_time_ms
    - user.id（从 request.state 提取）
    - error.message, error.type（异常时）
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path

        if path in _SKIP_PATHS:
            return await call_next(request)

        if not is_tracing_initialized():
            return await call_next(request)

        span_name = f"{request.method} {self._sanitize_path(path)}"

        parent_context = self._extract_parent_context(request)

        with _TRACER.start_as_current_span(
            span_name,
            kind=SpanKind.SERVER,
            context=parent_context,
        ) as span:
            span.set_attribute("http.method", request.method)
            span.set_attribute("http.url", str(request.url))
            span.set_attribute("http.scheme", request.url.scheme)
            span.set_attribute("http.target", request.url.path)
            if request.url.query:
                span.set_attribute("http.query", request.url.query)
            span.set_attribute(
                "http.user_agent",
                request.headers.get("user-agent", "unknown"),
            )
            client_ip = request.client.host if request.client else "unknown"
            span.set_attribute("http.client_ip", client_ip)

            self._inject_trace_context(request, span)

            start_time = time.time()
            status_code = 500
            try:
                response = await call_next(request)
                status_code = response.status_code

                span.set_attribute("http.status_code", status_code)
                duration_ms = (time.time() - start_time) * 1000
                span.set_attribute("http.response_time_ms", round(duration_ms, 2))

                self._add_user_context(request, span)

                self._inject_trace_headers(response, span)

                if status_code >= 500:
                    span.set_status(
                        StatusCode.ERROR,
                        f"HTTP {status_code}",
                    )
                elif status_code >= 400:
                    span.set_status(
                        StatusCode.OK,
                        f"HTTP {status_code} (client error)",
                    )
                else:
                    span.set_status(StatusCode.OK)

                return response

            except Exception as exc:
                duration_ms = (time.time() - start_time) * 1000
                span.set_attribute("http.status_code", 500)
                span.set_attribute("http.response_time_ms", round(duration_ms, 2))
                span.set_status(StatusCode.ERROR, str(exc))
                span.record_exception(exc)
                logger.error(
                    f"请求异常: {request.method} {path} - {exc}",
                    exc_info=True,
                )
                raise

    @staticmethod
    def _sanitize_path(path: str) -> str:
        parts = path.split("/")
        normalized = []
        for part in parts:
            if part and part.isdigit():
                normalized.append("{id}")
            elif part and len(part) > 20 and all(c in "0123456789abcdef-" for c in part.lower()):
                normalized.append("{id}")
            else:
                normalized.append(part)
        return "/".join(normalized)

    @staticmethod
    def _extract_parent_context(request: Request) -> Optional[otel_context.Context]:
        traceparent = request.headers.get("traceparent", "")
        tracestate = request.headers.get("tracestate", "")

        if not traceparent:
            return None

        carrier = {"traceparent": traceparent}
        if tracestate:
            carrier["tracestate"] = tracestate

        try:
            ctx = extract_trace_context(carrier)
            return ctx
        except Exception:
            logger.debug("解析入站 traceparent 失败，使用本地 root span")
            return None

    @staticmethod
    def _inject_trace_context(request: Request, span: trace.Span):
        span.set_attribute(
            "trace_id",
            format(span.get_span_context().trace_id, "032x"),
        )
        span.set_attribute(
            "span_id",
            format(span.get_span_context().span_id, "016x"),
        )
        request.state.trace_id = format(span.get_span_context().trace_id, "032x")
        request.state.span_id = format(span.get_span_context().span_id, "016x")
        request.state.span = span

    @staticmethod
    def _inject_trace_headers(response: Response, span: trace.Span):
        trace_id = format(span.get_span_context().trace_id, "032x")
        span_id = format(span.get_span_context().span_id, "016x")
        response.headers["X-Trace-Id"] = trace_id
        response.headers["X-Span-Id"] = span_id

    @staticmethod
    def _add_user_context(request: Request, span: trace.Span):
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            span.set_attribute("user.id", str(user_id))

        endpoint_name = getattr(request.state, "endpoint_name", None)
        if endpoint_name:
            span.set_attribute("endpoint.name", endpoint_name)