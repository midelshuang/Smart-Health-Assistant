# utils/logger.py
import logging
import os


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


def setup_logger(name="HealthAI_ETL", log_file="logs/etl_run.log"):
    """
    配置并返回一个全局通用的 Logger。
    包含控制台输出、文件记录和追踪关联功能。
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

    # 3. 定义格式（包含 trace_id 和 span_id）
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s '
        '[trace_id=%(trace_id)s span_id=%(span_id)s] - %(message)s'
    )

    # 4. 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(tracing_filter)
    logger.addHandler(console_handler)

    # 5. 文件处理器
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    file_handler.addFilter(tracing_filter)
    logger.addHandler(file_handler)

    return logger