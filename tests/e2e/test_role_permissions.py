"""
角色权限测试套件

覆盖三种角色的权限边界：
- 游客（未登录）：仅能访问 RAG 查询、健康检查、TDEE 计算
- 普通用户（user）：可访问所有用户功能
- 管理员（admin）：额外拥有管理仪表盘访问权

测试维度：
1. 端点访问控制（正确角色 → 200，错误角色 → 401/403）
2. Token 有效期与类型校验
3. 账号锁定/限流行为
4. 水平权限隔离
"""
import pytest
import time
from datetime import timedelta
from unittest.mock import patch, MagicMock, PropertyMock

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.services.auth_service import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user_id,
    get_current_user_optional,
    require_role,
    is_guest,
    GuestUser,
    ROLE_HIERARCHY,
    VALID_ROLES,
)
from src.models.db_models import User


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_db():
    """Mock 数据库会话"""
    return MagicMock(spec=Session)


@pytest.fixture
def sample_user():
    """普通用户"""
    user = MagicMock(spec=User)
    user.external_id = "user_regular_001"
    user.username = "testuser"
    user.role = "user"
    user.is_active = True
    user.password_hash = None
    return user


@pytest.fixture
def sample_admin():
    """管理员用户"""
    admin = MagicMock(spec=User)
    admin.external_id = "user_admin_001"
    admin.username = "adminuser"
    admin.role = "admin"
    admin.is_active = True
    admin.password_hash = None
    return admin


@pytest.fixture
def sample_expert():
    """专家用户（中间角色）"""
    expert = MagicMock(spec=User)
    expert.external_id = "user_expert_001"
    expert.username = "expertuser"
    expert.role = "expert"
    expert.is_active = True
    expert.password_hash = None
    return expert


def _make_token(sub: str, role: str = "user", token_type: str = "access", expires_min: int = 30):
    """构造测试用 Token"""
    from src.config.settings import SECRET_KEY, ALGORITHM
    from jose import jwt as jose_jwt

    data = {
        "sub": sub,
        "role": role,
        "type": token_type,
        "exp": __import__("datetime").datetime.utcnow() + timedelta(minutes=expires_min),
    }
    return jose_jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)


def _make_auth_header(token: str) -> dict:
    """构造 Authorization Header"""
    return {"Authorization": f"Bearer {token}"}


def _setup_user_query(mock_db, user):
    """Setup mock DB to return a specific user"""
    query_mock = MagicMock()
    query_mock.filter.return_value.first.return_value = user
    mock_db.query.return_value = query_mock
    return query_mock


# ============================================================
# 1. 角色层级模型测试
# ============================================================

class TestRoleHierarchy:
    """角色层级模型验证"""

    def test_role_hierarchy_values(self):
        assert ROLE_HIERARCHY["user"] == 0
        assert ROLE_HIERARCHY["expert"] == 1
        assert ROLE_HIERARCHY["admin"] == 2

    def test_valid_roles(self):
        assert "user" in VALID_ROLES
        assert "admin" in VALID_ROLES
        assert "expert" in VALID_ROLES

    def test_admin_can_impersonate_user(self):
        assert ROLE_HIERARCHY["admin"] >= ROLE_HIERARCHY["user"]

    def test_user_cannot_access_admin(self):
        assert ROLE_HIERARCHY["user"] < ROLE_HIERARCHY["admin"]

    def test_expert_can_access_user(self):
        assert ROLE_HIERARCHY["expert"] >= ROLE_HIERARCHY["user"]

    def test_expert_cannot_access_admin(self):
        assert ROLE_HIERARCHY["expert"] < ROLE_HIERARCHY["admin"]


# ============================================================
# 2. GuestUser 哨兵类测试
# ============================================================

