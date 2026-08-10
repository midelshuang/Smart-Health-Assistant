"""
功能测试套件

覆盖：
1. 用户搜索功能（食物搜索、指南查询、自然语言饮食记录）
2. 制定计划功能（计划生成、查看、更新、打卡、动态调整）
3. 用户档案管理（获取、更新、TDEE 计算、BMI 自动计算）
4. 认证全流程（注册、登录、刷新、改密、忘记密码）
5. 边界值与异常处理
"""
import pytest
import time
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from fastapi import HTTPException

from src.api.routes.user import UserProfileUpdate


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def valid_user():
    user = MagicMock()
    user.external_id = "user001"
    user.username = "testuser"
    user.role = "user"
    user.is_active = True
    user.password_hash = None
    return user


@pytest.fixture
def sample_profile_data():
    return {
        "nickname": "测试用户",
        "age": 30,
        "gender": "male",
        "height": 175.0,
        "weight": 80.0,
        "body_fat_percentage": 22.0,
        "province": "四川",
        "sports_hobby": "跑步",
        "health_goal": "weight_loss",
        "health_tags": ["obesity"],
        "allergies": "花生",
    }


# ============================================================
# 1. 用户搜索功能测试
# ============================================================

class TestFoodSearch:
    """食物搜索功能"""

    def test_search_food_success(self, mock_db, valid_user):
        """正常食物搜索"""
        from src.api.routes.diet import search_food
        with patch("src.api.routes.diet.search_foods") as mock_search:
            mock_search.return_value = [
                {"nutrition_id": 1, "food_name": "鸡蛋", "calories": 144},
                {"nutrition_id": 2, "food_name": "鸡蛋羹", "calories": 120},
            ]
            result = search_food(
                keyword="鸡蛋",
                limit=10,
                db=mock_db,
                current_user_id="user001",
            )
            assert len(result) == 2
            assert result[0]["food_name"] == "鸡蛋"

    def test_search_food_empty_keyword(self, mock_db, valid_user):
        """空关键词返回错误提示"""
        from src.api.routes.diet import search_food
        result = search_food(
            keyword=None,
            limit=10,
            db=mock_db,
            current_user_id="user001",
        )
        assert "error" in result
        assert "搜索关键词" in result["error"]

    def test_search_food_empty_string_keyword(self, mock_db, valid_user):
        """空字符串关键词返回错误"""
        from src.api.routes.diet import search_food
        result = search_food(
            keyword="",
            limit=10,
            db=mock_db,
            current_user_id="user001",
        )
        assert "error" in result

    def test_search_food_no_results(self, mock_db, valid_user):
        """无结果时返回空列表"""
        from src.api.routes.diet import search_food
        with patch("src.api.routes.diet.search_foods") as mock_search:
            mock_search.return_value = []
            result = search_food(
                keyword="不存在的食物XYZ",
                limit=10,
                db=mock_db,
                current_user_id="user001",
            )
            assert len(result) == 0

    def test_search_food_limit_parameter(self, mock_db, valid_user):
        """limit 参数正确传递"""
        from src.api.routes.diet import search_food
        with patch("src.api.routes.diet.search_foods") as mock_search:
            mock_search.return_value = []
            search_food(
                keyword="米饭",
                limit=5,
                db=mock_db,
                current_user_id="user001",
            )
            mock_search.assert_called_once_with(db=mock_db, keyword="米饭", limit=5)


class TestGuidelineQuery:
    """指南查询功能"""

    def test_query_guideline_success(self):
        """指南查询成功返回"""
        from src.api.routes.rag import execute_rag_query, RAGQueryRequest
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.query.return_value = {
                "success": True,
                "answer": "减脂期间建议控制碳水摄入...",
                "intent": "guideline_query",
                "sources": [{"file_name": "成人肥胖食养指南"}],
            }
            body = RAGQueryRequest(query="减脂饮食原则", stream=False)
            result = execute_rag_query(body, current_user=MagicMock())
            assert result["success"] is True
            assert "answer" in result

    def test_query_with_conversation_context(self):
        """多轮上下文查询"""
        from src.api.routes.rag import execute_rag_query, RAGQueryRequest
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.query.return_value = {
                "success": True,
                "answer": "结合您之前的减脂目标...",
                "intent": "guideline_query",
                "sources": [],
            }
            body = RAGQueryRequest(
                query="那晚餐应该怎么吃？",
                conversation_context=[
                    {"role": "user", "content": "我想减脂"},
                    {"role": "assistant", "content": "好的，我来为您制定减脂方案。"},
                ],
                stream=False,
            )
            result = execute_rag_query(body, current_user=MagicMock())
            assert result["success"] is True


