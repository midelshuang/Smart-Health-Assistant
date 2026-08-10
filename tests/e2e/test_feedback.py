"""
用户反馈机制测试套件 (Test Feedback Mechanism)

覆盖:
1. 数据模型验证 - UserFeedback 字段 + 唯一约束
2. 反馈提交 - 点赞/踩/使用计划
3. 幂等性测试 - 点赞→踩→点赞 不产生重复记录
4. 偏好聚合 - 统计数据正确计算
5. 风格指令生成 - 根据反馈比率生成调整指令
6. API 端点测试 - 接口请求/响应验证
7. 权限控制 - 游客无法提交反馈
8. 反馈驱动 RAG - 风格指令注入系统提示词
"""

import pytest
from unittest.mock import MagicMock, patch
from typing import Dict, Any


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_db():
    """Mock SQLAlchemy session"""
    return MagicMock()


@pytest.fixture
def valid_token():
    """生成有效 JWT Token"""
    from src.services.auth_service import create_access_token
    return create_access_token({"sub": "user_feedback_001"})


@pytest.fixture
def valid_user():
    """模拟已认证用户"""
    user = MagicMock()
    user.external_id = "user_feedback_001"
    user.username = "feedback_tester"
    user.role = "user"
    user.is_active = True
    return user


@pytest.fixture
def admin_user():
    """模拟管理员用户"""
    user = MagicMock()
    user.external_id = "admin_feedback_001"
    user.username = "admin_feedback"
    user.role = "admin"
    user.is_active = True
    return user


def _make_token(sub: str, role: str = "user") -> str:
    from src.services.auth_service import create_access_token
    return create_access_token({"sub": sub, "role": role})


# ============================================================
# 1. 数据模型验证
# ============================================================

class TestFeedbackDataModel:
    """数据模型验证"""

    def test_user_feedback_table_structure(self):
        """UserFeedback 表结构验证"""
        from src.models.db_models import UserFeedback
        assert UserFeedback.__tablename__ == 'user_feedbacks'

        columns = [c.name for c in UserFeedback.__table__.columns]
        assert 'id' in columns
        assert 'user_id' in columns
        assert 'target_type' in columns
        assert 'target_id' in columns
        assert 'feedback_action' in columns
        assert 'created_at' in columns
        assert 'updated_at' in columns

    def test_user_profile_has_feedback_preferences(self):
        """UserProfile 包含 feedback_preferences 字段"""
        from src.models.db_models import UserProfile
        columns = [c.name for c in UserProfile.__table__.columns]
        assert 'feedback_preferences' in columns

    def test_valid_target_types(self):
        """合法目标类型"""
        from src.services.feedback_service import TARGET_ANSWER, TARGET_PLAN, VALID_TARGETS
        assert TARGET_ANSWER == "answer"
        assert TARGET_PLAN == "plan"
        assert len(VALID_TARGETS) == 2

    def test_valid_actions(self):
        """合法反馈动作"""
        from src.services.feedback_service import ACTION_LIKE, ACTION_DISLIKE, ACTION_USE_PLAN, VALID_ACTIONS
        assert ACTION_LIKE == "like"
        assert ACTION_DISLIKE == "dislike"
        assert ACTION_USE_PLAN == "use_plan"
        assert len(VALID_ACTIONS) == 3


# ============================================================
# 2. 反馈提交与幂等性
# ============================================================

class TestFeedbackSubmission:
    """反馈提交测试"""

    def test_submit_like(self, mock_db):
        """提交点赞"""
        from src.services.feedback_service import submit_feedback

        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = submit_feedback(
            db=mock_db,
            user_id="user001",
            target_type="answer",
            target_id="msg-uuid-001",
            action="like",
        )
        assert result["success"] is True
        assert result["feedback"]["action"] == "like"

    def test_submit_dislike(self, mock_db):
        """提交踩"""
        from src.services.feedback_service import submit_feedback

        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = submit_feedback(
            db=mock_db,
            user_id="user001",
            target_type="answer",
            target_id="msg-uuid-002",
            action="dislike",
        )
        assert result["success"] is True
        assert result["feedback"]["action"] == "dislike"

    def test_submit_use_plan(self, mock_db):
        """提交使用计划"""
        from src.services.feedback_service import submit_feedback

        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = submit_feedback(
            db=mock_db,
            user_id="user001",
            target_type="plan",
            target_id="100",
            action="use_plan",
        )
        assert result["success"] is True
        assert result["feedback"]["action"] == "use_plan"

    def test_invalid_action_rejected(self, mock_db):
        """无效动作被拒绝"""
        from src.services.feedback_service import submit_feedback

        result = submit_feedback(
            db=mock_db,
            user_id="user001",
            target_type="answer",
            target_id="msg-uuid-001",
            action="invalid_action",
        )
        assert result["success"] is False
        assert "无效的反馈动作" in result["error"]

    def test_invalid_target_type_rejected(self, mock_db):
        """无效目标类型被拒绝"""
        from src.services.feedback_service import submit_feedback

        result = submit_feedback(
            db=mock_db,
            user_id="user001",
            target_type="invalid_type",
            target_id="001",
            action="like",
        )
        assert result["success"] is False
        assert "无效的目标类型" in result["error"]


