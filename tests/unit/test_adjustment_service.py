"""
Service/adjustment_service.py 单元测试

覆盖：
- compute_adjustment 纯函数（核心算法）
  - 减脂目标（剩余热量充足/超标/不足）
  - 不同 current_meal 切换
  - 计划配置缺失场景
  - 餐次权重分配
- _determine_next_meal 推断下一餐
- _get_remaining_meal_weights 剩余餐次权重
- DEFAULT_MEAL_WEIGHTS / MIN_MEAL_CALORIES / MAX_MEAL_CALORIES 常量
"""
import pytest
from datetime import date

from src.services.adjustment_service import (
    compute_adjustment,
    _determine_next_meal,
    _get_remaining_meal_weights,
    get_today_intake,
    get_today_adjustment,
    DEFAULT_MEAL_WEIGHTS,
    MIN_MEAL_CALORIES,
    MAX_MEAL_CALORIES,
    MEAL_ORDER,
)


# ============================================================
# 常量
# ============================================================
class TestConstants:
    def test_meal_weights_sum_to_one(self):
        total = sum(DEFAULT_MEAL_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001

    def test_all_meals_in_order(self):
        for meal in ["breakfast", "lunch", "snack", "dinner"]:
            assert meal in MEAL_ORDER
            assert meal in DEFAULT_MEAL_WEIGHTS

    def test_safety_bounds(self):
        assert MIN_MEAL_CALORIES == 200
        assert MAX_MEAL_CALORIES == 1200
        assert MIN_MEAL_CALORIES < MAX_MEAL_CALORIES


# ============================================================
# _determine_next_meal
# ============================================================
class TestDetermineNextMeal:
    def test_no_meals_eaten_returns_breakfast(self):
        by_meal = {k: {"calories": 0} for k in MEAL_ORDER}
        assert _determine_next_meal(by_meal) == "breakfast"

    def test_breakfast_eaten_returns_lunch(self):
        by_meal = {
            "breakfast": {"calories": 500},
            "lunch": {"calories": 0},
            "snack": {"calories": 0},
            "dinner": {"calories": 0},
        }
        assert _determine_next_meal(by_meal) == "lunch"

    def test_all_meals_eaten_returns_snack(self):
        by_meal = {
            "breakfast": {"calories": 500},
            "lunch": {"calories": 700},
            "snack": {"calories": 100},
            "dinner": {"calories": 800},
        }
        assert _determine_next_meal(by_meal) == "snack"


# ============================================================
# _get_remaining_meal_weights
# ============================================================
class TestGetRemainingMealWeights:
    def test_returns_unfinished_meals_from_current(self):
        by_meal = {
            "breakfast": {"calories": 500},  # 已吃
            "lunch": {"calories": 0},        # 未吃
            "snack": {"calories": 0},
            "dinner": {"calories": 0},
        }
        remaining = _get_remaining_meal_weights(by_meal, "lunch")
        assert "lunch" in remaining
        assert "snack" in remaining
        assert "dinner" in remaining
        assert "breakfast" not in remaining  # 已吃

    def test_all_meals_eaten_returns_current_meal(self):
        by_meal = {
            "breakfast": {"calories": 500},
            "lunch": {"calories": 700},
            "snack": {"calories": 100},
            "dinner": {"calories": 800},
        }
        remaining = _get_remaining_meal_weights(by_meal, "dinner")
        # 都吃过了，至少给当前餐一个权重
        assert "dinner" in remaining


# ============================================================
# compute_adjustment
# ============================================================
class TestComputeAdjustment:
    """compute_adjustment 主函数"""

    @pytest.fixture
    def plan_config(self):
        return {
            "target_calories": 2000,
            "macros": {
                "protein_g": 150,
                "fat_g": 60,
                "carbs_g": 220,
            },
        }

    @pytest.fixture
    def empty_intake(self):
        return {
            "total_calories": 0,
            "total_protein_g": 0,
            "total_fat_g": 0,
            "total_carbs_g": 0,
            "by_meal": {k: {"calories": 0} for k in MEAL_ORDER},
        }

    def test_basic_structure(self, plan_config, empty_intake):
        result = compute_adjustment(plan_config, empty_intake)
        for key in [
            "target_calories", "consumed_calories", "remaining_calories",
            "next_meal", "next_meal_target_calories", "adjustment_ratio",
            "status", "message",
        ]:
            assert key in result, f"缺少字段 {key}"

    def test_no_intake_returns_under_budget(self, plan_config, empty_intake):
        result = compute_adjustment(plan_config, empty_intake)
        # 一口没吃 → 剩余 = 目标 = 2000，占比 100% > 50%
        assert result["status"] == "under_budget"
        assert result["remaining_calories"] == 2000

    def test_over_budget_when_exceeded(self, plan_config):
        """今日摄入 > 目标 + 100 → over_budget"""
        intake = {
            "total_calories": 2200,  # 超出 200
            "total_protein_g": 100,
            "total_fat_g": 50,
            "total_carbs_g": 250,
            "by_meal": {
                "breakfast": {"calories": 700},
                "lunch": {"calories": 800},
                "snack": {"calories": 300},
                "dinner": {"calories": 400},
            },
        }
        result = compute_adjustment(plan_config, intake)
        assert result["status"] == "over_budget"
        assert result["remaining_calories"] < 0

    def test_on_track_when_close_to_target(self, plan_config):
        """剩余在 [target*0.5 - target*0.5] 之间 → on_track"""
        intake = {
            "total_calories": 1200,  # 剩余 800，目标 2000，占比 40% < 50%
            "total_protein_g": 80,
            "total_fat_g": 30,
            "total_carbs_g": 130,
            "by_meal": {
                "breakfast": {"calories": 500},
                "lunch": {"calories": 700},
                "snack": {"calories": 0},
                "dinner": {"calories": 0},
            },
        }
        result = compute_adjustment(plan_config, intake)
        assert result["status"] == "on_track"

    def test_first_meal_is_breakfast_when_no_intake(self, plan_config, empty_intake):
        result = compute_adjustment(plan_config, empty_intake)
        assert result["next_meal"] == "breakfast"

    def test_explicit_current_meal_overrides_auto(self, plan_config, empty_intake):
        result = compute_adjustment(plan_config, empty_intake, current_meal="dinner")
        assert result["next_meal"] == "dinner"

    def test_next_meal_target_within_safety_bounds(self, plan_config, empty_intake):
        result = compute_adjustment(plan_config, empty_intake, current_meal="breakfast")
        target = result["next_meal_target_calories"]
        assert MIN_MEAL_CALORIES <= target <= MAX_MEAL_CALORIES

    def test_adjustment_ratio_when_no_intake(self, plan_config, empty_intake):
        """一口没吃时调整比例应较大（剩余 / 剩余餐次总权重 * 目标）"""
        result = compute_adjustment(plan_config, empty_intake)
        # 全部餐次剩余时，比例 = 1.0（恰好按计划分配）
        assert abs(result["adjustment_ratio"] - 1.0) < 0.05

    def test_missing_target_calories_returns_error(self):
        """计划缺少 target_calories 应返回 invalid_plan"""
        result = compute_adjustment(
            plan_config={"macros": {}},
            today_intake={"total_calories": 0, "by_meal": {}},
        )
        assert result.get("status") == "invalid_plan"
        assert "error" in result

    def test_remaining_macros_calculated(self, plan_config, empty_intake):
        result = compute_adjustment(plan_config, empty_intake)
        assert result["remaining_protein_g"] == 150
        assert result["remaining_fat_g"] == 60
        assert result["remaining_carbs_g"] == 220


# ============================================================
# get_today_intake / get_today_adjustment
# ============================================================
class TestGetTodayIntake:
    """get_today_intake 数据库聚合（无 DB 时跳过）"""

    def test_returns_empty_when_no_logs(self, require_db):
        from src.models.db_models import DailyDietLog
        # 清空今日记录
        db = require_db
        db.query(DailyDietLog).filter(
            DailyDietLog.user_id == "test_user_for_aggregation",
            DailyDietLog.log_date == date.today(),
        ).delete()
        db.commit()

        result = get_today_intake(db, "test_user_for_aggregation")
        assert result["total_calories"] == 0
        assert result["item_count"] == 0
        assert "by_meal" in result


class TestGetTodayAdjustment:
    """get_today_adjustment 服务入口（使用 Mock DB）"""

    def test_no_plan_returns_error(self):
        """未找到激活计划时应返回 success=False"""
        from unittest.mock import MagicMock
        db = MagicMock()
        # Mock query chain: db.query(Plan).filter(...).first() → None
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = None
        # order_by 也需要返回带 first() 的对象
        query_mock.filter.return_value.order_by.return_value.first.return_value = None
        db.query.return_value = query_mock

        result = get_today_adjustment(db=db, user_id=1)
        assert result["success"] is False
        assert "error" in result

    def test_invalid_plan_config_returns_error(self):
        """计划存在但 config 缺失 target_calories 时应返回 success=False"""
        from unittest.mock import MagicMock
        db = MagicMock()
        plan_mock = MagicMock()
        plan_mock.config = {}  # 空 config，无 target_calories
        plan_mock.id = 999
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = plan_mock
        db.query.return_value = query_mock

        result = get_today_adjustment(db=db, user_id=1, plan_id=999)
        assert result["success"] is False