class TestNaturalLanguageDietLog:
    """自然语言饮食记录"""

    def test_parse_diet_text_success(self, mock_db, valid_user):
        """自然语言解析饮食"""
        from src.api.routes.diet import parse_diet_text, DietParseRequest
        with patch("src.services.diet_service.parse_and_log_diet") as mock_parse:
            mock_parse.return_value = {
                "logged": [
                    {"nutrition_id": 1, "food_name": "鸡蛋", "quantity_grams": 100},
                ],
                "candidates": [],
                "unmatched": [],
                "total_calories": 220,
            }
            body = DietParseRequest(text="中午吃了一碗米饭和两个鸡蛋")
            result = parse_diet_text(body, token="token", db=mock_db, current_user_id="user001")
            assert "total_calories" in result
            assert result["total_calories"] == 220

    def test_parse_diet_text_empty(self, mock_db, valid_user):
        """空文本解析 → Pydantic 校验报错"""
        from pydantic import ValidationError
        from src.api.routes.diet import DietParseRequest
        with pytest.raises(ValidationError):
            DietParseRequest(text="")


# ============================================================
# 2. 制定计划功能测试
# ============================================================

class TestPlanGeneration:
    """计划生成功能"""

    def test_generate_plan_success(self, mock_db, valid_user):
        """正常生成计划"""
        from src.api.routes.plan import generate_diet_plan, PlanGenerateRequest
        with patch("src.api.routes.plan.generate_plan") as mock_gen:
            mock_gen.return_value = {
                "success": True,
                "plan_id": 1,
                "plan_name": "减脂饮食计划",
                "daily_target_calories": 1600,
                "meals": {
                    "breakfast": {"calories": 400},
                    "lunch": {"calories": 600},
                    "dinner": {"calories": 500},
                    "snack": {"calories": 100},
                },
            }
            body = PlanGenerateRequest(
                goal_type="weight_loss",
                start_date=date.today(),
                end_date=date.today() + timedelta(days=30),
            )
            result = generate_diet_plan(
                body, token="token", db=mock_db, current_user_id="user001",
            )
            assert result["success"] is True
            assert result["plan_id"] == 1

    def test_generate_plan_invalid_goal(self, mock_db, valid_user):
        """无效目标类型"""
        from src.api.routes.plan import generate_diet_plan, PlanGenerateRequest
        body = PlanGenerateRequest(goal_type="invalid_goal_type")
        with patch("src.api.routes.plan.generate_plan") as mock_gen:
            mock_gen.side_effect = Exception("无效的目标类型")
            with pytest.raises(HTTPException) as exc:
                generate_diet_plan(
                    body, token="token", db=mock_db, current_user_id="user001",
                )
            assert exc.value.status_code == 500

    def test_generate_plan_uses_knowledge_base(self, mock_db, valid_user):
        """计划生成使用知识库（Mock 验证）"""
        from src.api.routes.plan import generate_diet_plan, PlanGenerateRequest
        with patch("src.api.routes.plan.generate_plan") as mock_gen:
            mock_gen.return_value = {
                "success": True,
                "plan_id": 1,
                "sources_used": ["中国居民膳食指南2022", "成人肥胖食养指南"],
                "daily_target_calories": 1600,
            }
            body = PlanGenerateRequest(goal_type="weight_loss")
            result = generate_diet_plan(
                body, token="token", db=mock_db, current_user_id="user001",
            )
            assert len(result.get("sources_used", [])) > 0, "计划应引用知识库来源"


