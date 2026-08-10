"""
监控中间件

为 FastAPI 应用提供 HTTP 请求监控：
1. 自动记录请求计数与耗时
2. 在途请求 Gauge 增减
3. 跳过 /metrics 自身（避免循环）
"""
import time
from fastapi import Request, Response
from fastapi.responses import Response as FastAPIResponse
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Callable, Awaitable

from src.utils.metrics import record_http_request, http_requests_in_progress


class MonitoringMiddleware(BaseHTTPMiddleware):
    """
    HTTP 监控中间件

    自动记录每个请求的方法/路径/状态码/耗时，
    并维护在途请求 Gauge。
    """

    # 不统计的路径（避免自身循环或噪音）
    _SKIP_PATHS = {"/metrics", "/favicon.ico"}

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        method = request.method

        # 跳过 /metrics 自身
        if path in self._SKIP_PATHS:
            return await call_next(request)

        # 在途请求 +1
        http_requests_in_progress.inc()

        # 标准化路径（将 /api/v1/users/123 → /api/v1/users/{id}）
        endpoint = self._normalize_path(path)

        start_time = time.time()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            # 异常时仍记录指标
            status_code = 500
            raise
        finally:
            # 记录耗时
            duration = time.time() - start_time
            record_http_request(
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                duration=duration,
            )
            # 在途请求 -1
            http_requests_in_progress.dec()

    @staticmethod
    def _normalize_path(path: str) -> str:
        """
        标准化路径，将动态 ID 替换为占位符

        示例：
        - /api/v1/users/123 → /api/v1/users/{id}
        - /api/v1/plans/456/checkins → /api/v1/plans/{id}/checkins
        """
        if not path.startswith("/api"):
            return path

        parts = path.split("/")
        normalized = []
        for part in parts:
            if part and part.isdigit():
                normalized.append("{id}")
            elif part and len(part) > 20 and all(c in "0123456789abcdef-" for c in part.lower()):
                # UUID 或长 hash
                normalized.append("{id}")
            else:
                normalized.append(part)
        return "/".join(normalized)
