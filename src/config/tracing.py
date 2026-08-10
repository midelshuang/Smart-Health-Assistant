"""
OpenTelemetry 分布式追踪配置模块

提供 TracerProvider 初始化、Exporter 配置、全局 Tracer 获取等能力。
支持 Jaeger（开发）和 OTLP Collector（生产）两种导出器。

使用方式：
    from src.config.tracing import init_tracing, get_tracer
    init_tracing()  # 应用启动时调用一次
    tracer = get_tracer("my_module")
    with tracer.start_as_current_span("operation") as span:
        ...
"""

import os
import logging
from typing import Optional, Dict, Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_ON,
    ALWAYS_OFF,
    ParentBased,
    TraceIdRatioBased,
)
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.propagators.textmap import TextMapPropagator
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

logger = logging.getLogger(__name__)

_TRACER_PROVIDER: Optional[TracerProvider] = None
_INITIALIZED = False

_SERVICE_NAME = "healthai-platform"
_SERVICE_VERSION = "2.0.0"
_DEPLOYMENT_ENVIRONMENT = os.getenv("APP_ENV", "development")


def init_tracing(
    service_name: Optional[str] = None,
    exporter_type: Optional[str] = None,
    jaeger_endpoint: Optional[str] = None,
    otlp_endpoint: Optional[str] = None,
    sample_rate: Optional[float] = None,
    extra_resource_attrs: Optional[Dict[str, str]] = None,
) -> TracerProvider:
    """
    初始化 OpenTelemetry TracerProvider（全局只初始化一次）

    Args:
        service_name: 服务名称，默认从环境变量 OTEL_SERVICE_NAME 获取
        exporter_type: 导出器类型 jaeger / otlp / console / none，
                       默认从环境变量 OTEL_EXPORTER_TYPE 获取
        jaeger_endpoint: Jaeger collector HTTP endpoint
        otlp_endpoint: OTLP Collector gRPC endpoint
        sample_rate: 采样率 0.0~1.0，默认 1.0（全采样）
        extra_resource_attrs: 额外的资源属性

    Returns:
        已初始化的 TracerProvider
    """
    global _TRACER_PROVIDER, _INITIALIZED

    if _INITIALIZED and _TRACER_PROVIDER is not None:
        logger.warning("OpenTelemetry TracerProvider 已初始化，跳过重复初始化")
        return _TRACER_PROVIDER

    service_name = service_name or os.getenv(
        "OTEL_SERVICE_NAME", _SERVICE_NAME
    )
    exporter_type = exporter_type or os.getenv(
        "OTEL_EXPORTER_TYPE", "jaeger"
    ).lower()
    sample_rate = sample_rate if sample_rate is not None else float(
        os.getenv("OTEL_SAMPLE_RATE", "1.0")
    )

    resource_attrs = {
        "service.name": service_name,
        "service.version": _SERVICE_VERSION,
        "deployment.environment": _DEPLOYMENT_ENVIRONMENT,
        "telemetry.sdk.language": "python",
        "telemetry.sdk.name": "opentelemetry",
    }
    if extra_resource_attrs:
        resource_attrs.update(extra_resource_attrs)
    resource = Resource(attributes=resource_attrs)

    sampler = _create_sampler(sample_rate)
    provider = TracerProvider(resource=resource, sampler=sampler)

    exporter = _create_exporter(
        exporter_type=exporter_type,
        jaeger_endpoint=jaeger_endpoint,
        otlp_endpoint=otlp_endpoint,
    )

    if exporter is not None:
        if exporter_type == "console":
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        else:
            provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info(
            f"OpenTelemetry: 已配置 {exporter_type} 导出器"
            f"(service={service_name}, sample_rate={sample_rate})"
        )
    else:
        logger.info("OpenTelemetry: 未配置导出器（追踪数据仅在内存中）")

    _setup_propagation()

    trace.set_tracer_provider(provider)

    _TRACER_PROVIDER = provider
    _INITIALIZED = True

    return provider


def _create_sampler(sample_rate: float):
    if sample_rate >= 1.0:
        return ALWAYS_ON
    if sample_rate <= 0.0:
        return ParentBased(ALWAYS_OFF)
    return ParentBased(TraceIdRatioBased(rate=sample_rate))