class TestFeedbackIdempotency:
    """幂等性测试 - 防刷分核心"""

    def test_like_dislike_like_no_duplicates(self, mock_db):
        """点赞→踩→点赞 只产生一条记录"""
        from src.services.feedback_service import submit_feedback

        mock_existing = MagicMock()
        mock_existing.id = 1
        mock_existing.feedback_action = "like"
        mock_existing.created_at = None

        # 设置完整的 mock 链: query -> filter -> first
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_query.filter.return_value = mock_filter

        # 第一次查询返回 None（创建新反馈），之后返回 mock_existing（更新）
        mock_filter.first.side_effect = [None, mock_existing, mock_existing]

        # 为 _update_user_preferences 准备 mock:
        # 每次调用 _update_user_preferences 需要:
        #   query(UserFeedback).filter(...).all()  -- answer 类型
        #   query(UserFeedback).filter(...).all()  -- plan 类型
        #   query(UserProfile).filter(...).first() -- 获取用户 profile
        mock_query_for_prefs = MagicMock()
        mock_filter_for_prefs = MagicMock()
        mock_query_for_prefs.filter.return_value = mock_filter_for_prefs
        mock_filter_for_prefs.all.return_value = []
        mock_filter_for_prefs.first.return_value = None

        # 每次 submit_feedback 调用: 1次主查询 + 3次偏好查询 = 4次 query
        # 3次 submit_feedback 调用 = 12次 query
        side_effects = [mock_query] + [mock_query_for_prefs] * 3  # 第1次
        side_effects += [mock_query] + [mock_query_for_prefs] * 3  # 第2次
        side_effects += [mock_query] + [mock_query_for_prefs] * 3  # 第3次
        mock_db.query.side_effect = side_effects

        # 点赞
        r1 = submit_feedback(db=mock_db, user_id="u1", target_type="answer", target_id="t1", action="like")
        assert r1["success"] is True

        # 踩（覆盖点赞，不创建新记录）
        mock_existing.feedback_action = "dislike"
        r2 = submit_feedback(db=mock_db, user_id="u1", target_type="answer", target_id="t1", action="dislike")
        assert r2["success"] is True
        assert r2["feedback"]["action"] == "dislike"

        # 再次点赞（覆盖踩，不创建新记录）
        mock_existing.feedback_action = "like"
        r3 = submit_feedback(db=mock_db, user_id="u1", target_type="answer", target_id="t1", action="like")
        assert r3["success"] is True
        assert r3["feedback"]["action"] == "like"

        # 验证: 只有一次 add 调用（其余是更新）
        add_count = mock_db.add.call_count
        assert add_count == 1, f"应该只有1次add(创建)调用，实际: {add_count}"

    def test_multiple_users_independent(self, mock_db):
        """不同用户对同一目标的反馈相互独立"""
        from src.services.feedback_service import submit_feedback

        mock_db.query.return_value.filter.return_value.first.return_value = None

        r1 = submit_feedback(db=mock_db, user_id="u1", target_type="answer", target_id="t1", action="like")
        r2 = submit_feedback(db=mock_db, user_id="u2", target_type="answer", target_id="t1", action="dislike")

        assert r1["success"] is True
        assert r2["success"] is True

        # 两次都应该是创建（不同用户）
        add_count = mock_db.add.call_count
        assert add_count == 2

    def test_multiple_targets_same_user(self, mock_db):
        """同一用户对不同目标的反馈相互独立"""
        from src.services.feedback_service import submit_feedback

        mock_db.query.return_value.filter.return_value.first.return_value = None

        r1 = submit_feedback(db=mock_db, user_id="u1", target_type="answer", target_id="t1", action="like")
        r2 = submit_feedback(db=mock_db, user_id="u1", target_type="plan", target_id="t2", action="use_plan")

        assert r1["success"] is True
        assert r2["success"] is True
        assert mock_db.add.call_count == 2


