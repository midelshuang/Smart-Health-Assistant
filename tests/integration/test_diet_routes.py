# -*- coding: utf-8 -*-
"""R07 回归测试：/diet/summary 静态路由必须先于 /{log_id} 动态路由匹配。

背景缺陷：/summary 注册在 /{log_id} 之后，FastAPI 按注册顺序匹配，
导致 GET /diet/summary 被 /{log_id} 截获，"summary" 无法转 int → 422。

本测试不依赖真实数据库：get_db 注入 None，服务层函数打桩。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import diet
from src.repositories.session import get_db
from src.services.auth_service import get_current_user_id


def _build_client(monkeypatch) -> TestClient:
    """构建挂载 diet 路由的最小应用，注入替身依赖与替身服务函数。"""
    summary_calls, detail_calls = [], []

    def fake_summary(db, user_id, log_date=None):
        summary_calls.append({"user_id": user_id, "log_date": log_date})
        return {"date": str(log_date or "2026-10-26"), "total_calories": 123.0, "meals": {}}

    def fake_detail(db, user_id, obj_id):
        detail_calls.append({"user_id": user_id, "obj_id": obj_id})
        return {"log_id": obj_id, "user_id": user_id}

    # 两个服务函数都在路由处理函数内/顶部从 diet_service 导入，替换模块属性即可生效
    monkeypatch.setattr("src.services.diet_service.get_daily_nutrition_summary", fake_summary)
    monkeypatch.setattr("src.services.diet_service.get_diet_log_detail", fake_detail)
    # get_diet_log_detail 还被 diet.py 顶部 from-import 绑定了一份，
    # 必须把路由模块命名空间里的名字一并替换，否则打桩不生效
    monkeypatch.setattr("src.api.routes.diet.get_diet_log_detail", fake_detail)

    app = FastAPI()
    app.include_router(diet.router, prefix="/api/v1")

    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_current_user_id] = lambda: "test-user"

    client = TestClient(app, raise_server_exceptions=False)
    client._summary_calls = summary_calls  # type: ignore[attr-defined]
    client._detail_calls = detail_calls    # type: ignore[attr-defined]
    return client


def test_summary_route_not_shadowed_by_dynamic_route(monkeypatch):
    """GET /diet/summary 必须命中汇总接口（200），而不是被 /{log_id} 截获返回 422。"""
    client = _build_client(monkeypatch)

    resp = client.get("/api/v1/diet/summary")

    assert resp.status_code == 200, f"预期 200，实际 {resp.status_code}：{resp.text}"
    body = resp.json()
    assert body["total_calories"] == 123.0
    assert len(client._summary_calls) == 1  # type: ignore[attr-defined]
    assert client._summary_calls[0]["user_id"] == "test-user"  # type: ignore[attr-defined]


def test_summary_route_accepts_date_param(monkeypatch):
    """带 log_date 查询参数时正常透传。"""
    client = _build_client(monkeypatch)

    resp = client.get("/api/v1/diet/summary", params={"log_date": "2026-10-01"})

    assert resp.status_code == 200
    assert resp.json()["date"] == "2026-10-01"


def test_dynamic_route_still_resolves_integer_id(monkeypatch):
    """修复不得影响正常 /{log_id} 路由。"""
    client = _build_client(monkeypatch)

    resp = client.get("/api/v1/diet/42")

    assert resp.status_code == 200
    assert resp.json()["log_id"] == 42
    assert client._detail_calls[0]["obj_id"] == 42  # type: ignore[attr-defined]


def test_non_integer_id_still_returns_422(monkeypatch):
    """真传非数字 id 时仍应得到 422（这是 /{log_id} 本身的合法行为）。"""
    client = _build_client(monkeypatch)

    resp = client.get("/api/v1/diet/not-a-number")

    assert resp.status_code == 422
