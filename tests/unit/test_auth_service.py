"""
Service/auth_service.py 单元测试

覆盖：
- verify_password 密码验证
- create_access_token / create_refresh_token Token 生成
- verify_refresh_token Refresh Token 校验
- authenticate_user 用户认证（Mock DB）
- get_current_user_id Token 解析（Mock DB + Token，含 Refresh 拒绝）
- change_password 修改密码
- reset_password_via_security 安全问题重置密码
"""
import pytest
from datetime import timedelta
from unittest.mock import MagicMock, patch
from jose import jwt

from src.services.auth_service import (
    verify_password,
    create_access_token,
    create_refresh_token,
    verify_refresh_token,
    authenticate_user,
    get_current_user_id,
    change_password,
    reset_password_via_security,
    hash_password,
    pwd_context,
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
)
from src.config.settings import SECRET_KEY, ALGORITHM


class TestVerifyPassword:
    """verify_password"""

    def test_correct_password_returns_true(self):
        """verify_password 应与 pwd_context.verify 行为一致"""
        with patch.object(pwd_context, "verify", return_value=True) as mock_verify:
            assert verify_password("mypassword", "fake_hashed") is True
            mock_verify.assert_called_once_with("mypassword", "fake_hashed")

    def test_wrong_password_returns_false(self):
        with patch.object(pwd_context, "verify", return_value=False):
            assert verify_password("wrongpassword", "fake_hashed") is False

    def test_empty_hashed_password_returns_false(self):
        """空哈希值应直接返回 False，不调用 pwd_context.verify"""
        with patch.object(pwd_context, "verify") as mock_verify:
            assert verify_password("any", "") is False
            assert verify_password("any", None) is False
            mock_verify.assert_not_called()


class TestCreateAccessToken:
    """create_access_token"""

    def test_returns_string_token(self):
        token = create_access_token({"sub": "user123"})
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_contains_sub_claim(self):
        token = create_access_token({"sub": "user123"})
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["sub"] == "user123"

    def test_token_has_expiration(self):
        token = create_access_token({"sub": "user123"}, expires_delta=timedelta(hours=1))
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert "exp" in payload

    def test_default_expiry_is_30_minutes(self):
        """不传 expires_delta 时默认 30 分钟"""
        import time
        before = int(time.time())
        token = create_access_token({"sub": "user123"})
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        after = int(time.time())
        # 默认 30 分钟 = 1800 秒
        assert abs(payload["exp"] - before - 1800) <= 2
        assert abs(payload["exp"] - after - 1800) <= 2


class TestAuthenticateUser:
    """authenticate_user（需要 DB）"""

    def test_returns_none_when_user_not_found(self):
        db = MagicMock()
        # query chain: db.query(User).filter(...).first() → None
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = None
        db.query.return_value = query_mock

        result = authenticate_user(db, "nonexistent", "any")
        assert result is None

    def test_returns_none_when_password_wrong(self):
        db = MagicMock()
        user_mock = MagicMock()
        user_mock.hashed_password = "fake_hashed"
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = user_mock
        db.query.return_value = query_mock

        # Mock verify_password 返回 False
        with patch("src.services.auth_service.verify_password", return_value=False):
            result = authenticate_user(db, "username", "wrong_password")
        assert result is None

    def test_returns_user_when_credentials_correct(self):
        db = MagicMock()
        user_mock = MagicMock()
        user_mock.hashed_password = "fake_hashed"
        user_mock.external_id = "user123"
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = user_mock
        db.query.return_value = query_mock

        # Mock verify_password 返回 True
        with patch("src.services.auth_service.verify_password", return_value=True):
            result = authenticate_user(db, "username", "correct_password")
        assert result is user_mock


class TestGetCurrentUserId:
    """get_current_user_id"""

    def test_returns_user_id_for_valid_token(self):
        # 生成一个有效 token
        token = create_access_token({"sub": "user_abc"})

        # Mock DB：返回存在的用户
        db = MagicMock()
        user_mock = MagicMock()
        user_mock.external_id = "user_abc"
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = user_mock
        db.query.return_value = query_mock

        result = get_current_user_id(token=token, db=db)
        assert result == "user_abc"

    def test_raises_401_for_invalid_token(self):
        from fastapi import HTTPException
        db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            get_current_user_id(token="invalid.token.here", db=db)
        assert exc.value.status_code == 401

    def test_raises_401_when_user_not_found(self):
        from fastapi import HTTPException
        token = create_access_token({"sub": "ghost_user"})

        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = None  # 用户不存在
        db.query.return_value = query_mock

        with pytest.raises(HTTPException) as exc:
            get_current_user_id(token=token, db=db)
        assert exc.value.status_code == 401

    def test_raises_401_for_token_without_sub(self):
        from fastapi import HTTPException
        # 用一个没有 sub 字段的 token
        token = create_access_token({"foo": "bar"})

        db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            get_current_user_id(token=token, db=db)
        assert exc.value.status_code == 401

    def test_rejects_refresh_token_for_business_api(self):
        """Refresh Token 不能用于业务接口鉴权"""
        from fastapi import HTTPException
        refresh = create_refresh_token({"sub": "user_abc"})
        db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            get_current_user_id(token=refresh, db=db)
        assert exc.value.status_code == 401


