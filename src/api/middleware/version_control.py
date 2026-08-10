"""
API 版本控制模块

设计思路：
1. 通过 prefix 在路由注册时绑定版本（如 /api/v1/、/api/v2/）
2. 中间件统一注入 X-API-Version 响应头
3. 当请求未带版本号时，自动重定向到最新稳定版
4. 通过 API_VERSION_CONFIG 控制每个版本的生命周期状态
"""
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Callable, Awaitable
import re
import logging

logger = logging.getLogger(__name__)

# ============================================================
# 版本配置：每个版本的生命周期管理
# ============================================================
API_VERSION_CONFIG = {
    "v1": {
        "status": "stable",          # stable / deprecated / sunset
        "released_at": "2025-01-01",
        "sunset_at": None,            # None 表示未计划下线
        "description": "初始版本（当前主版本）",
    },
    "v2": {
        "status": "draft",            # 草稿，未正式发布
        "released_at": None,
        "sunset_at": None,
        "description": "V4 预留版本（深度个性化推荐 + 多模态支持）",
    },
}

# 当前默认版本（请求未带版本号时使用）
DEFAULT_API_VERSION = "v1"

# 支持的版本列表
SUPPORTED_VERSIONS = list(API_VERSION_CONFIG.keys())

# 路径匹配：/api/{version}/xxx
_API_VERSION_PATTERN = re.compile(r"^/api/(v\d+)/.*")


def get_api_version_from_path(path: str) -> str | None:
    """从请求路径中提取 API 版本号"""
    match = _API_VERSION_PATTERN.match(path)
    return match.group(1) if match else None


def get_latest_stable_version() -> str:
    """获取最新的 stable 版本"""
    for ver, cfg in API_VERSION_CONFIG.items():
        if cfg["status"] == "stable":
            return ver
    return DEFAULT_API_VERSION


class APIVersionMiddleware(BaseHTTPMiddleware):
    """
    API 版本控制中间件

    功能：
    1. 自动注入 X-API-Version 响应头
    2. 已弃用版本返回 Sunset + Deprecation 头
    3. 请求未带版本号时，自动重定向到默认版本（仅对 /api/ 开头生效）
    4. 不支持的版本返回 400
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path

        # 只对 /api/ 开头的请求生效
        if not path.startswith("/api"):
            return await call_next(request)

        # 根路径 /api 或 /api/ → 重定向到默认版本的健康检查
        if path in ("/api", "/api/"):
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=f"/api/{DEFAULT_API_VERSION}/health", status_code=302)

        # 提取版本号
        version = get_api_version_from_path(path)

        if version and version not in API_VERSION_CONFIG:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "unsupported_api_version",
                    "message": f"API 版本 '{version}' 不被支持",
                    "supported_versions": SUPPORTED_VERSIONS,
                },
            )

        # 执行请求
        response = await call_next(request)

        # 注入版本响应头
        if version:
            response.headers["X-API-Version"] = version
            cfg = API_VERSION_CONFIG.get(version, {})
            response.headers["X-API-Status"] = cfg.get("status", "unknown")

            # 已弃用版本：添加 Sunset + Deprecation 头（遵循 RFC 8594 / RFC 7234）
            if cfg.get("status") == "deprecated":
                response.headers["Deprecation"] = "true"
                if cfg.get("sunset_at"):
                    response.headers["Sunset"] = cfg["sunset_at"]

        return response


def get_version_info() -> dict:
    """获取所有 API 版本信息（供 /api/versions 接口使用）"""
    return {
        "default_version": DEFAULT_API_VERSION,
        "latest_stable": get_latest_stable_version(),
        "versions": API_VERSION_CONFIG,
    }