def _create_exporter(
    exporter_type: str,
    jaeger_endpoint: Optional[str] = None,
    otlp_endpoint: Optional[str] = None,
):
    if exporter_type == "none":
        return None

    if exporter_type == "console":
        return ConsoleSpanExporter()

    if exporter_type == "jaeger":
        endpoint = jaeger_endpoint or os.getenv(
            "OTEL_EXPORTER_JAEGER_ENDPOINT", "http://localhost:14268/api/traces"
        )
        try:
            from opentelemetry.exporter.jaeger.thrift import JaegerExporter

            return JaegerExporter(
                agent_host_name=endpoint.split("//")[1].split(":")[0]
                if "://" in endpoint
                else "localhost",
                agent_port=int(endpoint.split(":")[-1].split("/")[0])
                if ":" in endpoint
                else 6831,
            )
        except ImportError:
            try:
                from opentelemetry.exporter.jaeger.proto_grpc import (
                    JaegerExporter as JaegerGrpcExporter,
                )

                return JaegerGrpcExporter(endpoint=endpoint)
            except ImportError:
                logger.warning(
                    "Jaeger exporter 不可用，降级为 ConsoleSpanExporter"
                )
                return ConsoleSpanExporter()
        except Exception as e:
            logger.warning(f"Jaeger exporter 初始化失败: {e}，降级为 ConsoleSpanExporter")
            return ConsoleSpanExporter()

    if exporter_type == "otlp":
        endpoint = otlp_endpoint or os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"
        )
        # 生产环境启用 TLS（HTTPS/gRPC over TLS），开发环境用明文 gRPC
        insecure = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true"
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            return OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
        except ImportError:
            logger.warning(
                "OTLP exporter 不可用，降级为 ConsoleSpanExporter"
            )
            return ConsoleSpanExporter()
        except Exception as e:
            logger.warning(f"OTLP exporter 初始化失败: {e}，降级为 ConsoleSpanExporter")
            return ConsoleSpanExporter()

    logger.warning(f"未知的导出器类型: {exporter_type}，降级为 ConsoleSpanExporter")
    return ConsoleSpanExporter()


def _setup_propagation():
    pass


def get_tracer(name: str) -> trace.Tracer:
    """
    获取一个命名的 Tracer

    Args:
        name: tracer 名称，通常使用模块名如 "src.core.nodes"

    Returns:
        OpenTelemetry Tracer 实例
    """
    return trace.get_tracer(name)


def get_current_span() -> Optional[trace.Span]:
    """获取当前上下文中的活动 Span"""
    return trace.get_current_span()


def trace_context_to_dict() -> Dict[str, str]:
    """
    将当前 Trace Context 序列化为字典（用于跨服务传播）

    Returns:
        包含 traceparent / tracestate 的字典
    """
    propagator = TraceContextTextMapPropagator()
    carrier: Dict[str, str] = {}
    propagator.inject(carrier)
    return carrier


def extract_trace_context(
    carrier: Dict[str, str],
) -> Optional[Context]:
    """
    从外部载体（HTTP headers 等）提取 Trace Context

    Args:
        carrier: 包含 traceparent 的字典

    Returns:
        提取的 Context，若无有效 trace 则返回 None
    """
    propagator = TraceContextTextMapPropagator()
    return propagator.extract(carrier)


def shutdown_tracing():
    """关闭 TracerProvider，刷新所有待处理的 Span 数据"""
    global _INITIALIZED, _TRACER_PROVIDER
    if _TRACER_PROVIDER is not None:
        _TRACER_PROVIDER.force_flush()
        _TRACER_PROVIDER = None
        _INITIALIZED = False
        logger.info("OpenTelemetry TracerProvider 已关闭")


def is_tracing_initialized() -> bool:
    """检查追踪是否已初始化"""
    return _INITIALIZED


def inject_trace_headers(headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    将当前 Trace Context 注入到 HTTP 请求头中（用于跨服务传播）

    Args:
        headers: 已有的 headers 字典（可选）

    Returns:
        合并了 traceparent / tracestate 的 headers 字典
    """
    if not is_tracing_initialized():
        return headers or {}

    carrier: Dict[str, str] = {}
    propagator = TraceContextTextMapPropagator()
    propagator.inject(carrier)

    result = dict(headers or {})
    if "traceparent" in carrier:
        result["traceparent"] = carrier["traceparent"]
    if "tracestate" in carrier:
        result["tracestate"] = carrier["tracestate"]
    return result