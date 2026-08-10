"""
utils/nutrition_calc.py 单元测试

覆盖：
- 活动等级/目标类型解析
- BMR 计算（Mifflin-St Jeor 公式）
- TDEE 计算（含活动系数/目标调整/宏量营养素分配）
- 安全底线校验
- 中文别名兼容
- 参数非法异常
"""
import pytest
from src.utils.nutrition_calc import (
    resolve_activity_level,
    resolve_goal_type,
    calculate_bmr,
    calculate_tdee,
    get_calorie_advice,
    ACTIVITY_LEVELS,
    MACRO_RATIOS,
    MIN_SAFE_CALORIES,
)


class TestResolveActivityLevel:
    """活动等级解析"""

    def test_english_standard_keys(self):
        for key in ["sedentary", "light", "moderate", "active", "very_active"]:
            assert resolve_activity_level(key) == key

    def test_chinese_alias(self):
        assert resolve_activity_level("久坐") == "sedentary"
        assert resolve_activity_level("中度") == "moderate"
        assert resolve_activity_level("运动员") == "very_active"

    def test_case_insensitive(self):
        assert resolve_activity_level("MODERATE") == "moderate"
        assert resolve_activity_level("Light") == "light"

    def test_empty_or_unknown_falls_back_to_sedentary(self):
        assert resolve_activity_level("") == "sedentary"
        assert resolve_activity_level(None) == "sedentary"
        assert resolve_activity_level("未知活动") == "sedentary"


class TestResolveGoalType:
    """目标类型解析"""

    def test_english_standard_keys(self):
        for key in ["weight_loss", "muscle_gain", "maintenance"]:
            assert resolve_goal_type(key) == key

    def test_chinese_alias(self):
        assert resolve_goal_type("减脂") == "weight_loss"
        assert resolve_goal_type("增肌") == "muscle_gain"
        assert resolve_goal_type("维持") == "maintenance"

    def test_empty_falls_back_to_maintenance(self):
        assert resolve_goal_type("") == "maintenance"
        assert resolve_goal_type(None) == "maintenance"


class TestCalculateBMR:
    """BMR 计算（Mifflin-St Jeor 公式）"""

    def test_male_bmr_formula(self):
        # 男性：BMR = 10*体重 + 6.25*身高 - 5*年龄 + 5
        bmr = calculate_bmr(weight_kg=70, height_cm=175, age=30, gender="male")
        expected = 10 * 70 + 6.25 * 175 - 5 * 30 + 5
        assert bmr == round(expected, 1)

    def test_female_bmr_formula(self):
        # 女性：BMR = 10*体重 + 6.25*身高 - 5*年龄 - 161
        bmr = calculate_bmr(weight_kg=60, height_cm=165, age=25, gender="female")
        expected = 10 * 60 + 6.25 * 165 - 5 * 25 - 161
        assert bmr == round(expected, 1)

    def test_gender_chinese_alias(self):
        bmr_male_en = calculate_bmr(70, 175, 30, "male")
        bmr_male_cn = calculate_bmr(70, 175, 30, "男")
        assert bmr_male_en == bmr_male_cn


