"""
提供api接口、前端页面托管及核心业务逻辑的HTTP访问入口
"""

# web_api.py
import os
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware


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


# ============================================================
# Sentry 集中化错误监控初始化
# 未配置 SENTRY_DSN 时静默跳过，不影响主流程
# ============================================================
def init_sentry():
    """初始化 Sentry SDK，自动捕获 FastAPI 未处理异常

    环境变量：
        SENTRY_DSN   - Sentry 项目 DSN（未设置则跳过）
        APP_ENV      - production 时启用，其他环境默认禁用
        OTEL_SAMPLE_RATE - 采样率（可选，默认 1.0）
    """
    sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
    if not sentry_dsn:
        logger.info("SENTRY_DSN 未配置，跳过 Sentry 初始化")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration

        # 开发环境也允许上报（通过 APP_ENV 控制 traces_sample_rate）
        app_env = os.getenv("APP_ENV", "development").lower()
        sample_rate = float(os.getenv("OTEL_SAMPLE_RATE", "1.0"))

        sentry_sdk.init(
            dsn=sentry_dsn,
            environment=app_env,
            traces_sample_rate=sample_rate,
            send_default_pii=False,  # 不发送 PII（个人身份信息）
            integrations=[FastApiIntegration()],
            # 过滤敏感信息：不上报 Authorization / Cookie 头
            before_send=lambda event, hint: _sentry_scrub(event),
        )
        logger.info(f"Sentry 已初始化 (env={app_env}, sample_rate={sample_rate})")
    except ImportError:
        logger.warning("sentry-sdk 未安装，跳过 Sentry 初始化")
    except Exception as e:
        logger.error(f"Sentry 初始化失败（不影响启动）: {e}")


def _sentry_scrub(event):
    """Sentry 事件脱敏：移除请求头中的敏感字段"""
    try:
        headers = event.get("request", {}).get("headers", {})
        sensitive_keys = {"authorization", "cookie", "x-api-key", "x-token"}
        for key in list(headers.keys()):
            if key.lower() in sensitive_keys:
                headers[key] = "[REDACTED]"
    except Exception:
        pass
    return event


# ============================================================
# 应用生命周期管理（FastAPI lifespan 上下文管理器）
# 取代 @app.on_event("startup"/"shutdown")，避免 deprecation warning
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动钩子 → 服务运行 → 关闭钩子

    启动阶段（yield 之前）：
    - 初始化 Sentry 错误监控（必须在中间件首次执行前完成）
    - 初始化 OpenTelemetry 分布式追踪（必须在中间件首次执行前完成）
    - 预热 Redis / ChromaDB 连接可在此扩展（目前懒加载，不强制）

    关闭阶段（yield 之后）：
    - 刷新并关闭追踪导出器，确保 span 不丢失
    - 刷新 Sentry 待发送事件
    - 数据库连接池由 SQLAlchemy 引擎自动管理，无需显式关闭
    """
    logger.info("HealthAI 应用启动中...")
    init_sentry()
    logger.info("Sentry 错误监控已初始化（若已配置 SENTRY_DSN）")
    init_tracing()
    logger.info("OpenTelemetry 分布式追踪已初始化")

    yield  # 服务运行期间

    logger.info("HealthAI 应用关闭中，开始清理资源...")
    # 刷新 Sentry 待发送事件
    try:
        import sentry_sdk
        sentry_sdk.flush(timeout=5)
    except Exception:
        pass
    shutdown_tracing()
    logger.info("HealthAI 应用已关闭")


# 创建 FastAPI 实例
app = FastAPI(
    title="HealthAI Platform",
    description="健康AI平台API",
    version="1.4.0",
    lifespan=lifespan,
)

# ============================================================
# 注册中间件（注意：注册顺序与执行顺序相反）
# ============================================================
# CORS 跨域中间件（最外层，确保预检请求先于业务中间件处理）
# 生产环境必须通过环境变量 CORS_ALLOWED_ORIGINS 配置白名单域名，逗号分隔
# 例如：CORS_ALLOWED_ORIGINS=https://healthai.example.com,https://www.healthai.example.com
_cors_origins_raw = os.getenv("CORS_ALLOWED_ORIGINS", "")
if _cors_origins_raw.strip():
    # 显式白名单模式（推荐生产环境）
    _cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
else:
    # 兜底：开发环境允许本机调试（生产部署必须显式配置白名单）
    _app_env = os.getenv("APP_ENV", "development").lower()
    _cors_origins = ["http://localhost:3000", "http://localhost:8000", "http://127.0.0.1:8000"] \
        if _app_env == "development" else []

if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Trace-Id", "X-Span-Id"],
        max_age=600,
    )
    logger.info(f"CORS 已启用，允许来源: {_cors_origins}")
else:
    logger.warning("CORS 未配置：CORS_ALLOWED_ORIGINS 为空且非开发环境，跨域请求将被浏览器拦截")

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
            content = f.read()
        # 添加 no-cache 头，确保每次都加载最新版本
        return Response(
            content=content,
            media_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )


# 后续所有路由、中间件、事件都注册在这里
# app.include_router(diet_router, prefix="/api")
# app.add_middleware(...)

# 注：应用生命周期（启动/关闭）已通过 lifespan 上下文管理器统一处理
# 见上方 @asynccontextmanager async def lifespan(app) 定义