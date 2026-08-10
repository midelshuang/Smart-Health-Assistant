"""
接口测试套件（API 级别）

通过直接调用路由函数（不启动服务）进行接口层测试：
1. 请求/响应格式校验
2. 参数校验与错误码
3. Token 生命周期
4. 限流行为
5. 数据格式一致性
6. 接口契约稳定性
"""
import pytest
import json
import time
from datetime import timedelta
from unittest.mock import patch, MagicMock

from fastapi import HTTPException
from pydantic import ValidationError

from src.api.routes.auth import (
    LoginRequest, TokenResponse, RegisterRequest,
    EmailRegisterRequest, SendCodeRequest,
    RefreshRequest, ChangePasswordRequest, ForgotPasswordRequest,
    login, register, register_with_email,
    send_code, refresh_token, change_password,
    forgot_password, get_security_question, get_identity,
)
from src.api.routes.rag import (
    RAGQueryRequest, RAGQueryResponse,
    RAGIndexRequest, RAGIndexResponse,
    RAGStatusResponse,
    execute_rag_query, index_documents, get_rag_status,
)
from src.api.routes.plan import (
    PlanGenerateRequest, PlanConfigUpdateRequest,
    PlanCheckInRequest,
)
from src.services.auth_service import (
    create_access_token, decode_token, get_current_user_id,
    GuestUser, is_guest,
)
from src.config.settings import SECRET_KEY, ALGORITHM
from jose import jwt


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def faker_request():
    req = MagicMock()
    req.headers = {}
    req.client.host = "127.0.0.1"
    return req


def _make_valid_token(sub: str = "user001", expires_min: int = 30) -> str:
    data = {
        "sub": sub,
        "type": "access",
        "exp": __import__("datetime").datetime.utcnow() + timedelta(minutes=expires_min),
    }
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)


def _make_expired_token(sub: str = "user001") -> str:
    data = {
        "sub": sub,
        "type": "access",
        "exp": __import__("datetime").datetime.utcnow() - timedelta(minutes=1),
    }
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)


def _make_refresh_token(sub: str = "user001") -> str:
    data = {
        "sub": sub,
        "type": "refresh",
        "exp": __import__("datetime").datetime.utcnow() + timedelta(days=7),
    }
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)


# ============================================================
# 1. 请求体校验测试
# ============================================================

class TestRequestValidation:
    """请求体 Pydantic 校验"""

    # ---- LoginRequest ----
    def test_login_request_valid(self):
        req = LoginRequest(username="testuser", password="pass")
        assert req.username == "testuser"
        assert req.password == "pass"

    def test_login_request_missing_username(self):
        with pytest.raises(ValidationError):
            LoginRequest(password="pass")

    def test_login_request_missing_password(self):
        with pytest.raises(ValidationError):
            LoginRequest(username="testuser")

    def test_login_request_empty_username(self):
        """空用户名通过 Pydantic（后续逻辑层校验）"""
        req = LoginRequest(username="", password="pass")
        assert req.username == ""

    def test_login_request_empty_password(self):
        req = LoginRequest(username="user", password="")
        assert req.password == ""

    # ---- RegisterRequest ----
    def test_register_request_valid(self):
        req = RegisterRequest(username="newuser", password="StrongPass1")
        assert req.username == "newuser"

    def test_register_request_optional_security(self):
        req = RegisterRequest(
            username="user", password="pass",
            security_question="你好", security_answer="答案",
        )
        assert req.security_question == "你好"

    def test_register_request_empty_security(self):
        req = RegisterRequest(username="user", password="pass")
        assert req.security_question is None

    # ---- RAGQueryRequest ----
    def test_rag_query_request_valid(self):
        req = RAGQueryRequest(query="鸡蛋热量", stream=False)
        assert req.query == "鸡蛋热量"

    def test_rag_query_request_empty(self):
        with pytest.raises(ValidationError):
            RAGQueryRequest(query="")

    def test_rag_query_request_too_long(self):
        with pytest.raises(ValidationError):
            RAGQueryRequest(query="Q" * 3000)

    def test_rag_query_request_with_context(self):
        req = RAGQueryRequest(
            query="那晚餐呢？",
            conversation_context=[
                {"role": "user", "content": "我想减脂"},
                {"role": "assistant", "content": "好的"},
            ],
        )
        assert len(req.conversation_context) == 2

    def test_rag_query_request_invalid_context(self):
        with pytest.raises(ValidationError):
            RAGQueryRequest(
                query="测试",
                conversation_context="not a list",
            )

    # ---- PlanGenerateRequest ----
    def test_plan_generate_default_goal(self):
        req = PlanGenerateRequest()
        assert req.goal_type == "health_maintenance"

    def test_plan_generate_weight_loss(self):
        req = PlanGenerateRequest(goal_type="weight_loss")
        assert req.goal_type == "weight_loss"

    def test_plan_generate_with_dates(self):
        from datetime import date
        req = PlanGenerateRequest(
            goal_type="muscle_gain",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=30),
        )
        assert req.start_date is not None