# ============================================================
# Refresh Token 机制
# ============================================================
class TestCreateRefreshToken:
    """create_refresh_token"""

    def test_returns_string_token(self):
        token = create_refresh_token({"sub": "user123"})
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_has_refresh_type(self):
        token = create_refresh_token({"sub": "user123"})
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["type"] == TOKEN_TYPE_REFRESH

    def test_default_expiry_is_7_days(self):
        import time
        before = int(time.time())
        token = create_refresh_token({"sub": "user123"})
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        # 7 天 = 604800 秒
        assert abs(payload["exp"] - before - 7 * 86400) <= 2

    def test_custom_expiry(self):
        token = create_refresh_token(
            {"sub": "user123"}, expires_delta=timedelta(days=1))
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        # 1 天 = 86400 秒
        assert "exp" in payload


class TestVerifyRefreshToken:
    """verify_refresh_token"""

    def test_valid_refresh_returns_sub(self):
        token = create_refresh_token({"sub": "user_abc"})
        sub = verify_refresh_token(token)
        assert sub == "user_abc"

    def test_invalid_token_raises_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            verify_refresh_token("invalid.token.here")
        assert exc.value.status_code == 401

    def test_access_token_rejected(self):
        """Access Token 不能当作 Refresh Token 使用"""
        from fastapi import HTTPException
        access = create_access_token({"sub": "user_abc"})
        with pytest.raises(HTTPException) as exc:
            verify_refresh_token(access)
        assert exc.value.status_code == 401

    def test_expired_refresh_raises_401(self):
        from fastapi import HTTPException
        # 立即过期
        token = create_refresh_token(
            {"sub": "user_abc"}, expires_delta=timedelta(seconds=-1))
        with pytest.raises(HTTPException) as exc:
            verify_refresh_token(token)
        assert exc.value.status_code == 401

    def test_token_without_sub_raises_401(self):
        from fastapi import HTTPException
        token = create_refresh_token({"foo": "bar"})
        with pytest.raises(HTTPException) as exc:
            verify_refresh_token(token)
        assert exc.value.status_code == 401


# ============================================================
# 修改密码
# ============================================================
class TestChangePassword:
    """change_password"""

    def test_success_returns_true(self):
        db = MagicMock()
        user = MagicMock()
        user.password_hash = "old_hash"
        user.external_id = "user_abc"

        with patch("src.services.auth_service.verify_password", return_value=True), \
             patch("src.services.auth_service.hash_password", return_value="new_hash"):
            result = change_password(db, user, "OldPwd123", "NewPwd456")
        assert result is True
        assert user.password_hash == "new_hash"
        db.commit.assert_called_once()

    def test_wrong_old_password_returns_false(self):
        db = MagicMock()
        user = MagicMock()
        user.password_hash = "old_hash"

        with patch("src.services.auth_service.verify_password", return_value=False):
            result = change_password(db, user, "WrongOld", "NewPwd456")
        assert result is False
        db.commit.assert_not_called()

    def test_same_as_old_raises_value_error(self):
        """新旧密码相同应抛出 ValueError"""
        db = MagicMock()
        user = MagicMock()
        user.password_hash = "old_hash"

        with patch("src.services.auth_service.verify_password", return_value=True), \
             pytest.raises(ValueError, match="相同"):
            change_password(db, user, "SamePwd123", "SamePwd123")
        db.commit.assert_not_called()


# ============================================================
# 重置密码（安全问题）
# ============================================================
class TestResetPasswordViaSecurity:
    """reset_password_via_security"""

    def test_success_returns_true(self):
        db = MagicMock()
        user = MagicMock()
        user.security_answer_hash = "answer_hash"
        user.external_id = "user_abc"

        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = user
        db.query.return_value = query_mock

        with patch("src.services.auth_service.verify_password", return_value=True), \
             patch("src.services.auth_service.hash_password", return_value="new_hash"):
            result = reset_password_via_security(db, "user_abc", "my_answer", "NewPwd123")
        assert result is True
        assert user.password_hash == "new_hash"
        db.commit.assert_called_once()

    def test_user_not_found_returns_false(self):
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = None
        db.query.return_value = query_mock

        result = reset_password_via_security(db, "ghost", "any", "NewPwd123")
        assert result is False

    def test_no_security_question_returns_false(self):
        """用户未设置安全问题时应失败"""
        db = MagicMock()
        user = MagicMock()
        user.security_answer_hash = None

        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = user
        db.query.return_value = query_mock

        result = reset_password_via_security(db, "user_abc", "any", "NewPwd123")
        assert result is False

    def test_wrong_answer_returns_false(self):
        db = MagicMock()
        user = MagicMock()
        user.security_answer_hash = "answer_hash"

        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = user
        db.query.return_value = query_mock

        with patch("src.services.auth_service.verify_password", return_value=False):
            result = reset_password_via_security(db, "user_abc", "wrong", "NewPwd123")
        assert result is False

    def test_supports_email_login_key(self):
        """登录键支持邮箱（也应能查到用户）"""
        db = MagicMock()
        user = MagicMock()
        user.security_answer_hash = "answer_hash"
        user.external_id = "email:abc@x.com"

        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = user
        db.query.return_value = query_mock

        with patch("src.services.auth_service.verify_password", return_value=True), \
             patch("src.services.auth_service.hash_password", return_value="new_hash"):
            result = reset_password_via_security(db, "abc@x.com", "ans", "NewPwd123")
        assert result is True
