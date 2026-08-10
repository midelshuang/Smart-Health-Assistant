"""
api/routes/auth.py 单元测试

覆盖：
- LoginRequest / TokenResponse Pydantic 模型
- login 接口（Mock authenticate_user + login_limiter）
- register 接口（Mock get_or_create_user）

注意：不使用 TestClient，避免依赖完整 ASGI 环境。
直接调用 router 函数，通过 patch 注入依赖。
"""
import pytest
from datetime import timedelta
from unittest.mock import patch, MagicMock

from fastapi import HTTPException

from src.api.routes.auth import (
    LoginRequest,
    TokenResponse,
    login,
    register,
)
from src.models.db_models import User


def _fake_request(ip: str = "127.0.0.1"):
    """构造一个最小可用的 Request mock"""
    req = MagicMock()
    req.headers = {}
    req.client.host = ip
    return req


class TestLoginRequest:
    def test_valid_login_request(self):
        req = LoginRequest(username="alice", password="secret123")
        assert req.username == "alice"
        assert req.password == "secret123"

    def test_missing_username_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LoginRequest(password="x")

    def test_missing_password_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LoginRequest(username="x")


class TestTokenResponse:
    def test_default_token_type_is_bearer(self):
        resp = TokenResponse(access_token="abc", refresh_token="r", expires_in=1800)
        assert resp.token_type == "bearer"

    def test_required_access_token(self):
        resp = TokenResponse(access_token="abc", refresh_token="r", expires_in=1800)
        assert resp.access_token == "abc"
        assert resp.expires_in == 1800

    def test_missing_access_token_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TokenResponse(refresh_token="r", expires_in=1800)


class TestLoginEndpoint:
    """login 接口"""

    def test_login_success_returns_token(self):
        db = MagicMock()
        mock_user = MagicMock()
        mock_user.external_id = "user123"

        with patch("src.api.routes.auth.authenticate_user", return_value=mock_user), \
             patch("src.api.routes.auth.create_access_token", return_value="fake_token"), \
             patch("src.api.routes.auth.create_refresh_token", return_value="fake_refresh"), \
             patch("src.api.routes.auth.check_login_allowed", return_value=(True, None)), \
             patch("src.api.routes.auth.record_login_success"), \
             patch("src.api.routes.auth.record_login_failure"):
            body = LoginRequest(username="alice", password="secret")
            result = login(body, _fake_request(), db)

        assert result["access_token"] == "fake_token"
        assert result["refresh_token"] == "fake_refresh"
        assert result["token_type"] == "bearer"
        assert result["expires_in"] > 0

    def test_login_with_wrong_credentials_raises_401(self):
        db = MagicMock()
        with patch("src.api.routes.auth.authenticate_user", return_value=None), \
             patch("src.api.routes.auth.check_login_allowed", return_value=(True, None)), \
             patch("src.api.routes.auth.record_login_failure"), \
             patch("src.api.routes.auth.get_remaining_attempts", return_value=4):
            body = LoginRequest(username="alice", password="wrong")
            with pytest.raises(HTTPException) as exc:
                login(body, _fake_request(), db)
        assert exc.value.status_code == 401

    def test_login_locked_returns_429(self):
        """账号被锁定时应返回 429"""
        db = MagicMock()
        with patch("src.api.routes.auth.check_login_allowed",
                   return_value=(False, "账号已被锁定，请 14 分 0 秒后重试")):
            body = LoginRequest(username="alice", password="secret")
            with pytest.raises(HTTPException) as exc:
                login(body, _fake_request(), db)
        assert exc.value.status_code == 429


class TestRegisterEndpoint:
    """register 接口"""

    def test_register_new_user_succeeds(self):
        db = MagicMock()
        # 用户不存在
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = None
        db.query.return_value = query_mock

        # get_or_create_user 返回新用户
        new_user = MagicMock()
        new_user.external_id = "alice"
        new_user.username = None
        with patch("src.api.routes.auth.get_or_create_user", return_value=new_user), \
             patch("src.api.routes.auth.hash_password", return_value="hashed"):
            from src.api.routes.auth import RegisterRequest
            body = RegisterRequest(username="alice", password="Strong1234")
            result = register(body, db)

        assert result["external_id"] == "alice"

    def test_register_existing_user_raises_409(self):
        db = MagicMock()
        existing_user = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = existing_user
        db.query.return_value = query_mock

        from src.api.routes.auth import RegisterRequest
        body = RegisterRequest(username="existing_user", password="Strong1234")
        with pytest.raises(HTTPException) as exc:
            register(body, db)
        assert exc.value.status_code == 409

    def test_register_weak_password_raises_400(self):
        """弱密码应被拒绝"""
        from src.api.routes.auth import RegisterRequest
        db = MagicMock()
        body = RegisterRequest(username="alice", password="123")
        with pytest.raises(HTTPException) as exc:
            register(body, db)
        assert exc.value.status_code == 400

    def test_register_invalid_username_raises_400(self):
        """非法用户名应被拒绝"""
        from src.api.routes.auth import RegisterRequest
        db = MagicMock()
        body = RegisterRequest(username="ab", password="Strong1234")  # 太短
        with pytest.raises(HTTPException) as exc:
            register(body, db)
        assert exc.value.status_code == 400