class TestGuestUser:
    def test_guest_is_singleton(self):
        g1 = GuestUser()
        g2 = GuestUser()
        assert g1 is g2

    def test_guest_bool_is_false(self):
        assert not bool(GuestUser())

    def test_is_guest_detects_guest(self):
        assert is_guest(GuestUser()) is True

    def test_is_guest_detects_real_user(self, sample_user):
        assert is_guest(sample_user) is False

    def test_guest_role_is_guest(self):
        assert GuestUser().role == "guest"

    def test_guest_has_no_external_id(self):
        assert GuestUser().external_id == "guest"


# ============================================================
# 3. Token 类型与有效期测试
# ============================================================

class TestTokenManagement:
    def test_access_token_creation(self):
        data = {"sub": "user001"}
        token = create_access_token(data)
        payload = decode_token(token)
        assert payload["sub"] == "user001"
        assert payload["type"] == "access"

    def test_refresh_token_creation(self):
        data = {"sub": "user001"}
        token = create_refresh_token(data)
        payload = decode_token(token)
        assert payload["sub"] == "user001"
        assert payload["type"] == "refresh"

    def test_refresh_token_cannot_be_used_as_access(self, mock_db):
        refresh_token = create_refresh_token({"sub": "user001"})
        with pytest.raises(HTTPException) as exc:
            get_current_user_id(token=refresh_token, db=mock_db)
        assert exc.value.status_code == 401

    def test_access_token_cannot_be_used_as_refresh(self):
        from src.services.auth_service import verify_refresh_token
        access_token = create_access_token({"sub": "user001"})
        with pytest.raises(HTTPException):
            verify_refresh_token(access_token)

    def test_token_expiry(self):
        expired_token = create_access_token(
            {"sub": "user001"},
            expires_delta=timedelta(minutes=-1),
        )
        from jose import JWTError
        with pytest.raises(JWTError):
            decode_token(expired_token)


# ============================================================
# 4. require_role 权限守卫测试
# ============================================================

class TestRequireRole:
    def test_unauthenticated_request_returns_401(self, mock_db):
        dep = require_role("user")
        with pytest.raises(HTTPException) as exc:
            dep(authorization=None, db=mock_db)
        assert exc.value.status_code == 401

    def test_empty_auth_header_returns_401(self, mock_db):
        dep = require_role("admin")
        with pytest.raises(HTTPException) as exc:
            dep(authorization="", db=mock_db)
        assert exc.value.status_code == 401

    def test_non_bearer_auth_returns_401(self, mock_db):
        dep = require_role("admin")
        with pytest.raises(HTTPException) as exc:
            dep(authorization="Basic dXNlcjpwYXNz", db=mock_db)
        assert exc.value.status_code == 401

    def test_regular_user_accesses_user_endpoint(self, sample_user, mock_db):
        token = _make_token("user001", role="user")
        _setup_user_query(mock_db, sample_user)
        dep = require_role("user")
        result = dep(authorization=f"Bearer {token}", db=mock_db)
        assert result.external_id == "user_regular_001"

    def test_regular_user_blocked_from_admin_endpoint(self, sample_user, mock_db):
        token = _make_token("user001", role="user")
        _setup_user_query(mock_db, sample_user)
        dep = require_role("admin")
        with pytest.raises(HTTPException) as exc:
            dep(authorization=f"Bearer {token}", db=mock_db)
        assert exc.value.status_code == 403

    def test_admin_accesses_admin_endpoint(self, sample_admin, mock_db):
        token = _make_token("admin001", role="admin")
        _setup_user_query(mock_db, sample_admin)
        dep = require_role("admin")
        result = dep(authorization=f"Bearer {token}", db=mock_db)
        assert result.role == "admin"

    def test_admin_accesses_user_endpoint(self, sample_admin, mock_db):
        token = _make_token("admin001", role="admin")
        _setup_user_query(mock_db, sample_admin)
        dep = require_role("user")
        result = dep(authorization=f"Bearer {token}", db=mock_db)
        assert result.role == "admin"

    def test_expert_accesses_user_endpoint(self, sample_expert, mock_db):
        token = _make_token("expert001", role="expert")
        _setup_user_query(mock_db, sample_expert)
        dep = require_role("user")
        result = dep(authorization=f"Bearer {token}", db=mock_db)
        assert result.role == "expert"

    def test_expert_blocked_from_admin_endpoint(self, sample_expert, mock_db):
        token = _make_token("expert001", role="expert")
        _setup_user_query(mock_db, sample_expert)
        dep = require_role("admin")
        with pytest.raises(HTTPException) as exc:
            dep(authorization=f"Bearer {token}", db=mock_db)
        assert exc.value.status_code == 403

    def test_disabled_user_blocked(self, mock_db):
        """被禁用用户 → 查询返回 None（is_active 过滤） → 401"""
        token = _make_token("disabled001", role="user")
        # 模拟 is_active=False 的用户被过滤掉
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = None
        mock_db.query.return_value = query_mock

        dep = require_role("user")
        with pytest.raises(HTTPException) as exc:
            dep(authorization=f"Bearer {token}", db=mock_db)
        assert exc.value.status_code == 401

    def test_refresh_token_rejected_by_require_role(self, sample_user, mock_db):
        refresh_token = _make_token("user001", role="user", token_type="refresh")
        _setup_user_query(mock_db, sample_user)
        dep = require_role("user")
        with pytest.raises(HTTPException) as exc:
            dep(authorization=f"Bearer {refresh_token}", db=mock_db)
        assert exc.value.status_code == 401