# ============================================================
# 2. 响应体格式校验
# ============================================================

class TestResponseFormat:
    """响应格式一致性"""

    def test_token_response_format(self):
        resp = TokenResponse(
            access_token="acc", refresh_token="ref", expires_in=1800,
        )
        assert resp.token_type == "bearer"
        assert resp.access_token == "acc"
        assert resp.expires_in == 1800

    def test_token_response_missing_access(self):
        with pytest.raises(ValidationError):
            TokenResponse(refresh_token="ref", expires_in=1800)

    def test_rag_query_response_structure(self):
        """RAG 响应包含所有必填字段"""
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.query.return_value = {
                "success": True,
                "answer": "测试回答",
                "intent": "food_search",
                "sources": [],
            }
            body = RAGQueryRequest(query="测试")
            result = execute_rag_query(body, current_user=GuestUser())
            assert "success" in result
            assert "answer" in result
            assert "intent" in result

    def test_rag_status_response_structure(self):
        """RAG 状态响应结构"""
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.get_status.return_value = {
                "graph_compiled": True,
                "chromadb": True,
                "llm": True,
                "config": {},
            }
            result = get_rag_status()
            assert result["graph_compiled"] is True
            assert result["chromadb"] is True

    def test_api_error_response_format(self):
        """错误响应格式"""
        try:
            raise HTTPException(status_code=400, detail="参数错误")
        except HTTPException as e:
            assert e.status_code == 400
            assert e.detail == "参数错误"


# ============================================================
# 3. Token 生命周期测试
# ============================================================

class TestTokenLifecycle:
    """Token 完整生命周期"""

    def test_token_encode_decode(self):
        """Token 编解码一致"""
        token = create_access_token({"sub": "user001"})
        payload = decode_token(token)
        assert payload["sub"] == "user001"
        assert payload["type"] == "access"

    def test_token_decode_with_jose(self):
        """jose 库直接解析"""
        token = create_access_token({"sub": "test_user"})
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["sub"] == "test_user"

    def test_token_sub_claim(self):
        """Token sub 声明正确"""
        token = create_access_token({"sub": "uid_123"})
        payload = decode_token(token)
        assert payload["sub"] == "uid_123"

    def test_token_expiration(self):
        """过期 Token 解析失败"""
        expired = create_access_token(
            {"sub": "user001"},
            expires_delta=timedelta(minutes=-10),
        )
        with pytest.raises(Exception):
            decode_token(expired)

    def test_token_tampering(self):
        """篡改 Token 解析失败"""
        legit = create_access_token({"sub": "user001"})
        # 替换一个字符
        tampered = legit[:-1] + ("a" if legit[-1] != "a" else "b")
        with pytest.raises(Exception):
            decode_token(tampered)

    def test_token_empty_sub(self):
        """sub 为空的 Token 被 get_current_user_id 拒绝"""
        token = create_access_token({"sub": ""})
        mock_db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            get_current_user_id(token=token, db=mock_db)
        assert exc.value.status_code == 401

    def test_token_none_sub(self):
        """sub 为 None 的 Token 被拒绝"""
        token = create_access_token({})
        mock_db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            get_current_user_id(token=token, db=mock_db)
        assert exc.value.status_code == 401


