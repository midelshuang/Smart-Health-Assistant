"""
提供api接口、前端页面托管及核心业务逻辑的HTTP访问入口
"""

# web_api.py
import os
import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles


# 注：oauth2_scheme 已统一移至 Service/auth_service.py，避免与路由模块产生循环导入
from src.services.auth_service import oauth2_scheme  # noqa: F401  re-export for backward compat

from src.api.routes.auth import router as auth_router
from src.api.routes.diet import router as diet_router
from src.api.routes.rag import router as rag_router
from src.api.routes.plan import router as plan_router
from src.api.routes.user import router as user_router
from src.api.routes.feedback import router as feedback_router
from src.api.routes.v2 import router as v2_router
from src.api.middleware.version_control import APIVersionMiddleware, get_version_info
from src.api.middleware.monitoring import MonitoringMiddleware
from src.api.middleware.tracing import TracingMiddleware
from src.utils.metrics import (
    get_metrics,
    get_metrics_content_type,
    update_redis_status,
    update_cache_hit_rate,
)
from src.config.tracing import init_tracing, shutdown_tracing

logger = logging.getLogger(__name__)

# 创建 FastAPI 实例
app = FastAPI(
    title="HealthAI Platform",
    description="健康AI平台API",
    version="1.4.0"
)

# ============================================================
# 初始化 OpenTelemetry 分布式追踪
# ============================================================
init_tracing()

# ============================================================
# 注册中间件（注意：注册顺序与执行顺序相反）
# ============================================================
# API 版本控制中间件（业务层）
app.add_middleware(APIVersionMiddleware)
# 追踪中间件（为每个请求创建 root span）
app.add_middleware(TracingMiddleware)
# 监控中间件（最外层，捕获所有请求）
app.add_middleware(MonitoringMiddleware)


# ============================================================
# 挂载路由
# ============================================================
# V1 路由（当前主版本）
app.include_router(auth_router, prefix="/api/v1")
app.include_router(user_router, prefix="/api/v1")
app.include_router(diet_router, prefix="/api/v1")
app.include_router(rag_router, prefix="/api/v1")
app.include_router(plan_router, prefix="/api/v1")
app.include_router(feedback_router, prefix="/api/v1")

# V2 路由（V4 预留占位，正式启用后实现具体逻辑）
app.include_router(v2_router, prefix="/api/v2")


# ============================================================
# API 版本管理接口（不属于任何版本前缀，独立挂载）
# ============================================================
@app.get("/api/versions", summary="获取所有 API 版本信息", tags=["版本管理"])
async def list_api_versions():
    """返回所有 API 版本及其生命周期状态"""
    return get_version_info()


@app.get("/api/health", summary="API 网关健康检查", tags=["版本管理"])
async def api_health():
    """API 网关层健康检查（不依赖数据库）"""
    return {"status": "ok", "service": "api-gateway"}


@app.get("/api/v1/health", summary="V1 API 健康检查", tags=["版本管理"])
async def v1_health():
    """V1 API 健康检查"""
    return {"status": "ok", "version": "v1"}


@app.get("/api/v2/health", summary="V2 API 健康检查", tags=["版本管理"])
async def v2_health():
    """V2 API 健康检查（草稿状态）"""
    return {"status": "draft", "version": "v2", "message": "V2 接口尚未正式启用"}


# ============================================================
# Prometheus 监控接口
# ============================================================
@app.get("/metrics", include_in_schema=False, tags=["监控"])
async def metrics():
    """Prometheus 指标导出端点"""
    # 更新 Redis 状态（拉取最新值）
    try:
        from src.utils.redis_client import get_cache_stats
        stats = get_cache_stats()
        available = stats.get("available", False)
        update_redis_status(available=available)
    except Exception as e:
        logger.warning(f"更新 Redis 监控指标失败: {e}")
        update_redis_status(available=False)

    return Response(
        content=get_metrics(),
        media_type=get_metrics_content_type(),
    )


# ============================================================
# 前端静态资源挂载（演示页面）
# ============================================================
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    logger.info(f"已挂载静态资源: {_STATIC_DIR}")

    # 根路径返回演示页面
    @app.get("/", include_in_schema=False, tags=["前端"])
    async def root():
        """前端演示页面"""
        with open(_STATIC_DIR / "index.html", encoding="utf-8") as f:
            return Response(content=f.read(), media_type="text/html")


# 后续所有路由、中间件、事件都注册在这里
# app.include_router(diet_router, prefix="/api")
# app.add_middleware(...)


# ============================================================
# 应用生命周期事件
# ============================================================
@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时刷新并关闭追踪"""
    shutdown_tracing()