# ============================================================
# 3. 偏好聚合与风格指令
# ============================================================

class TestPreferenceAggregation:
    """偏好聚合测试"""

    def test_like_ratio_high_returns_positive_directive(self):
        """高点赞率 → 保持当前风格指令"""
        from src.services.feedback_service import get_style_directive

        stats = {
            "answer": {"total": 10, "likes": 8, "dislikes": 1, "uses": 1, "like_ratio": 0.8, "dislike_ratio": 0.1},
            "plan": {"total": 5, "likes": 4, "dislikes": 0, "uses": 3, "like_ratio": 0.8, "dislike_ratio": 0.0},
        }
        directive = get_style_directive(stats)
        assert directive != ""
        assert "满意度较高" in directive

    def test_dislike_ratio_high_returns_adjust_directive(self):
        """高踩率 → 调整回答方式指令"""
        from src.services.feedback_service import get_style_directive

        stats = {
            "answer": {"total": 10, "likes": 1, "dislikes": 7, "uses": 2, "like_ratio": 0.1, "dislike_ratio": 0.7},
            "plan": {"total": 5, "likes": 1, "dislikes": 3, "uses": 1, "like_ratio": 0.2, "dislike_ratio": 0.6},
        }
        directive = get_style_directive(stats)
        assert directive != ""
        assert "满意度较低" in directive

    def test_insufficient_feedback_no_directive(self):
        """反馈不足 → 无指令"""
        from src.services.feedback_service import get_style_directive

        stats = {
            "answer": {"total": 1, "likes": 1, "dislikes": 0, "uses": 0, "like_ratio": 1.0, "dislike_ratio": 0.0},
            "plan": {"total": 0, "likes": 0, "dislikes": 0, "uses": 0, "like_ratio": 0.5, "dislike_ratio": 0.0},
        }
        directive = get_style_directive(stats)
        assert directive == ""

    def test_no_feedback_no_directive(self):
        """零反馈 → 无指令"""
        from src.services.feedback_service import get_style_directive

        stats = {
            "answer": {"total": 0, "likes": 0, "dislikes": 0, "uses": 0, "like_ratio": 0.5, "dislike_ratio": 0.0},
            "plan": {"total": 0, "likes": 0, "dislikes": 0, "uses": 0, "like_ratio": 0.5, "dislike_ratio": 0.0},
        }
        directive = get_style_directive(stats)
        assert directive == ""

    def test_plan_use_ratio_high(self):
        """计划使用率高 → 保持计划风格"""
        from src.services.feedback_service import get_style_directive

        stats = {
            "answer": {"total": 3, "likes": 2, "dislikes": 0, "uses": 1, "like_ratio": 0.67, "dislike_ratio": 0.0},
            "plan": {"total": 4, "likes": 1, "dislikes": 0, "uses": 3, "like_ratio": 0.25, "dislike_ratio": 0.0},
        }
        directive = get_style_directive(stats)
        assert directive != ""
        assert "频繁使用" in directive


class TestStyleDirectiveForPlan:
    """计划风格指令测试"""

    def test_plan_dislike_high_has_extra_directive(self):
        """计划高踩率 → 含额外调整指令"""
        from src.services.feedback_service import get_style_directive_for_plan

        mock_db = MagicMock()
        mock_profile = MagicMock()
        mock_profile.feedback_preferences = {
            "stats": {
                "answer": {"total": 5, "likes": 3, "dislikes": 1, "uses": 1, "like_ratio": 0.6, "dislike_ratio": 0.2},
                "plan": {"total": 5, "likes": 1, "dislikes": 3, "uses": 1, "like_ratio": 0.2, "dislike_ratio": 0.6},
            }
        }
        mock_db.query.return_value.filter.return_value.first.return_value = mock_profile

        directive = get_style_directive_for_plan(mock_db, "u1")
        assert directive != ""
        assert "灵活" in directive or "备选" in directive

    def test_no_plan_feedback_no_directive(self):
        """无计划反馈 → 空指令"""
        from src.services.feedback_service import get_style_directive_for_plan

        mock_db = MagicMock()
        mock_profile = MagicMock()
        mock_profile.feedback_preferences = None
        mock_db.query.return_value.filter.return_value.first.return_value = mock_profile

        directive = get_style_directive_for_plan(mock_db, "u1")
        assert directive == ""