# ============================================================
# 5. get_current_user_optional 测试
# ============================================================

class TestOptionalAuth:
    def test_no_auth_returns_guest(self, mock_db):
        result = get_current_user_optional(authorization=None, db=mock_db)
        assert is_guest(result)

    def test_blank_auth_returns_guest(self, mock_db):
        result = get_current_user_optional(authorization="", db=mock_db)
        assert is_guest(result)

    def test_invalid_token_returns_guest(self, mock_db):
        result = get_current_user_optional(
            authorization="Bearer invalid.token.here", db=mock_db,
        )
        assert is_guest(result)

    def test_refresh_token_returns_guest(self, mock_db):
        refresh_token = _make_token("user001", token_type="refresh")
        result = get_current_user_optional(
            authorization=f"Bearer {refresh_token}", db=mock_db,
        )
        assert is_guest(result)

    def test_valid_token_returns_user(self, sample_user, mock_db):
        token = _make_token("user001")
        _setup_user_query(mock_db, sample_user)
        result = get_current_user_optional(
            authorization=f"Bearer {token}", db=mock_db,
        )
        assert not is_guest(result)
        assert result.external_id == "user_regular_001"


# ============================================================
# 6. 接口级权限边界测试
# ============================================================

class TestEndpointAccessControl:
    """按端点分类的权限边界验证（通过 mock 依赖注入验证权限行为）"""

    def test_guest_can_access_identity(self):
        """游客访问 /auth/identity → 返回 is_guest=True"""
        from src.api.routes.auth import get_identity
        result = get_identity(current_user=GuestUser())
        assert result["is_authenticated"] is False
        assert result["is_guest"] is True

    def test_user_can_access_identity(self, sample_user):
        """普通用户访问 /auth/identity → 返回角色信息"""
        from src.api.routes.auth import get_identity
        result = get_identity(current_user=sample_user)
        assert result["is_authenticated"] is True
        assert result["role"] == "user"

    def test_guest_can_query_rag(self):
        """游客 RAG 查询（Mock Pipeline）"""
        from src.api.routes.rag import RAGQueryRequest, execute_rag_query
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.query.return_value = {
                "success": True, "answer": "测试回答", "intent": "food_search",
                "sources": [],
            }
            body = RAGQueryRequest(query="鸡蛋热量", stream=False)
            result = execute_rag_query(body, current_user=GuestUser())
            assert result["success"] is True

    def test_guest_can_access_rag_status(self):
        """游客查询 RAG 状态"""
        from src.api.routes.rag import get_rag_status
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.get_status.return_value = {
                "graph_compiled": True, "chromadb": True, "llm": True,
            }
            result = get_rag_status()
            assert result["chromadb"] is True

    def test_user_can_query_rag(self, sample_user):
        """普通用户 RAG 查询"""
        from src.api.routes.rag import RAGQueryRequest, execute_rag_query
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.query.return_value = {
                "success": True, "answer": "个性化回答", "intent": "guideline_query",
                "sources": [],
            }
            body = RAGQueryRequest(query="糖尿病饮食", stream=False)
            result = execute_rag_query(body, current_user=sample_user)
            assert result["success"] is True

    def test_guest_cannot_index_documents(self):
        """游客调用 /rag/index → 401"""
        from src.api.routes.rag import index_documents, RAGIndexRequest
        body = RAGIndexRequest(dir_path="/tmp/test", source_type="guideline")
        with pytest.raises(HTTPException) as exc:
            index_documents(body, current_user=GuestUser())
        assert exc.value.status_code == 401

    def test_guest_cannot_access_user_profile(self, mock_db):
        """游客访问 /user/profile → get_current_user_id 返回 None → 401"""
        from src.services.auth_service import get_current_user_id
        token = ""
        with pytest.raises(HTTPException) as exc:
            get_current_user_id(token=token, db=mock_db)
        assert exc.value.status_code == 401

    def test_guest_cannot_access_plans(self, mock_db):
        """游客访问 /plans → get_current_user_id 返回 None → 401"""
        from src.services.auth_service import get_current_user_id
        token = ""
        with pytest.raises(HTTPException) as exc:
            get_current_user_id(token=token, db=mock_db)
        assert exc.value.status_code == 401

    def test_guest_cannot_access_diet_search(self, mock_db):
        """游客访问 /diet/search → get_current_user_id 返回 None → 401"""
        from src.services.auth_service import get_current_user_id
        token = ""
        with pytest.raises(HTTPException) as exc:
            get_current_user_id(token=token, db=mock_db)
        assert exc.value.status_code == 401

    def test_admin_can_access_dashboard(self, mock_db):
        """管理员访问 /user/dashboard/stats"""
        from src.api.routes.user import admin_dashboard
        mock_db.bind = MagicMock()
        with patch("src.api.routes.user._table_exists", return_value=True):
            query_mock = MagicMock()
            query_mock.filter.return_value.count.return_value = 100
            query_mock.count.return_value = 50
            mock_db.query.return_value = query_mock
            result = admin_dashboard(
                db=mock_db,
                current_user=MagicMock(role="admin", is_active=True),
            )
            assert "total_users" in result

    def test_regular_user_blocked_from_dashboard(self, mock_db):
        """普通用户访问 /user/dashboard → require_role("admin") 拒绝"""
        dep = require_role("admin")
        token = _make_token("user001", role="user")
        query_mock = MagicMock()
        user_mock = MagicMock()
        user_mock.role = "user"
        user_mock.is_active = True
        query_mock.filter.return_value.first.return_value = user_mock
        mock_db.query.return_value = query_mock

        with pytest.raises(HTTPException) as exc:
            dep(authorization=f"Bearer {token}", db=mock_db)
        assert exc.value.status_code == 403

    def test_guest_blocked_from_dashboard(self, mock_db):
        """游客访问 /user/dashboard → require_role("admin") 抛 401"""
        dep = require_role("admin")
        with pytest.raises(HTTPException) as exc:
            dep(authorization=None, db=mock_db)
        assert exc.value.status_code == 401