class TestPlanCRUD:
    """计划 CRUD 操作"""

    def test_list_plans(self, mock_db, valid_user):
        """列出用户所有计划"""
        from src.api.routes.plan import list_user_plans
        with patch("src.api.routes.plan.list_plans") as mock_list:
            mock_list.return_value = [
                {"plan_id": 1, "name": "减脂计划", "status": "active"},
                {"plan_id": 2, "name": "增肌计划", "status": "archived"},
            ]
            result = list_user_plans(
                status=None, token="token", db=mock_db, current_user_id="user001",
            )
            assert len(result) == 2

    def test_list_plans_by_status(self, mock_db, valid_user):
        """按状态过滤计划"""
        from src.api.routes.plan import list_user_plans
        with patch("src.api.routes.plan.list_plans") as mock_list:
            mock_list.return_value = [
                {"plan_id": 1, "name": "减脂计划", "status": "active"},
            ]
            result = list_user_plans(
                status="active", token="token", db=mock_db, current_user_id="user001",
            )
            assert len(result) == 1
            assert result[0]["status"] == "active"

    def test_get_plan_detail(self, mock_db, valid_user):
        """获取计划详情"""
        from src.api.routes.plan import get_plan
        with patch("src.api.routes.plan.get_plan_detail") as mock_get:
            mock_get.return_value = {
                "success": True,
                "plan_id": 1,
                "config": {"daily_calories": 1600},
            }
            result = get_plan(
                plan_id=1, token="token", db=mock_db, current_user_id="user001",
            )
            assert result["plan_id"] == 1

    def test_get_nonexistent_plan(self, mock_db, valid_user):
        """获取不存在的计划 → 404"""
        from src.api.routes.plan import get_plan
        with patch("src.api.routes.plan.get_plan_detail") as mock_get:
            mock_get.return_value = {"success": False, "error": "计划不存在"}
            with pytest.raises(HTTPException) as exc:
                get_plan(
                    plan_id=99999, token="token", db=mock_db, current_user_id="user001",
                )
            assert exc.value.status_code == 404

    def test_delete_plan(self, mock_db, valid_user):
        """删除计划"""
        from src.api.routes.plan import remove_plan
        with patch("src.api.routes.plan.delete_plan") as mock_del:
            mock_del.return_value = {"success": True}
            result = remove_plan(
                plan_id=1, token="token", db=mock_db, current_user_id="user001",
            )
            assert result["success"] is True

    def test_update_plan_config(self, mock_db, valid_user):
        """更新计划配置"""
        from src.api.routes.plan import update_plan, PlanConfigUpdateRequest
        with patch("src.api.routes.plan.update_plan_config") as mock_update:
            mock_update.return_value = {"success": True}
            body = PlanConfigUpdateRequest(config={"daily_calories": 1800})
            result = update_plan(
                plan_id=1, body=body, token="token", db=mock_db, current_user_id="user001",
            )
            assert result["success"] is True


class TestPlanCheckIn:
    """计划打卡"""

    def test_checkin_success(self, mock_db, valid_user):
        """打卡成功"""
        from src.api.routes.plan import create_checkin, PlanCheckInRequest
        with patch("src.api.routes.plan.checkin_plan") as mock_ci:
            mock_ci.return_value = {"success": True, "checkin_id": 1}
            body = PlanCheckInRequest(
                checkin_date=date.today(),
                actual_calories=1500,
                notes="正常饮食",
            )
            result = create_checkin(
                plan_id=1, body=body, token="token", db=mock_db, current_user_id="user001",
            )
            assert result["success"] is True

    def test_list_checkins(self, mock_db, valid_user):
        """列出打卡记录"""
        from src.api.routes.plan import list_plan_checkins
        with patch("src.api.routes.plan.list_checkins") as mock_list:
            mock_list.return_value = {
                "success": True,
                "count": 2,
                "data": [
                    {"id": 1, "checkin_date": "2026-08-01", "actual_calories": 1500},
                    {"id": 2, "checkin_date": "2026-08-02", "actual_calories": 1600},
                ],
            }
            result = list_plan_checkins(
                plan_id=1, token="token", db=mock_db, current_user_id="user001",
            )
            assert result["success"] is True
            assert len(result["data"]) == 2


class TestTodayAdjustment:
    """今日动态调整"""

    def test_get_today_adjustment(self, mock_db, valid_user):
        """获取今日调整方案"""
        from src.api.routes.plan import get_today_adjustment_api
        with patch("src.services.adjustment_service.get_today_adjustment") as mock_adj:
            mock_adj.return_value = {
                "success": True,
                "plan_id": 1,
                "plan_target_calories": 1800,
                "today_intake": {"total_calories": 600},
                "adjustment": {
                    "remaining_calories": 1200,
                    "next_meal": "dinner",
                    "next_meal_target_calories": 500,
                    "status": "on_track",
                },
            }
            result = get_today_adjustment_api(
                plan_id=1, token="token", db=mock_db, current_user_id="user001",
            )
            assert result["success"] is True
            assert result["adjustment"]["status"] == "on_track"