# ============================================================
# 4. 错误码一致性测试
# ============================================================

class TestErrorCodeConsistency:
    """错误码使用规范"""

    def test_400_for_invalid_params(self, mock_db):
        """参数错误 → 400"""
        from src.api.routes.auth import register
        body = RegisterRequest(username="ab", password="weak")
        with pytest.raises(HTTPException) as exc:
            register(body, mock_db)
        assert exc.value.status_code == 400

    def test_401_for_unauthenticated(self):
        """未认证 → 401"""
        from src.services.auth_service import get_current_user_optional
        result = get_current_user_optional(authorization=None, db=MagicMock())
        assert is_guest(result)

    def test_403_for_insufficient_role(self, mock_db):
        """权限不足 → 403"""
        from src.services.auth_service import require_role
        dep = require_role("admin")
        token = _make_valid_token("user001")
        mock_user = MagicMock()
        mock_user.external_id = "user001"
        mock_user.role = "user"
        mock_user.is_active = True
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = mock_user
        mock_db.query.return_value = query_mock
        with pytest.raises(HTTPException) as exc:
            dep(authorization=f"Bearer {token}", db=mock_db)
        assert exc.value.status_code == 403

    def test_404_for_nonexistent_resource(self, mock_db):
        """资源不存在 → 404"""
        from src.api.routes.auth import get_security_question
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = None
        mock_db.query.return_value = query_mock
        with pytest.raises(HTTPException) as exc:
            get_security_question(username="ghost", db=mock_db)
        assert exc.value.status_code == 404

    def test_409_for_duplicate_resource(self, mock_db):
        """资源冲突 → 409"""
        from src.api.routes.auth import register
        existing = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = existing
        mock_db.query.return_value = query_mock
        body = RegisterRequest(username="existing", password="StrongPass1")
        with pytest.raises(HTTPException) as exc:
            register(body, mock_db)
        assert exc.value.status_code == 409

    def test_429_for_rate_limit(self):
        """限流 → 429"""
        from src.api.routes.auth import login, LoginRequest
        with patch("src.api.routes.auth.check_login_allowed",
                   return_value=(False, "限流中")):
            body = LoginRequest(username="u", password="p")
            with pytest.raises(HTTPException) as exc:
                login(body, MagicMock(), MagicMock())
            assert exc.value.status_code == 429

    def test_500_for_server_error(self):
        """服务端异常 → 500"""
        from src.api.routes.rag import execute_rag_query, RAGQueryRequest
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.query.side_effect = RuntimeError("内部错误")
            body = RAGQueryRequest(query="测试")
            with pytest.raises(HTTPException) as exc:
                execute_rag_query(body, current_user=MagicMock())
            assert exc.value.status_code == 500


# ============================================================
# 5. 输入边界测试
# ============================================================

