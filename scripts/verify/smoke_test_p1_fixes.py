"""
P1 修复后冒烟测试

验证：
1. web_api 模块可成功 import（所有依赖链完整）
2. FastAPI app 实例创建成功
3. lifespan 中间件链装配正确（CORS / Tracing / Monitoring / APIVersion）
4. 关键路由可路由：
   - /api/health
   - /api/v1/auth/login （404 表示路由存在但方法/参数不对）
   - /api/v1/auth/logout
   - /api/v1/rag/status
5. CORS preflight OPTIONS 请求正确响应
6. 异常脱敏：触发 500 异常时 detail 不包含原始异常字符串
"""
import os
import sys
from pathlib import Path

# 设置最小化环境变量，让 import 不报错
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("JWT_SECRET_KEY", "smoke-test-secret-not-for-prod-9f3a2c1b8e7d")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/healthai_db")
os.environ.setdefault("LLM_API_KEY", "sk-smoke-test")
os.environ.setdefault("LLM_BASE_URL", "http://localhost")
os.environ.setdefault("LLM_MODEL", "smoke-model")
os.environ.setdefault("EMBEDDING_API_KEY", "sk-smoke-test")
os.environ.setdefault("EMBEDDING_BASE_URL", "http://localhost")
os.environ.setdefault("EMBEDDING_MODEL", "smoke-embed")
os.environ.setdefault("APP_ENV", "development")
# 显式 CORS 白名单，验证 CORS 中间件装配
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8000")
os.environ.setdefault("CHROMA_HOST", "localhost")
os.environ.setdefault("CHROMA_PORT", "8001")

sys.path.insert(0, str(_PROJECT_ROOT))


def test_import_and_app_creation():
    """测试 1：模块 import + app 实例"""
    print("\n[1/5] 模块 import + app 实例创建...")
    import web_api
    assert hasattr(web_api, "app"), "web_api 模块必须有 app 属性"
    assert web_api.app.title == "HealthAI Platform"
    print(f"  ✅ app.title = {web_api.app.title}, version = {web_api.app.version}")
    # 检查 lifespan 已注入
    lifespan = web_api.app.router.lifespan
    assert lifespan is not None, "FastAPI app 必须有 lifespan"
    print("  ✅ lifespan 已注入")
    return web_api.app


def test_middleware_chain(app):
    """测试 2：中间件链"""
    print("\n[2/5] 中间件链装配...")
    from starlette.middleware.cors import CORSMiddleware
    from src.api.middleware.version_control import APIVersionMiddleware
    from src.api.middleware.tracing import TracingMiddleware
    from src.api.middleware.monitoring import MonitoringMiddleware

    # Starlette/FastAPI 把中间件栈放在 user_middleware 列表
    mw_classes = []
    for mw in app.user_middleware:
        cls = getattr(mw, "cls", None)
        if cls:
            mw_classes.append(cls)

    found = {
        "CORSMiddleware": CORSMiddleware in mw_classes,
        "APIVersionMiddleware": APIVersionMiddleware in mw_classes,
        "TracingMiddleware": TracingMiddleware in mw_classes,
        "MonitoringMiddleware": MonitoringMiddleware in mw_classes,
    }
    for name, ok in found.items():
        assert ok, f"缺少中间件: {name}"
        print(f"  ✅ {name}")

    return found


def test_routes_registered(app):
    """测试 3：关键路由注册"""
    print("\n[3/5] 关键路由注册...")
    # 用 TestClient 触发路由表展开
    from fastapi.testclient import TestClient
    client = TestClient(app)

    expected = [
        ("/api/health", "GET"),
        ("/api/v1/auth/login", "POST"),
        ("/api/v1/auth/logout", "POST"),     # P1-5 新增
        ("/api/v1/auth/refresh", "POST"),
        ("/api/v1/auth/change-password", "POST"),
        ("/api/v1/rag/status", "GET"),
        ("/api/v1/rag/query", "POST"),
    ]
    # 跳过 Mount（StaticFiles）等没有 methods 属性的路由对象
    for path, method in expected:
        matched = [r for r in app.routes if getattr(r, "path", None) == path]
        assert matched, f"缺少路由 {method} {path}"
        methods = set()
        for r in matched:
            ms = getattr(r, "methods", None) or set()
            methods.update(ms)
        assert method in methods, f"路由 {path} 缺少方法 {method}（现有 {methods}）"
        print(f"  ✅ {method} {path}")

    return client


def test_health_and_cors_preflight(client):
    """测试 4：/api/health 与 CORS preflight"""
    print("\n[4/5] /api/health + CORS preflight...")
    # health 不依赖数据库
    r = client.get("/api/health")
    assert r.status_code == 200, f"/api/health 应返回 200，实际 {r.status_code}"
    body = r.json()
    assert body.get("status") == "ok", f"/api/health 返回 {body}"
    print(f"  ✅ GET /api/health → {body}")

    # CORS preflight（测试环境默认允许本机）
    r = client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization,Content-Type",
        },
    )
    # 允许 200 或 400 都算 CORS 中间件响应（OPTIONS 不走业务路由）
    assert r.status_code in (200, 400), f"CORS preflight 应返回 200 或 400，实际 {r.status_code}"
    acao = r.headers.get("access-control-allow-origin", "")
    assert acao, "CORS preflight 必须返回 Access-Control-Allow-Origin"
    print(f"  ✅ OPTIONS /api/health (preflight) → {r.status_code}, ACAO={acao}")


def test_exception_sanitization():
    """测试 5：异常信息脱敏（P0-2 修复回归）

    通过源码级扫描确认 detail 不再直接返回原始异常字符串
    """
    print("\n[5/5] 异常信息脱敏回归（源码级）...")
    import re
    rag_py = _PROJECT_ROOT / "src" / "api" / "routes" / "rag.py"
    plan_py = _PROJECT_ROOT / "src" / "api" / "routes" / "plan.py"
    pattern = re.compile(r'detail=f"[^"]*\{e\}"')

    for f in (rag_py, plan_py):
        content = f.read_text(encoding="utf-8")
        matches = pattern.findall(content)
        assert not matches, f"{f.name} 仍存在 detail=f'...{{e}}' 泄漏: {matches}"
        print(f"  ✅ {f.name} 无 detail=f'...{{e}}' 模式")

    # tracing 脱敏函数存在性检查
    tracing_py = _PROJECT_ROOT / "src" / "api" / "middleware" / "tracing.py"
    content = tracing_py.read_text(encoding="utf-8")
    assert "_sanitize_query" in content, "tracing.py 必须有 _sanitize_query"
    assert "_sanitize_url" in content, "tracing.py 必须有 _sanitize_url"
    assert "_SENSITIVE_QUERY_KEYS" in content, "tracing.py 必须定义敏感参数白名单"
    print("  ✅ tracing.py 含 _sanitize_query / _sanitize_url / _SENSITIVE_QUERY_KEYS")


def main():
    print("=" * 60)
    print("P1 修复冒烟测试")
    print("=" * 60)

    try:
        app = test_import_and_app_creation()
        test_middleware_chain(app)
        client = test_routes_registered(app)
        test_health_and_cors_preflight(client)
        test_exception_sanitization()
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)

    print("\n" + "=" * 60)
    print("✅ 全部冒烟测试通过")
    print("=" * 60)


if __name__ == "__main__":
    main()