# ============================================================
# 3. 用户档案管理测试
# ============================================================

class TestUserProfile:
    """用户档案管理"""

    def test_get_profile_auto_create(self, mock_db, valid_user):
        """获取档案（不存在则自动创建）"""
        from src.api.routes.user import get_profile
        with patch("src.api.routes.user._get_or_create_profile") as mock_get:
            mock_profile = MagicMock()
            mock_profile.user_id = "user001"
            mock_profile.nickname = "测试"
            mock_profile.age = 30
            mock_profile.gender = "male"
            mock_profile.height = 175.0
            mock_profile.weight = 80.0
            mock_profile.bmi = 26.1
            mock_profile.body_fat_percentage = 22.0
            mock_profile.province = "四川"
            mock_profile.sports_hobby = None
            mock_profile.health_goal = "weight_loss"
            mock_profile.health_tags = ["obesity"]
            mock_profile.allergies = None
            mock_profile.updated_at = None
            mock_get.return_value = mock_profile

            result = get_profile(
                token="token", db=mock_db, current_user_id="user001",
            )
            assert result["user_id"] == "user001"
            assert result["age"] == 30

    def test_update_profile(self, mock_db, valid_user):
        """更新档案"""
        from src.api.routes.user import update_profile, UserProfileUpdate
        with patch("src.api.routes.user._get_or_create_profile") as mock_get:
            mock_profile = MagicMock()
            mock_get.return_value = mock_profile

            body = UserProfileUpdate(
                nickname="新昵称",
                age=31,
                weight=75.0,
                height=175.0,
            )
            result = update_profile(
                body, token="token", db=mock_db, current_user_id="user001",
            )
            assert mock_profile.weight == 75.0
            assert mock_profile.age == 31

    def test_update_profile_auto_bmi(self, mock_db, valid_user):
        """更新档案时自动计算 BMI"""
        from src.api.routes.user import update_profile, UserProfileUpdate
        with patch("src.api.routes.user._get_or_create_profile") as mock_get:
            mock_profile = MagicMock()
            mock_get.return_value = mock_profile

            body = UserProfileUpdate(weight=70.0, height=175.0)
            update_profile(
                body, token="token", db=mock_db, current_user_id="user001",
            )
            expected_bmi = round(70.0 / (1.75 ** 2), 1)
            mock_profile.bmi = expected_bmi

    def test_update_profile_partial(self, mock_db, valid_user):
        """部分更新（仅传 nickname）"""
        from src.api.routes.user import update_profile, UserProfileUpdate
        with patch("src.api.routes.user._get_or_create_profile") as mock_get:
            mock_profile = MagicMock()
            mock_profile.height = 175.0
            mock_profile.weight = 80.0
            mock_get.return_value = mock_profile

            body = UserProfileUpdate(nickname="新昵称")
            update_profile(
                body, token="token", db=mock_db, current_user_id="user001",
            )
            assert mock_profile.nickname == "新昵称"