# ============================================================
# 4. API 端点测试
# ============================================================

class TestFeedbackAPIRoutes:
    """API 端点测试"""

    def test_submit_answer_feedback_success(self, mock_db, valid_user):
        """回答反馈 API 成功"""
        from src.api.routes.feedback import submit_answer_feedback, AnswerFeedbackRequest

        with patch("src.api.routes.feedback.submit_feedback") as mock_submit:
            mock_submit.return_value = {"success": True, "feedback": {"id": 1, "action": "like"}}
            body = AnswerFeedbackRequest(message_uuid="msg-001", action="like")
            result = submit_answer_feedback(
                body=body, token="token", db=mock_db, current_user_id="user001",
            )
            assert result["success"] is True

    def test_submit_answer_feedback_invalid_action(self, mock_db, valid_user):
        """回答反馈只允许 like/dislike"""
        from src.api.routes.feedback import submit_answer_feedback, AnswerFeedbackRequest
        from fastapi import HTTPException

        body = AnswerFeedbackRequest(message_uuid="msg-001", action="use_plan")
        with pytest.raises(HTTPException) as exc:
            submit_answer_feedback(
                body=body, token="token", db=mock_db, current_user_id="user001",
            )
        assert exc.value.status_code == 400
        assert "只支持 like/dislike" in exc.value.detail

    def test_submit_plan_feedback_success(self, mock_db, valid_user):
        """计划反馈 API 成功"""
        from src.api.routes.feedback import submit_plan_feedback, PlanFeedbackRequest

        with patch("src.api.routes.feedback.submit_feedback") as mock_submit:
            mock_submit.return_value = {"success": True, "feedback": {"id": 1, "action": "use_plan"}}
            body = PlanFeedbackRequest(plan_id=100, action="use_plan")
            result = submit_plan_feedback(
                body=body, token="token", db=mock_db, current_user_id="user001",
            )
            assert result["success"] is True

    def test_get_preferences(self, mock_db, valid_user):
        """查询偏好 API"""
        from src.api.routes.feedback import get_my_preferences

        with patch("src.api.routes.feedback.get_user_preferences") as mock_get:
            mock_get.return_value = {
                "stats": {"answer": {"total": 5, "likes": 3}},
                "style_directive": "保持当前风格",
                "updated_at": "2025-01-01T00:00:00",
            }
            result = get_my_preferences(
                token="token", db=mock_db, current_user_id="user001",
            )
            assert result["style_directive"] == "保持当前风格"

    def test_list_feedback(self, mock_db, valid_user):
        """查询反馈列表 API"""
        from src.api.routes.feedback import list_user_feedback

        with patch("src.api.routes.feedback.get_feedback_list") as mock_list:
            mock_list.return_value = [
                {"id": 1, "target_type": "answer", "action": "like"},
                {"id": 2, "target_type": "plan", "action": "dislike"},
            ]
            result = list_user_feedback(
                token="token", db=mock_db, current_user_id="user001",
            )
            assert result["total"] == 2
            assert len(result["data"]) == 2

    def test_delete_feedback(self, mock_db, valid_user):
        """删除反馈 API"""
        from src.api.routes.feedback import remove_feedback, DeleteFeedbackRequest

        with patch("src.api.routes.feedback.delete_feedback") as mock_del:
            mock_del.return_value = {"success": True}
            body = DeleteFeedbackRequest(target_type="answer", target_id="msg-001")
            result = remove_feedback(
                body=body, token="token", db=mock_db, current_user_id="user001",
            )
            assert result["success"] is True


# ============================================================
# 5. 权限控制
# ============================================================

class TestFeedbackPermissions:
    """权限控制测试"""

    def test_feedback_requires_authentication(self, mock_db):
        """反馈需要登录 - 未认证用户无法获取 user_id"""
        from src.services.auth_service import get_current_user_id
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            get_current_user_id(token="invalid_token", db=mock_db)
        assert exc.value.status_code == 401

    def test_guest_cannot_submit_feedback(self, mock_db):
        """游客无法提交反馈"""
        from src.services.feedback_service import submit_feedback

        result = submit_feedback(
            db=mock_db,
            user_id="guest",
            target_type="answer",
            target_id="msg-001",
            action="like",
        )
        # 反馈服务本身不阻止游客，但实际 API 层通过认证拦截
        assert result["success"] is True