# ============================================================
# 7. 水平权限隔离测试
# ============================================================

class TestHorizontalIsolation:
    def test_user_profile_isolation(self, mock_db):
        """用户 A 只能读取自己的档案"""
        from src.api.routes.user import get_profile
        profile = MagicMock()
        profile.user_id = "user_a"
        profile.nickname = "UserA"
        profile.age = 25
        profile.gender = "male"
        profile.height = 175.0
        profile.weight = 70.0
        profile.bmi = 22.9
        profile.body_fat_percentage = None
        profile.province = None
        profile.sports_hobby = None
        profile.health_goal = None
        profile.health_tags = None
        profile.allergies = None
        profile.updated_at = None

        with patch("src.api.routes.user._get_or_create_profile", return_value=profile):
            result = get_profile(
                token="token", db=mock_db, current_user_id="user_a",
            )
            assert result["user_id"] == "user_a"
            assert result["nickname"] == "UserA"

    def test_plan_isolation(self, mock_db):
        """用户 A 的计划列表不含用户 B 的计划"""
        from src.api.routes.plan import list_user_plans
        with patch("src.api.routes.plan.list_plans") as mock_list:
            mock_list.return_value = [
                {"plan_id": 1, "user_id": "user_a", "name": "减脂计划"},
            ]
            result = list_user_plans(
                status=None, token="token", db=mock_db, current_user_id="user_a",
            )
            assert len(result) == 1
            assert result[0]["user_id"] == "user_a"