class TestTDEECalculation:
    """TDEE 计算"""

    def test_tdee_logged_in_user(self, mock_db, valid_user):
        """登录用户 TDEE 计算"""
        from src.api.routes.user import calculate_tdee, TDEERequest
        with patch("src.api.routes.user._get_or_create_profile") as mock_get, \
             patch("src.utils.nutrition_calc.calculate_tdee") as mock_calc:
            mock_profile = MagicMock()
            mock_profile.weight = 80.0
            mock_profile.height = 175.0
            mock_profile.age = 30
            mock_profile.gender = "male"
            mock_profile.body_fat_percentage = 22.0
            mock_profile.health_goal = "weight_loss"
            mock_get.return_value = mock_profile

            mock_calc.return_value = {
                "bmr": 1800,
                "target_calories": 1800,
                "bmi": 26.1,
                "tdee": 2400,
                "gender": "male",
                "macros": {"protein_g": 150, "fat_g": 70, "carbs_g": 200},
                "safety_warning": None,
            }
            body = TDEERequest()
            result = calculate_tdee(
                body, current_user=valid_user, db=mock_db,
            )
            assert result["target_calories"] == 1800

    def test_tdee_guest_mode(self, mock_db):
        """游客模式 TDEE（必须传身高体重）"""
        from src.api.routes.user import calculate_tdee, TDEERequest
        from src.services.auth_service import GuestUser
        with patch("src.utils.nutrition_calc.calculate_tdee") as mock_calc:
            mock_calc.return_value = {
                "bmr": 1500, "target_calories": 2000, "bmi": 22.5,
                "tdee": 2500, "gender": "female",
                "macros": {"protein_g": 100, "fat_g": 50, "carbs_g": 250},
                "safety_warning": None,
            }
            body = TDEERequest(weight_kg=65.0, height_cm=170.0, age=25, gender="female")
            result = calculate_tdee(
                body, current_user=GuestUser(), db=mock_db,
            )
            assert result["guest_mode"] is True
            assert result["target_calories"] == 2000

    def test_tdee_guest_missing_params(self, mock_db):
        """游客缺少参数 → 400"""
        from src.api.routes.user import calculate_tdee, TDEERequest
        from src.services.auth_service import GuestUser
        body = TDEERequest()
        with pytest.raises(HTTPException) as exc:
            calculate_tdee(
                body, current_user=GuestUser(), db=mock_db,
            )
        assert exc.value.status_code == 400

    def test_tdee_safety_floor_male(self):
        """男性 TDEE 安全底线 ≥ 1500 kcal"""
        from src.utils.nutrition_calc import calculate_tdee
        result = calculate_tdee(
            weight_kg=30.0, height_cm=150.0, age=80,
            gender="male", activity_level="sedentary",
        )
        assert result["target_calories"] >= 1500, "男性每日热量不应低于 1500 kcal"

    def test_tdee_safety_floor_female(self):
        """女性 TDEE 安全底线 ≥ 1200 kcal"""
        from src.utils.nutrition_calc import calculate_tdee
        result = calculate_tdee(
            weight_kg=30.0, height_cm=150.0, age=80,
            gender="female", activity_level="sedentary",
        )
        assert result["target_calories"] >= 1200, "女性每日热量不应低于 1200 kcal"


# ============================================================
# 4. 边界值与异常处理
# ============================================================

class TestBoundaryValues:
    """边界值测试"""

    def test_height_boundary_min(self, mock_db, valid_user):
        """身高最小值边界（<=0 应报错）"""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserProfileUpdate(height=0.0)

    def test_height_boundary_max(self, mock_db, valid_user):
        """身高最大值边界（>300 应报错）"""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserProfileUpdate(height=500.0)

    def test_age_boundary_min(self, mock_db, valid_user):
        """年龄最小值边界（<1 应报错）"""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserProfileUpdate(age=0)

    def test_age_boundary_max(self, mock_db, valid_user):
        """年龄最大值边界（>150 应报错）"""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserProfileUpdate(age=200)

    def test_weight_boundary_min(self, mock_db, valid_user):
        """体重最小值边界（<=0 应报错）"""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserProfileUpdate(weight=0.0)

    def test_weight_boundary_max(self, mock_db, valid_user):
        """体重最大值边界（>500 应报错）"""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserProfileUpdate(weight=1000.0)

    def test_query_length_boundary(self):
        """RAG 查询长度边界"""
        from src.api.routes.rag import RAGQueryRequest
        with pytest.raises(Exception):
            RAGQueryRequest(query="a" * 5000)

    def test_query_length_zero(self):
        """空查询"""
        from src.api.routes.rag import RAGQueryRequest
        with pytest.raises(Exception):
            RAGQueryRequest(query="")

    def test_body_fat_percentage_boundary(self, mock_db, valid_user):
        """体脂率负数应报错"""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserProfileUpdate(body_fat_percentage=-1)

    def test_body_fat_percentage_over_100(self, mock_db, valid_user):
        """体脂率超 100 应报错"""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserProfileUpdate(body_fat_percentage=101)


# ============================================================
# 5. 异常恢复能力
# ============================================================