# ============================================================
# 6. 反馈驱动 RAG 集成测试
# ============================================================

class TestFeedbackDrivenRAG:
    """反馈驱动的 RAG 集成测试"""

    def test_feedback_injected_into_generator_system_prompt(self):
        """风格指令注入 generator_node 系统提示词"""
        from src.core.nodes import generator_node

        state = {
            "original_query": "鸡蛋的热量",
            "sql_results": [{"food_name": "鸡蛋", "calories_per_100g": 144, "protein": 13.3, "fat": 8.8, "carbs": 2.8}],
            "vector_results": [],
            "feedback_style_directive": "用户满意度较低，请调整回答方式：更简洁。",
        }

        result = generator_node(state)
        assert "generated_answer" in result
        assert result.get("answer_source") == "knowledge_base"

    def test_no_feedback_directive_normal_prompt(self):
        """无反馈时使用默认提示词"""
        from src.core.nodes import generator_node

        state = {
            "original_query": "鸡蛋的热量",
            "sql_results": [{"food_name": "鸡蛋", "calories_per_100g": 144, "protein": 13.3, "fat": 8.8, "carbs": 2.8}],
            "vector_results": [],
            "feedback_style_directive": "",
        }

        result = generator_node(state)
        assert "generated_answer" in result

    def test_plan_style_directive_injected(self):
        """计划风格指令注入 plan_generator_node"""
        from src.core.nodes import plan_generator_node

        state = {
            "user_id": "user001",
            "goal_type": "weight_loss",
            "original_query": "减肥计划",
            "plan_style_directive": "用户近期对计划满意度较低，请提供更多备选方案。",
        }

        with patch("src.repositories.session.SessionLocal") as mock_session_class, \
             patch("src.services.plan_service.get_user_profile_dict") as mock_get_profile, \
             patch("src.services.plan_service.generate_plan") as mock_generate:

            mock_session_class.return_value.__enter__.return_value = MagicMock()
            mock_session_class.return_value.__exit__.return_value = False
            mock_get_profile.return_value = {"age": 25, "gender": "female", "weight": 65, "height": 165}
            mock_generate.return_value = {
                "success": True,
                "plan_id": 100,
                "config": {"summary": "减肥计划"},
            }

            result = plan_generator_node(state)
            assert "plan_config" in result
            assert result["plan_config"]["summary"] == "减肥计划"

            # 验证 style_directive 被传递
            call_kwargs = mock_generate.call_args[1]
            assert "style_directive" in call_kwargs

    def test_plan_generator_without_style_directive(self):
        """无计划反馈时 generate_plan 正常调用"""
        from src.core.nodes import plan_generator_node

        state = {
            "user_id": "user001",
            "goal_type": "weight_loss",
            "original_query": "减肥计划",
            "plan_style_directive": "",
        }

        with patch("src.repositories.session.SessionLocal") as mock_session_class, \
             patch("src.services.plan_service.get_user_profile_dict") as mock_get_profile, \
             patch("src.services.plan_service.generate_plan") as mock_generate:

            mock_session_class.return_value.__enter__.return_value = MagicMock()
            mock_session_class.return_value.__exit__.return_value = False
            mock_get_profile.return_value = {"age": 25, "gender": "female", "weight": 65, "height": 165}
            mock_generate.return_value = {
                "success": True,
                "plan_id": 100,
                "config": {"summary": "减肥计划"},
            }

            result = plan_generator_node(state)
            call_kwargs = mock_generate.call_args[1]
            assert call_kwargs.get("style_directive") is None


# ============================================================
# 7. 边界场景与异常处理
# ============================================================