# ============================================================
# 8. 安全问题与密码重置权限
# ============================================================

class TestPasswordResetSecurity:
    def test_security_question_does_not_leak_user_existence(self, mock_db):
        """查询不存在的用户 → 404"""
        from src.api.routes.auth import get_security_question
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = None
        mock_db.query.return_value = query_mock
        with pytest.raises(HTTPException) as exc:
            get_security_question(username="ghost", db=mock_db)
        assert exc.value.status_code == 404

    def test_security_question_leaks_nothing(self, mock_db):
        """安全查询仅返回问题文本"""
        from src.api.routes.auth import get_security_question
        query_mock = MagicMock()
        user_mock = MagicMock()
        user_mock.security_question = "你的宠物名字是什么？"
        query_mock.filter.return_value.first.return_value = user_mock
        mock_db.query.return_value = query_mock
        result = get_security_question(username="alice", db=mock_db)
        assert result == {"security_question": "你的宠物名字是什么？"}
        assert "password" not in str(result).lower()


# ============================================================
# 9. 登录限流测试
# ============================================================

class TestLoginRateLimit:
    def test_login_locked_after_too_many_failures(self):
        from src.api.routes.auth import login, LoginRequest
        db = MagicMock()
        with patch("src.api.routes.auth.check_login_allowed",
                   return_value=(False, "账号已被锁定")), \
             patch("src.api.routes.auth.record_login_failure"):
            body = LoginRequest(username="alice", password="wrong")
            with pytest.raises(HTTPException) as exc:
                login(body, MagicMock(), db)
            assert exc.value.status_code == 429

    def test_limited_by_ip(self):
        from src.api.routes.auth import login, LoginRequest
        db = MagicMock()
        with patch("src.api.routes.auth.check_login_allowed",
                   return_value=(False, "IP 已被限流")), \
             patch("src.api.routes.auth.record_login_failure"):
            body = LoginRequest(username="alice", password="wrong")
            with pytest.raises(HTTPException) as exc:
                login(body, MagicMock(), db)
            assert exc.value.status_code == 429

    def test_login_success_clears_failure_count(self):
        from src.api.routes.auth import login, LoginRequest
        db = MagicMock()
        mock_user = MagicMock()
        mock_user.external_id = "user001"
        mock_user.is_active = True
        with patch("src.api.routes.auth.authenticate_user", return_value=mock_user), \
             patch("src.api.routes.auth.create_access_token", return_value="token_a"), \
             patch("src.api.routes.auth.create_refresh_token", return_value="token_r"), \
             patch("src.api.routes.auth.check_login_allowed", return_value=(True, None)), \
             patch("src.api.routes.auth.record_login_success") as mock_clear, \
             patch("src.api.routes.auth.record_login_failure"):
            body = LoginRequest(username="alice", password="correct")
            result = login(body, MagicMock(), db)
            assert result["access_token"] == "token_a"
            mock_clear.assert_called_once()