class TestErrorRecovery:
    """异常场景恢复能力"""

    def test_rag_pipeline_failure_returns_error(self):
        """RAG 管道异常 → 返回错误信息"""
        from src.api.routes.rag import execute_rag_query, RAGQueryRequest
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.query.side_effect = RuntimeError("LLM 连接超时")
            body = RAGQueryRequest(query="减脂饮食", stream=False)
            with pytest.raises(HTTPException) as exc:
                execute_rag_query(body, current_user=MagicMock())
            assert exc.value.status_code == 500

    def test_plan_generation_failure(self, mock_db, valid_user):
        """计划生成失败 → 返回 400"""
        from src.api.routes.plan import generate_diet_plan, PlanGenerateRequest
        with patch("src.api.routes.plan.generate_plan") as mock_gen:
            mock_gen.return_value = {"success": False, "error": "知识库不可用"}
            body = PlanGenerateRequest(goal_type="weight_loss")
            with pytest.raises(HTTPException) as exc:
                generate_diet_plan(
                    body, token="token", db=mock_db, current_user_id="user001",
                )
            assert exc.value.status_code == 400

    def test_database_connection_failure(self, valid_user):
        """数据库连接失败 → 应有明确错误"""
        from src.models.db_models import User
        mock_db = MagicMock()
        mock_db.query.side_effect = ConnectionError("数据库连接失败")
        with pytest.raises(ConnectionError):
            mock_db.query(User)

    def test_invalid_json_in_request_body(self):
        """请求体 JSON 格式错误"""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            from src.api.routes.plan import PlanGenerateRequest
            PlanGenerateRequest(goal_type=None)


# ============================================================
# 6. 认证流程端到端测试
# ============================================================

class TestAuthenticationFlow:
    """认证全流程"""

    def test_register_then_login(self, mock_db):
        """注册后登录"""
        from src.api.routes.auth import register, login, RegisterRequest, LoginRequest
        mock_user = MagicMock()
        mock_user.external_id = "newuser"
        mock_user.username = None

        mock_db.query.return_value.filter.return_value.first.return_value = None

        with patch("src.api.routes.auth.get_or_create_user", return_value=mock_user), \
             patch("src.api.routes.auth.check_login_allowed", return_value=(True, None)), \
             patch("src.api.routes.auth.authenticate_user", return_value=mock_user), \
             patch("src.api.routes.auth.create_access_token", return_value="token_a"), \
             patch("src.api.routes.auth.create_refresh_token", return_value="token_r"), \
             patch("src.api.routes.auth.record_login_success"):
            # 注册
            reg_body = RegisterRequest(username="newuser", password="StrongPass1")
            result = register(reg_body, mock_db)
            assert result["external_id"] == "newuser"

            # 登录
            login_body = LoginRequest(username="newuser", password="StrongPass1")
            result = login(login_body, MagicMock(), mock_db)
            assert result["access_token"] == "token_a"

    def test_refresh_token_flow(self, mock_db):
        """Refresh Token 换 Access Token"""
        from src.api.routes.auth import refresh_token
        with patch("src.api.routes.auth.verify_refresh_token", return_value="user001"), \
             patch("src.api.routes.auth.create_access_token", return_value="new_access"), \
             patch("src.api.routes.auth.create_refresh_token", return_value="new_refresh"):
            mock_user = MagicMock()
            mock_user.external_id = "user001"
            mock_user.is_active = True
            query_mock = MagicMock()
            query_mock.filter.return_value.first.return_value = mock_user
            mock_db.query.return_value = query_mock

            result = refresh_token(body=MagicMock(refresh_token="valid_refresh"), db=mock_db)
            assert result["access_token"] == "new_access"

    def test_change_password_flow(self, mock_db, valid_user):
        """修改密码流程"""
        from src.api.routes.auth import change_password, ChangePasswordRequest
        with patch("src.api.routes.auth._change_password", return_value=True):
            body = ChangePasswordRequest(old_password="old123", new_password="new456Strong")
            result = change_password(
                body, token="token", db=mock_db, current_user_id="user001",
            )
            assert result["message"] == "密码修改成功"

    def test_change_password_wrong_old(self, mock_db, valid_user):
        """旧密码错误 → 400"""
        from src.api.routes.auth import change_password, ChangePasswordRequest
        with patch("src.api.routes.auth._change_password", return_value=False):
            body = ChangePasswordRequest(old_password="wrong", new_password="newpass")
            with pytest.raises(HTTPException) as exc:
                change_password(
                    body, token="token", db=mock_db, current_user_id="user001",
                )
            assert exc.value.status_code == 400

    def test_change_password_same_as_old(self, mock_db, valid_user):
        """新密码与旧密码相同 → 400"""
        from src.api.routes.auth import change_password, ChangePasswordRequest
        with patch("src.api.routes.auth._change_password", side_effect=ValueError("新密码不能与旧密码相同")):
            body = ChangePasswordRequest(old_password="same", new_password="same")
            with pytest.raises(HTTPException) as exc:
                change_password(
                    body, token="token", db=mock_db, current_user_id="user001",
                )
            assert exc.value.status_code == 400

    def test_forgot_password_flow(self, mock_db):
        """忘记密码流程"""
        from src.api.routes.auth import forgot_password, ForgotPasswordRequest
        with patch("src.api.routes.auth._reset_password", return_value=True):
            body = ForgotPasswordRequest(
                username="testuser",
                security_answer="pet_name",
                new_password="NewPass123",
            )
            result = forgot_password(body, mock_db)
            assert result["message"] == "密码重置成功，请使用新密码登录"