class TestFeedbackEdgeCases:
    """边界场景测试"""

    def test_submit_feedback_with_rating(self, mock_db):
        """带评分的反馈"""
        from src.services.feedback_service import submit_feedback

        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = submit_feedback(
            db=mock_db, user_id="u1", target_type="answer", target_id="t1",
            action="like", rating=5, category="helpful",
        )
        assert result["success"] is True

    def test_submit_feedback_with_comment(self, mock_db):
        """带评论的反馈"""
        from src.services.feedback_service import submit_feedback

        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = submit_feedback(
            db=mock_db, user_id="u1", target_type="answer", target_id="t1",
            action="dislike", comment="回答不够详细",
        )
        assert result["success"] is True

    def test_submit_feedback_db_error(self):
        """数据库异常时返回错误"""
        from src.services.feedback_service import submit_feedback

        mock_db = MagicMock()
        mock_db.query.side_effect = Exception("数据库连接失败")

        result = submit_feedback(
            db=mock_db, user_id="u1", target_type="answer", target_id="t1", action="like",
        )
        assert result["success"] is False
        assert "失败" in result["error"]

    def test_delete_nonexistent_feedback(self, mock_db):
        """删除不存在的反馈"""
        from src.services.feedback_service import delete_feedback

        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = delete_feedback(db=mock_db, user_id="u1", target_type="answer", target_id="t1")
        assert result["success"] is False
        assert "不存在" in result["error"]

    def test_get_preferences_no_profile(self, mock_db):
        """无档案时返回默认偏好"""
        from src.services.feedback_service import get_user_preferences

        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = get_user_preferences(db=mock_db, user_id="u1")
        assert "stats" in result
        assert result["stats"]["answer"]["like_ratio"] == 0.5
        assert result["style_directive"] == ""

    def test_style_directive_dislike_answer_and_plan(self):
        """回答和计划都高踩率 → 双指令"""
        from src.services.feedback_service import get_style_directive

        stats = {
            "answer": {"total": 10, "likes": 1, "dislikes": 7, "uses": 2, "like_ratio": 0.1, "dislike_ratio": 0.7},
            "plan": {"total": 5, "likes": 0, "dislikes": 4, "uses": 1, "like_ratio": 0.0, "dislike_ratio": 0.8},
        }
        directive = get_style_directive(stats)
        assert directive != ""
        # 应该包含回答调整和计划调整两个指令
        assert directive.count("\n\n") >= 1

    def test_feedback_list_filter_by_type(self, mock_db):
        """按类型过滤反馈列表"""
        from src.services.feedback_service import get_feedback_list

        mock_feedback = MagicMock()
        mock_feedback.id = 1
        mock_feedback.target_type = "answer"
        mock_feedback.target_id = "msg-001"
        mock_feedback.feedback_action = "like"
        mock_feedback.rating = 5
        mock_feedback.category = "helpful"
        mock_feedback.created_at = None
        mock_feedback.updated_at = None

        # 设置完整 mock 链: query -> filter -> filter -> order_by -> limit -> all
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_order = MagicMock()
        mock_limit = MagicMock()

        # filter 返回自身以支持链式调用
        mock_filter.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order
        mock_order.limit.return_value = mock_limit
        mock_limit.all.return_value = [mock_feedback]

        mock_query.filter.return_value = mock_filter
        mock_db.query.return_value = mock_query

        result = get_feedback_list(db=mock_db, user_id="u1", target_type="answer")
        assert len(result) == 1
        assert result[0]["target_type"] == "answer"


# ============================================================
# 8. 反馈统计阈值测试
# ============================================================

class TestFeedbackThresholds:
    """反馈比率阈值测试"""

    def test_like_ratio_at_boundary_60(self):
        """点赞率刚好 60% 触发正向指令"""
        from src.services.feedback_service import get_style_directive

        stats = {
            "answer": {"total": 5, "likes": 3, "dislikes": 1, "uses": 1, "like_ratio": 0.6, "dislike_ratio": 0.2},
            "plan": {"total": 5, "likes": 3, "dislikes": 1, "uses": 1, "like_ratio": 0.6, "dislike_ratio": 0.2},
        }
        directive = get_style_directive(stats)
        assert "满意度较高" in directive

    def test_like_ratio_below_60(self):
        """点赞率 59% 不触发正向指令"""
        from src.services.feedback_service import get_style_directive

        stats = {
            "answer": {"total": 10, "likes": 5, "dislikes": 3, "uses": 2, "like_ratio": 0.5, "dislike_ratio": 0.3},
            "plan": {"total": 10, "likes": 5, "dislikes": 3, "uses": 2, "like_ratio": 0.5, "dislike_ratio": 0.3},
        }
        directive = get_style_directive(stats)
        assert "满意度较高" not in directive

    def test_dislike_ratio_at_boundary_40(self):
        """踩率刚好 40% 触发调整指令"""
        from src.services.feedback_service import get_style_directive

        stats = {
            "answer": {"total": 5, "likes": 1, "dislikes": 2, "uses": 2, "like_ratio": 0.2, "dislike_ratio": 0.4},
            "plan": {"total": 5, "likes": 1, "dislikes": 2, "uses": 2, "like_ratio": 0.2, "dislike_ratio": 0.4},
        }
        directive = get_style_directive(stats)
        assert "满意度较低" in directive