"""
餐次多样性兜底测试（_ensure_meal_diversity）

背景：LLM 生成 7 天计划时经常"只换早餐，午晚餐照抄 Day1"，
兜底函数需保证任何两天同餐次的食物组合签名唯一。
"""

from src.services.plan_service import (
    _ensure_meal_diversity,
    _meal_food_signature,
)


def _make_meal(meal_type, foods):
    return {
        "meal_type": meal_type,
        "time": "12:00",
        "items": [
            {"food": f, "quantity": "100g", "calories": 100, "cooking_method": "蒸"}
            for f in foods
        ],
        "total_calories": 100 * len(foods),
    }


def _make_day(day, breakfast, lunch, dinner):
    return {
        "day": day,
        "day_label": f"周{day}",
        "meals": [
            _make_meal("breakfast", breakfast),
            _make_meal("lunch", lunch),
            _make_meal("dinner", dinner),
        ],
    }


def _assert_meal_sigs_unique(config):
    """所有同餐次签名必须唯一"""
    seen = set()
    for d in config["days"]:
        for m in d["meals"]:
            sig = (m["meal_type"], _meal_food_signature(m))
            assert sig not in seen, f"餐次签名仍重复: {sig}"
            seen.add(sig)


def test_duplicated_lunch_dinner_get_mutated():
    """模拟真实 bug：Day3-7 午餐晚餐照抄 Day1，只有早餐不同 → 兜底必须修复"""
    config = {
        "duration_hint": "7_days",
        "days": [
            _make_day(1, ["燕麦粥", "煮鸡蛋"], ["糙米饭", "清蒸鸡胸肉", "清炒西兰花"], ["杂粮馒头", "豆腐蔬菜汤"]),
            _make_day(2, ["小米饭", "凉拌菠菜"], ["藜麦饭", "清蒸鲈鱼", "蒜蓉油麦菜"], ["蒸红薯", "虾仁冬瓜汤"]),
            _make_day(3, ["蒸紫薯", "凉拌黄瓜"], ["糙米饭", "清蒸鸡胸肉", "清炒西兰花"], ["杂粮馒头", "豆腐蔬菜汤"]),
            _make_day(4, ["全麦面包片"], ["糙米饭", "清蒸鸡胸肉", "清炒西兰花"], ["杂粮馒头", "豆腐蔬菜汤"]),
            _make_day(5, ["玉米棒"], ["糙米饭", "清蒸鸡胸肉", "清炒西兰花"], ["杂粮馒头", "豆腐蔬菜汤"]),
            _make_day(6, ["蒸山药"], ["糙米饭", "清蒸鸡胸肉", "清炒西兰花"], ["杂粮馒头", "豆腐蔬菜汤"]),
            _make_day(7, ["蒸紫薯", "无糖豆浆"], ["糙米饭", "清蒸鸡胸肉", "清炒西兰花"], ["杂粮馒头", "豆腐蔬菜汤"]),
        ],
    }
    fixed = _ensure_meal_diversity(config)
    # Day3-7 午餐 5 份重复 → 修 5；晚餐 5 份重复 → 修 5
    assert fixed == 10, f"应修复 10 个餐次，实际 {fixed}"
    _assert_meal_sigs_unique(config)
    # 原份量/热量框架保留（只换名字，calories 不动）
    day3_lunch = config["days"][2]["meals"][1]
    assert len(day3_lunch["items"]) == 3
    assert all(it["calories"] == 100 for it in day3_lunch["items"])


def test_diverse_plan_untouched():
    """本身多样化的计划 → 返回 0，内容不变"""
    config = {
        "duration_hint": "7_days",
        "days": [
            _make_day(1, ["燕麦粥"], ["糙米饭", "清蒸鸡胸肉"], ["杂粮馒头"]),
            _make_day(2, ["小米饭"], ["藜麦饭", "清蒸鲈鱼"], ["蒸红薯"]),
            _make_day(3, ["蒸紫薯"], ["荞麦面", "卤牛肉片"], ["蒸山药"]),
        ],
    }
    import copy

    snapshot = copy.deepcopy(config)
    fixed = _ensure_meal_diversity(config)
    assert fixed == 0
    assert config == snapshot


def test_swap_pool_extends_alt_pools():
    """30_days 计划的 food_swap_pool 应扩充替代池"""
    config = {
        "duration_hint": "30_days",
        "food_swap_pool": {
            "优质蛋白替代": ["清蒸鳕鱼", "卤鸡腿"],
            "主食替代": ["薏米饭"],
        },
        "days": [
            _make_day(1, ["燕麦粥"], ["糙米饭", "清蒸鸡胸肉"], ["杂粮馒头"]),
            _make_day(2, ["小米饭"], ["糙米饭", "清蒸鸡胸肉"], ["杂粮馒头"]),
            _make_day(3, ["蒸紫薯"], ["糙米饭", "清蒸鸡胸肉"], ["杂粮馒头"]),
        ],
    }
    fixed = _ensure_meal_diversity(config)
    assert fixed >= 2
    _assert_meal_sigs_unique(config)
    # 变异后的午餐应包含来自 swap_pool 的食材或默认池食材
    all_lunch_foods = {
        it["food"]
        for d in config["days"]
        for m in d["meals"] if m["meal_type"] == "lunch"
        for it in m["items"]
    }
    assert "糙米饭" not in all_lunch_foods or len(all_lunch_foods) >= 3
    # 清蒸鸡胸肉只允许保留一份（第一处）
    assert sum(1 for f in all_lunch_foods if f == "清蒸鸡胸肉") <= 1


def test_fewer_than_two_days_noop():
    """少于 2 天 → 不做任何处理"""
    config = {"days": [_make_day(1, ["燕麦粥"], ["糙米饭"], ["杂粮馒头"])]}
    assert _ensure_meal_diversity(config) == 0