class TestCalculateTDEE:
    """TDEE 主函数测试"""

    def test_basic_tdee_calculation(self, sample_user_profile):
        result = calculate_tdee(
            weight_kg=80, height_cm=175, age=30, gender="male",
            activity_level="moderate", goal_type="weight_loss",
        )
        assert result is not None
        assert "bmr" in result
        assert "tdee" in result
        assert "target_calories" in result
        assert "macros" in result
        assert "bmi" in result

    def test_tdee_activity_factor_applied(self):
        """TDEE 应该是取整后 BMR × 活动系数"""
        from src.utils.nutrition_calc import calculate_bmr
        result = calculate_tdee(70, 175, 30, "male", "sedentary")
        # calculate_bmr 内部已对 BMR round 到 1 位小数（banker's rounding），
        # TDEE 基于该取整后的 BMR 计算，测试需与之一致。
        bmr = calculate_bmr(70, 175, 30, "male")
        tdee_expected = bmr * ACTIVITY_LEVELS["sedentary"]
        assert result["tdee"] == round(tdee_expected, 1)

    def test_weight_loss_reduces_calories(self):
        """减脂目标应低于 TDEE"""
        result = calculate_tdee(70, 175, 30, "male", "moderate", "weight_loss")
        assert result["target_calories"] < result["tdee"]

    def test_muscle_gain_increases_calories(self):
        """增肌目标应高于 TDEE"""
        result = calculate_tdee(70, 175, 30, "male", "moderate", "muscle_gain")
        assert result["target_calories"] > result["tdee"]

    def test_maintenance_equals_tdee(self):
        """维持目标应等于 TDEE"""
        result = calculate_tdee(70, 175, 30, "male", "moderate", "maintenance")
        assert result["target_calories"] == result["tdee"]

    def test_macros_keys_present(self):
        result = calculate_tdee(70, 175, 30, "male", "moderate", "maintenance")
        macros = result["macros"]
        assert "protein_g" in macros
        assert "fat_g" in macros
        assert "carbs_g" in macros
        assert macros["protein_g"] > 0
        assert macros["fat_g"] > 0
        assert macros["carbs_g"] > 0

    def test_bmi_calculation(self):
        result = calculate_tdee(70, 175, 30, "male", "moderate")
        expected_bmi = 70 / (1.75 ** 2)
        assert result["bmi"] == round(expected_bmi, 1)

    def test_katch_mcardle_formula(self):
        """提供体脂率时使用 Katch-McArdle 公式"""
        result = calculate_tdee(
            80, 175, 30, "male", "moderate",
            body_fat_percentage=20,
        )
        lean_mass = 80 * (1 - 20 / 100)
        expected_bmr = 370 + 21.6 * lean_mass
        assert result["bmr"] == round(expected_bmr, 1)

    def test_safety_warning_for_low_target(self):
        """女性极低热量目标应触发安全警告并夹紧到安全底线

        BMR = 10*40 + 6.25*150 - 5*20 - 161 = 1076.5
        TDEE = 1076.5 * 1.2 = 1291.8
        target = 1291.8 - 500 = 791.8 < 1200 → 触发安全警告
        """
        result = calculate_tdee(40, 150, 20, "female", "sedentary", "weight_loss")
        assert result["safety_warning"] is not None
        assert result["target_calories"] == MIN_SAFE_CALORIES["female"]

    def test_no_safety_warning_for_normal_target(self):
        """正常目标不应触发安全警告"""
        result = calculate_tdee(70, 175, 30, "male", "moderate", "maintenance")
        assert result["safety_warning"] is None

    def test_invalid_params_raise_error(self):
        with pytest.raises(ValueError):
            calculate_tdee(weight_kg=0, height_cm=175, age=30, gender="male")
        with pytest.raises(ValueError):
            calculate_tdee(weight_kg=-50, height_cm=175, age=30, gender="male")
        with pytest.raises(ValueError):
            calculate_tdee(weight_kg=70, height_cm=0, age=30, gender="male")
        with pytest.raises(ValueError):
            calculate_tdee(weight_kg=70, height_cm=175, age=0, gender="male")


class TestGetCalorieAdvice:
    """饮食建议生成"""

    def test_weight_loss_advice(self):
        advice = get_calorie_advice(1800, "weight_loss")
        assert "1800" in advice
        assert "热量缺口" in advice

    def test_muscle_gain_advice(self):
        advice = get_calorie_advice(3000, "muscle_gain")
        assert "3000" in advice
        assert "热量盈余" in advice

    def test_maintenance_advice(self):
        advice = get_calorie_advice(2500, "maintenance")
        assert "2500" in advice
        assert "维持" in advice
