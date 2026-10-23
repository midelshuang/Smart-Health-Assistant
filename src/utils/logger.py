# utils/logger.py
import json
import logging
import os
import re


# ============================================================
# 敏感字段脱敏正则（密码 / Token / API Key / Secret）
# ============================================================
_SENSITIVE_KEYS = re.compile(
    r'(password|passwd|secret|token|api[_-]?key|authorization|refresh[_-]?token)',
    re.IGNORECASE,
)


class SensitiveDataFilter(logging.Filter):
    """
    敏感数据脱敏过滤器

    对日志消息中形如 key=value 或 "key": "value" 的敏感字段值进行脱敏，
    防止密码 / Token / API Key 明文写入日志文件或发送到 Sentry / Loki。
    """

    # 匹配 key=value 或 key:"value" 或 "key":"value"
    _KV_PATTERN = re.compile(
        r'(["\']?)(password|passwd|secret|token|api[_-]?key|authorization|refresh[_-]?token)(["\']?)\s*[:=]\s*(["\']?)([^"\'\s,}\]]+)(["\']?)',
        re.IGNORECASE,
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._KV_PATTERN.sub(
                lambda m: f'{m.group(1)}{m.group(2)}{m.group(3)}={m.group(4)}***{m.group(6)}',
                record.msg,
            )
        return True


class TracingLogFilter(logging.Filter):
    """
    日志追踪关联过滤器

    自动从当前 OpenTelemetry Span Context 提取 trace_id 和 span_id，
    注入到日志记录中，实现日志与追踪链路的关联。

    日志格式示例：
        2025-08-04 10:00:00 - RAG_Nodes - INFO ...
        [trace_id=abc123... span_id=def456...] 答案生成完成
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from opentelemetry import trace
            span = trace.get_current_span()
            if span and span.is_valid:
                ctx = span.get_span_context()
                record.trace_id = format(ctx.trace_id, "032x")
                record.span_id = format(ctx.span_id, "016x")
            else:
                record.trace_id = "-"
                record.span_id = "-"
        except Exception:
            record.trace_id = "-"
            record.span_id = "-"
        return True


class JsonFormatter(logging.Formatter):
    """
    JSON 格式化器（供 Promtail 抓取发送到 Loki）

    输出格式：
        {"timestamp":"2026-09-16T10:00:00","level":"INFO","logger":"app",
         "trace_id":"abc...","span_id":"def...","message":"..."}
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", "-"),
            "span_id": getattr(record, "span_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def _get_formatter():
    """根据 LOG_FORMAT 环境变量选择日志格式化器

    - text（默认）：人类可读格式，适合开发环境
    - json：结构化 JSON，适合 Promtail → Loki 集中化日志收集
    """
    log_format = os.getenv("LOG_FORMAT", "text").lower().strip()
    if log_format == "json":
        return JsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
    # 默认文本格式
    return logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s '
        '[trace_id=%(trace_id)s span_id=%(span_id)s] - %(message)s'
    )


def setup_logger(name="HealthAI_ETL", log_file="logs/etl_run.log"):
    """
    配置并返回一个全局通用的 Logger。
    包含控制台输出、文件记录、追踪关联和敏感数据脱敏功能。

    环境变量：
        LOG_FORMAT=json  → JSON 格式输出（供 Promtail → Loki 收集）
        LOG_FORMAT=text  → 人类可读格式（默认）
    """
    # 1. 确保日志目录存在
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logger = logging.getLogger(name)

    # 防止重复添加 Handler (非常重要！)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    # 2. 追踪关联过滤器（自动注入 trace_id/span_id）
    tracing_filter = TracingLogFilter()

    # 3. 敏感数据脱敏过滤器
    sensitive_filter = SensitiveDataFilter()

    # 4. 格式化器（支持 text / json 切换）
    formatter = _get_formatter()

    # 5. 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(tracing_filter)
    console_handler.addFilter(sensitive_filter)
    logger.addHandler(console_handler)

    # 6. 文件处理器
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    file_handler.addFilter(tracing_filter)
    file_handler.addFilter(sensitive_filter)
    logger.addHandler(file_handler)

    return logger