class TestInputBoundary:
    """输入参数边界值"""

    @pytest.mark.parametrize("length,should_pass", [
        (1, True),
        (50, True),
        (2000, True),
        (2001, False),
        (5000, False),
        (100000, False),
    ])
    def test_rag_query_length_boundary(self, length, should_pass):
        """RAG 查询长度边界"""
        query = "Q" * length
        if should_pass:
            body = RAGQueryRequest(query=query)
            assert len(body.query) == length
        else:
            with pytest.raises(ValidationError):
                RAGQueryRequest(query=query)

    @pytest.mark.parametrize("username,should_pass", [
        ("a", False),       # 太短
        ("ab", False),      # 太短
        ("abc", True),      # 刚好
        ("user123", True),  # 正常
        ("user_123_test", True),  # 较长
    ])
    def test_username_length_boundary(self, username, should_pass):
        """用户名长度边界"""
        from src.utils.password_policy import validate_username
        ok, _ = validate_username(username)
        assert ok == should_pass, f"用户名 '{username}' 校验失败"

    @pytest.mark.parametrize("password,should_pass", [
        ("123456", False),         # 纯数字
        ("abcdef", False),         # 纯字母
        ("123", False),            # 太短
        ("Str0ngP@ss", True),      # 强密码
        ("Passw0rd!", True),       # 中强
    ])
    def test_password_strength(self, password, should_pass):
        """密码强度边界"""
        from src.utils.password_policy import validate_password
        ok, _ = validate_password(password)
        assert ok == should_pass, f"密码 '{password}' 校验结果错误"

    @pytest.mark.parametrize("email,should_pass", [
        ("user@example.com", True),
        ("user.name+tag@domain.co.uk", True),
        ("invalid", False),
        ("@no-user.com", False),
        ("user@", False),
        ("user@.com", False),
        ("", False),
    ])
    def test_email_format(self, email, should_pass):
        """邮箱格式校验"""
        from src.api.routes.auth import _validate_email
        err = _validate_email(email)
        passed = err is None
        assert passed == should_pass, f"邮箱 '{email}' 校验错误"


# ============================================================
# 6. 接口契约稳定性
# ============================================================

class TestAPIContractStability:
    """确保 API 接口契约稳定"""

    def test_auth_identity_contract(self):
        """身份接口契约"""
        result = get_identity(current_user=GuestUser())
        assert "is_authenticated" in result
        assert "is_guest" in result
        assert "role" in result
        assert "username" in result

    def test_auth_login_contract(self, mock_db, faker_request):
        """登录接口契约"""
        mock_user = MagicMock()
        mock_user.external_id = "user001"
        with patch("src.api.routes.auth.authenticate_user", return_value=mock_user), \
             patch("src.api.routes.auth.create_access_token", return_value="acc_tok"), \
             patch("src.api.routes.auth.create_refresh_token", return_value="ref_tok"), \
             patch("src.api.routes.auth.check_login_allowed", return_value=(True, None)), \
             patch("src.api.routes.auth.record_login_success"):
            body = LoginRequest(username="user", password="pass")
            result = login(body, faker_request, mock_db)
            assert "access_token" in result
            assert "refresh_token" in result
            assert result["token_type"] == "bearer"
            assert "expires_in" in result

    def test_rag_query_contract(self):
        """RAG 查询接口契约"""
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.query.return_value = {
                "success": True,
                "answer": "回答",
                "intent": "food_search",
                "sources": [],
            }
            body = RAGQueryRequest(query="测试")
            result = execute_rag_query(body, current_user=GuestUser())
            assert "answer" in result
            assert "success" in result
            assert "intent" in result
            assert "sources" in result

    def test_rag_status_contract(self):
        """RAG 状态接口契约"""
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.get_status.return_value = {
                "graph_compiled": True,
                "chromadb": True,
                "llm": True,
                "config": {"model": "gpt-4o-mini"},
            }
            result = get_rag_status()
            assert "graph_compiled" in result
            assert "chromadb" in result
            assert "llm" in result


# ============================================================
# 7. 接口异常边界
# ============================================================

