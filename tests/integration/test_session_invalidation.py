# -*- coding: utf-8 -*-
"""R09 回归测试：改密/重置密码后旧 JWT 全部失效（session_version + ver 声明）。

背景缺陷：改密后旧 access/refresh token 在有效期内依然可用。

机制：
- 登录/刷新签发的 JWT 带 ver=user.session_version；
- change_password / reset_password_via_security 将 session_version +1；
- 三个认证依赖（get_current_user_id / get_current_user_optional / require_role）
  校验 ver 与当前 session_version 一致，不一致 → 401 / 游客态。

打桩方式：假 DB Session + 真实 JWT 签发/解码，不依赖真实服务。
"""
import pytest
from fastapi import HTTPException

from src.services.auth_service import (
    change_password,
    create_access_token,
    get_current_user_id,
    get_current_user_optional,
    hash_password,
    require_role,
    reset_password_via_security,
)


class _FakeDB:
    def __init__(self, user):
        self.user = user
        self.committed = False

    def query(self, model):
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        return self.user

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


def _fake_user(session_version=0):
    from types import SimpleNamespace
    return SimpleNamespace(
        external_id="ext-1", username="tester", is_active=True,
        role="user", session_version=session_version,
        password_hash=hash_password("old_pass_123"),
        security_answer_hash=hash_password("我的第一个宠物"),
    )


def _token(ver=None):
    data = {"sub": "ext-1"}
    if ver is not None:
        data["ver"] = ver
    return create_access_token(data=data)


# ============================================================
# 1. ver 校验：匹配放行 / 不匹配失效 / 历史 token 兼容
# ============================================================

def test_current_user_id_accepts_matching_ver():
    user = _fake_user(session_version=2)
    db = _FakeDB(user)

    assert get_current_user_id(token=_token(ver=2), db=db) == "ext-1"


def test_current_user_id_rejects_stale_ver():
    """改密后（session_version 2→3），旧 token 的 ver=2 必须失效。"""
    user = _fake_user(session_version=3)
    db = _FakeDB(user)

    with pytest.raises(HTTPException) as exc:
        get_current_user_id(token=_token(ver=2), db=db)
    assert exc.value.status_code == 401


def test_current_user_id_legacy_token_without_ver():
    """历史 token 无 ver 视为 0：未改密用户（version=0）不受部署影响。"""
    user = _fake_user(session_version=0)
    db = _FakeDB(user)

    assert get_current_user_id(token=_token(), db=db) == "ext-1"


def test_current_user_id_legacy_token_dies_after_password_change():
    """历史 token 无 ver（=0）+ 用户已改密（version=1）→ 失效。"""
    user = _fake_user(session_version=1)
    db = _FakeDB(user)

    with pytest.raises(HTTPException) as exc:
        get_current_user_id(token=_token(), db=db)
    assert exc.value.status_code == 401


def test_optional_auth_returns_guest_on_stale_ver():
    user = _fake_user(session_version=5)
    db = _FakeDB(user)

    result = get_current_user_optional(
        authorization=f"Bearer {_token(ver=4)}", db=db)

    from src.services.auth_service import is_guest
    assert is_guest(result) is True


def test_require_role_rejects_stale_ver():
    user = _fake_user(session_version=1)
    db = _FakeDB(user)
    dep = require_role("user")

    with pytest.raises(HTTPException) as exc:
        dep(authorization=f"Bearer {_token(ver=0)}", db=db)
    assert exc.value.status_code == 401


# ============================================================
# 2. 改密/重置密码必须自增 session_version
# ============================================================

def test_change_password_bumps_session_version():
    user = _fake_user(session_version=0)
    db = _FakeDB(user)

    ok = change_password(db=db, user=user, old_password="old_pass_123",
                         new_password="new_pass_456")

    assert ok is True
    assert user.session_version == 1
    assert db.committed is True


def test_reset_password_via_security_bumps_session_version():
    user = _fake_user(session_version=4)
    db = _FakeDB(user)

    ok = reset_password_via_security(
        db=db, login_key="tester",
        security_answer="我的第一个宠物", new_password="reset_pass_789")

    assert ok is True
    assert user.session_version == 5
    assert db.committed is True


# ============================================================
# 3. 端到端：改密后旧 token 走完整依赖链被拒
# ============================================================

def test_full_flow_old_token_dead_after_password_change():
    """真实时序：签发 → 改密 → 旧 token 必须失效。"""
    user = _fake_user(session_version=0)
    old_token = _token(ver=0)

    # 登录后 token 可用
    db = _FakeDB(user)
    assert get_current_user_id(token=old_token, db=db) == "ext-1"

    # 改密
    assert change_password(db=db, user=user, old_password="old_pass_123",
                           new_password="new_pass_456") is True

    # 旧 token 失效
    with pytest.raises(HTTPException) as exc:
        get_current_user_id(token=old_token, db=db)
    assert exc.value.status_code == 401
