# -*- coding: utf-8 -*-
"""R04 回归测试：/rag/index 仅限 admin + 目录白名单；/rag/chunk 需登录。

背景缺陷：
1. /rag/index 只要求登录（普通用户即可），且 dir_path 无白名单——
   登录用户可让服务器读取任意目录并入库（任意目录读/知识库投毒）；
2. /rag/chunk 完全无认证，chunk_id 形如 guideline_0 可枚举，
   未认证用户可批量拉取知识库全文。

打桩方式：真实签发 JWT + 假 DB Session 替身 + 假 pipeline，不依赖真实服务。
"""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import rag
from src.config import settings
from src.repositories.session import get_db
from src.services.auth_service import create_access_token


# ============================================================
# 打桩工具
# ============================================================

class _FakeDB:
    """仅实现认证依赖用到的 query().filter().first() 查询链。"""

    def __init__(self, user):
        self._user = user

    def query(self, model):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._user

    def close(self):
        pass


def _fake_user(role):
    return SimpleNamespace(
        external_id="ext-1", is_active=True, role=role, username="tester")


def _build_client(db):
    app = FastAPI()
    app.include_router(rag.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _auth_headers():
    token = create_access_token({"sub": "ext-1"})
    return {"Authorization": f"Bearer {token}"}


def _stub_pipeline(monkeypatch, index_calls):
    class _FakePipeline:
        def index_documents(self, dir_path=None, source_type="guideline"):
            index_calls.append({"dir_path": dir_path, "source_type": source_type})
            return {"success": True, "chunks_processed": 3, "collection_count": 99}

    monkeypatch.setattr("src.api.routes.rag.get_pipeline", lambda: _FakePipeline())


# ============================================================
# 1. /rag/index：认证与角色
# ============================================================

def test_index_requires_token(monkeypatch):
    """无 token → 401。"""
    _stub_pipeline(monkeypatch, [])
    client = _build_client(_FakeDB(None))

    resp = client.post("/api/v1/rag/index", json={})

    assert resp.status_code == 401


def test_index_rejects_non_admin(monkeypatch):
    """普通用户（user 角色）→ 403。"""
    _stub_pipeline(monkeypatch, [])
    client = _build_client(_FakeDB(_fake_user("user")))

    resp = client.post("/api/v1/rag/index", json={}, headers=_auth_headers())

    assert resp.status_code == 403


# ============================================================
# 2. /rag/index：目录白名单
# ============================================================

def test_index_allows_default_dir(monkeypatch):
    """admin + 未显式传 dir_path → 使用默认数据目录，放行。"""
    index_calls = []
    _stub_pipeline(monkeypatch, index_calls)
    client = _build_client(_FakeDB(_fake_user("admin")))

    resp = client.post("/api/v1/rag/index", json={}, headers=_auth_headers())

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert len(index_calls) == 1


def test_index_allows_whitelisted_dir_and_subdir(monkeypatch):
    """admin + 白名单根目录及其子目录 → 放行。"""
    index_calls = []
    _stub_pipeline(monkeypatch, index_calls)
    client = _build_client(_FakeDB(_fake_user("admin")))
    root = settings.Authoritative_file_path

    resp1 = client.post(
        "/api/v1/rag/index", json={"dir_path": root}, headers=_auth_headers())
    resp2 = client.post(
        "/api/v1/rag/index",
        json={"dir_path": root + "/sub"},
        headers=_auth_headers(),
    )

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert len(index_calls) == 2


def test_index_rejects_outside_whitelist(monkeypatch):
    """admin + 白名单外目录（如系统目录）→ 403，pipeline 不被调用。"""
    index_calls = []
    _stub_pipeline(monkeypatch, index_calls)
    client = _build_client(_FakeDB(_fake_user("admin")))

    resp = client.post(
        "/api/v1/rag/index",
        json={"dir_path": "C:/Windows/System32"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 403
    assert index_calls == [], "白名单外目录绝不允许触发入库"


def test_index_rejects_traversal_escape(monkeypatch):
    """../ 穿越逃出白名单根 → 403。"""
    index_calls = []
    _stub_pipeline(monkeypatch, index_calls)
    client = _build_client(_FakeDB(_fake_user("admin")))
    escape_path = settings.Authoritative_file_path + "/../../outside"

    resp = client.post(
        "/api/v1/rag/index", json={"dir_path": escape_path}, headers=_auth_headers())

    assert resp.status_code == 403
    assert index_calls == []


# ============================================================
# 3. /rag/chunk：需登录
# ============================================================

def test_chunk_requires_login():
    """游客（无 token）→ 401，不可枚举拉取知识库。"""
    client = _build_client(_FakeDB(None))

    resp = client.get("/api/v1/rag/chunk/guideline_0")

    assert resp.status_code == 401


def test_chunk_allows_logged_in_user(monkeypatch):
    """登录用户 → 通过认证进入数据层，命中假 DB 返回 chunk 详情。"""
    chunk = SimpleNamespace(
        chunk_id="guideline_0", content="指南内容", file_name="guide.pdf",
        file_path="/data/guide.pdf", source_type="guideline",
        guideline_title="测试指南", chunk_index=0, char_count=4,
        audience="adult", disease_tags="", region="全国",
        age_group="middle_aged", doc_type="guideline_content",
    )

    class _FakeChunkDB(_FakeDB):
        def first(self):
            return chunk

    # 路由内部函数级 import SessionLocal，替换模块属性即生效
    monkeypatch.setattr(
        "src.repositories.session.SessionLocal", lambda: _FakeChunkDB(None))

    client = _build_client(_FakeDB(_fake_user("user")))

    resp = client.get("/api/v1/rag/chunk/guideline_0", headers=_auth_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body["chunk_id"] == "guideline_0"
    assert body["content"] == "指南内容"