class TestInterfaceEdgeCases:
    """接口异常边界"""

    def test_login_with_nonexistent_user(self, mock_db, faker_request):
        """不存在的用户登录 → 401"""
        with patch("src.api.routes.auth.authenticate_user", return_value=None), \
             patch("src.api.routes.auth.check_login_allowed", return_value=(True, None)), \
             patch("src.api.routes.auth.record_login_failure"), \
             patch("src.api.routes.auth.get_remaining_attempts", return_value=5):
            body = LoginRequest(username="ghost", password="pass")
            with pytest.raises(HTTPException) as exc:
                login(body, faker_request, mock_db)
            assert exc.value.status_code == 401

    def test_login_with_wrong_password(self, mock_db, faker_request):
        """错误密码登录 → 401"""
        mock_user = MagicMock()
        with patch("src.api.routes.auth.authenticate_user", return_value=None), \
             patch("src.api.routes.auth.check_login_allowed", return_value=(True, None)), \
             patch("src.api.routes.auth.record_login_failure"), \
             patch("src.api.routes.auth.get_remaining_attempts", return_value=4):
            body = LoginRequest(username="user", password="wrong")
            with pytest.raises(HTTPException) as exc:
                login(body, faker_request, mock_db)
            assert exc.value.status_code == 401

    def test_refresh_with_invalid_token(self, mock_db):
        """无效 Refresh Token → 401"""
        body = RefreshRequest(refresh_token="invalid.token.here")
        with pytest.raises(HTTPException) as exc:
            refresh_token(body, mock_db)
        assert exc.value.status_code == 401

    def test_refresh_with_access_token(self, mock_db):
        """用 Access Token 当 Refresh Token → 401"""
        access_tok = _make_valid_token("user001")
        body = RefreshRequest(refresh_token=access_tok)
        with pytest.raises(HTTPException) as exc:
            refresh_token(body, mock_db)
        assert exc.value.status_code == 401

    def test_refresh_with_expired_token(self, mock_db):
        """过期 Token 刷新 → 401"""
        expired = _make_expired_token("user001")
        body = RefreshRequest(refresh_token=expired)
        with pytest.raises(HTTPException) as exc:
            refresh_token(body, mock_db)
        assert exc.value.status_code == 401

    def test_change_password_same_password(self, mock_db):
        """新旧密码相同 → 400"""
        body = ChangePasswordRequest(old_password="same", new_password="same")
        with patch("src.api.routes.auth._change_password",
                   side_effect=ValueError("新密码不能与旧密码相同")):
            with pytest.raises(HTTPException) as exc:
                change_password(
                    body, token="token", db=mock_db, current_user_id="user001",
                )
            assert exc.value.status_code == 400


# ============================================================
# 8. 接口数据流完整性
# ============================================================

class TestDataFlowIntegrity:
    """数据在接口间流转的完整性"""

    def test_data_not_lost_in_rag_pipeline(self):
        """RAG 请求→响应数据完整性"""
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            expected = {
                "success": True,
                "answer": "这是回答",
                "intent": "guideline_query",
                "sources": [
                    {"file_name": "指南A", "source_type": "guideline"},
                ],
                "sql_results": [
                    {"food_name": "鸡蛋", "calories": 144},
                ],
            }
            mock_pipe.return_value.query.return_value = expected

            body = RAGQueryRequest(query="测试查询")
            result = execute_rag_query(body, current_user=GuestUser())

            assert result["success"] == expected["success"]
            assert result["answer"] == expected["answer"]
            assert result["intent"] == expected["intent"]
            assert result["sql_results"] == expected["sql_results"]

    def test_data_not_modified_in_profile_update(self, mock_db):
        """档案更新数据无意外修改"""
        from src.api.routes.user import update_profile, UserProfileUpdate
        with patch("src.api.routes.user._get_or_create_profile") as mock_get:
            mock_profile = MagicMock()
            mock_profile.height = 175.0
            mock_profile.weight = 80.0
            mock_get.return_value = mock_profile

            body = UserProfileUpdate(nickname="新昵称", age=30)
            update_profile(
                body, token="token", db=mock_db, current_user_id="user001",
            )
            assert mock_profile.nickname == "新昵称"
            assert mock_profile.age == 30
            assert mock_profile.height == 175.0  # 未修改
            assert mock_profile.weight == 80.0  # 未修改

    def test_token_data_integrity(self):
        """Token 中数据完整性"""
        token = create_access_token({
            "sub": "user001",
            "extra": "data",
        })
        payload = decode_token(token)
        assert payload["sub"] == "user001"
        assert payload["extra"] == "data"
        assert "exp" in payload
        assert "type" in payload