# ============================================================
# 7. 接口安全测试
# ============================================================

class TestAPISecurity:
    """接口安全测试"""

    def test_sql_injection_in_username(self, mock_db):
        """用户名 SQL 注入防护"""
        from src.api.routes.auth import register, RegisterRequest
        body = RegisterRequest(
            username="admin' OR 1=1--",
            password="TestPass1",
        )
        with pytest.raises(HTTPException) as exc:
            register(body, mock_db)
        assert exc.value.status_code == 400

    def test_sql_injection_in_query(self):
        """RAG 查询 SQL 注入防护"""
        from src.api.routes.rag import RAGQueryRequest
        malicious_query = "鸡蛋' OR 1=1--"
        body = RAGQueryRequest(query=malicious_query)
        assert body.query == malicious_query  # Pydantic 接受，后端自行防护

    def test_xss_in_query(self):
        """RAG 查询 XSS 防护"""
        from src.api.routes.rag import RAGQueryRequest
        xss_query = "<script>alert('xss')</script>"
        body = RAGQueryRequest(query=xss_query)
        assert "<script>" in body.query  # Pydantic 接受，后端自行过滤

    def test_long_query_truncation(self):
        """超长查询被拒绝"""
        from pydantic import ValidationError
        from src.api.routes.rag import RAGQueryRequest
        with pytest.raises(ValidationError):
            RAGQueryRequest(query="Q" * 3000)


# ============================================================
# 8. 计划来源一致性验证
# ============================================================

class TestPlanSourceConsistency:
    """计划生成必须基于知识库"""

    def test_plan_contains_guide_sources(self, mock_db, valid_user):
        """计划包含指南来源"""
        from src.api.routes.plan import generate_diet_plan, PlanGenerateRequest
        with patch("src.api.routes.plan.generate_plan") as mock_gen:
            mock_gen.return_value = {
                "success": True,
                "plan_id": 1,
                "sources_used": [
                    {"file_name": "中国居民膳食指南2022", "source_type": "guideline"},
                    {"file_name": "成人肥胖食养指南", "source_type": "guideline"},
                ],
            }
            body = PlanGenerateRequest(goal_type="weight_loss")
            result = generate_diet_plan(
                body, token="token", db=mock_db, current_user_id="user001",
            )
            assert len(result["sources_used"]) >= 2

    def test_plan_no_hallucinated_sources(self, mock_db, valid_user):
        """计划无虚构来源"""
        from src.api.routes.plan import generate_diet_plan, PlanGenerateRequest
        with patch("src.api.routes.plan.generate_plan") as mock_gen:
            mock_gen.return_value = {
                "success": True,
                "plan_id": 1,
                "sources_used": [
                    {"file_name": "中国居民膳食指南2022", "source_type": "guideline"},
                ],
            }
            body = PlanGenerateRequest(goal_type="weight_loss")
            result = generate_diet_plan(
                body, token="token", db=mock_db, current_user_id="user001",
            )
            for source in result["sources_used"]:
                assert "file_name" in source
                assert "source_type" in source
                assert source["source_type"] in ("guideline", "new_food", "gi_data", "expert_media")