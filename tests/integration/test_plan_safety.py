# -*- coding: utf-8 -*-
"""R01+R08 回归测试：计划写路径强制安全校验（失败回退原菜）+ 解析失败不落 active。

背景缺陷：
1. (R01) 换菜/编辑计划后直接保存，无过敏原/疾病禁忌校验，也无热量重算；
2. (R08) LLM 输出解析失败时返回兜底结构，仍以 status=active 落库，
   垃圾计划占用用户的 active 计划位。

打桩方式：假 DB Session（按模型名分发查询结果）+ 假 LLM 客户端，
不依赖真实服务。
"""
import json
from types import SimpleNamespace

from src.services import plan_service
from src.services.plan_service import (
    _extract_plan_foods,
    _is_fallback_plan,
    _profile_to_validator_profile,
    _recalc_plan_calories,
    generate_plan,
    save_plan_config,
    update_plan_config,
)


# ============================================================
# 打桩工具
# ============================================================

class _FakeDB:
    """按 query 的模型名分发 first() 结果，记录 commit/add。"""

    def __init__(self, plan=None, profile=None, user=None):
        self.plan = plan
        self.profile = profile
        self.user = user
        self._model_name = ""
        self.committed = False
        self.added = []

    def query(self, model):
        self._model_name = getattr(model, "__name__", "")
        return self

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        if self._model_name == "UserProfile":
            return self.profile
        if self._model_name == "Plan":
            return self.plan
        if self._model_name == "User":
            return self.user
        return None

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def refresh(self, obj):
        pass

    def add(self, obj):
        self.added.append(obj)

    def close(self):
        pass


def _fake_profile():
    return SimpleNamespace(
        user_id="ext-1", nickname="测试用户", age=30, gender="男",
        height=175, weight=80, target_weight=70, activity_level="light",
        allergies="海鲜过敏，对虾蟹过敏", health_tags=["糖尿病"],
        dietary_preferences=None, bmi=26.1, body_fat_percentage=22.0,
        health_goal="减脂", province="北京", sports_hobby=None,
    )


def _fake_plan(config):
    return SimpleNamespace(
        id=1, user_id="ext-1", goal_type="fat_loss", config=config,
        start_date="2026-01-01", end_date=None, status="active",
        created_at="2026-01-01T00:00:00",
    )


def _safe_config():
    return {
        "days": [{
            "day_label": "第1天",
            "meals": [{
                "meal_type": "早餐",
                "items": [
                    {"food": "燕麦粥", "calories": 300},
                    {"food": "水煮西兰花", "calories": 50},
                ],
                "total_calories": 999,
            }],
            "total_calories": 999,
        }],
    }


# ============================================================
# 1. R01：换菜/编辑保存时强制校验
# ============================================================

def test_update_plan_rejects_allergen_food():
    """用户海鲜过敏，换入虾仁 → 拒绝保存，原 config 保持不变（回退原菜）。"""
    original = _safe_config()
    plan = _fake_plan(json.loads(json.dumps(original)))
    db = _FakeDB(plan=plan, profile=_fake_profile())

    swapped = json.loads(json.dumps(original))
    swapped["days"][0]["meals"][0]["items"].append({"food": "白灼虾仁", "calories": 90})

    result = update_plan_config(db=db, user_id="ext-1", plan_id=1, config=swapped)

    assert result["success"] is False
    assert result.get("risks"), "必须返回风险明细"
    assert plan.config == original, "校验失败时原计划 config 必须保持不变"
    assert db.committed is False


def test_update_plan_accepts_safe_food_and_recalcs():
    """安全换菜 → 保存成功，且每餐/每日热量被重算。"""
    plan = _fake_plan(_safe_config())
    db = _FakeDB(plan=plan, profile=_fake_profile())

    swapped = _safe_config()
    swapped["days"][0]["meals"][0]["items"].append({"food": "苹果", "calories": 95})

    result = update_plan_config(db=db, user_id="ext-1", plan_id=1, config=swapped)

    assert result["success"] is True
    assert db.committed is True
    assert plan.config["days"][0]["meals"][0]["total_calories"] == 445.0
    assert plan.config["days"][0]["total_calories"] == 445.0


def test_save_plan_config_rejects_allergen_food():
    """手动保存同样强制校验：含过敏原 → 拒绝且不落库。"""
    db = _FakeDB(profile=_fake_profile())
    bad = _safe_config()
    bad["days"][0]["meals"][0]["items"].append({"food": "清蒸螃蟹", "calories": 120})

    result = save_plan_config(
        db=db, user_id="ext-1", goal_type="fat_loss", config=bad)

    assert result["success"] is False
    assert result.get("risks")
    assert db.added == [] and db.committed is False


def test_save_plan_config_allows_when_no_profile():
    """无档案用户：无法校验 → 放行（保持可用性）。"""
    db = _FakeDB(profile=None)

    result = save_plan_config(
        db=db, user_id="guest-x", goal_type="fat_loss", config=_safe_config())

    assert result["success"] is True
    assert db.committed is True


# ============================================================
# 2. 档案适配与热量重算
# ============================================================

def test_profile_to_validator_profile_mapping():
    vp = _profile_to_validator_profile({
        "allergies": "对虾蟹过敏，喝牛奶拉肚子",
        "health_tags": ["2型糖尿病", "高血压"],
    })
    assert "seafood" in vp["allergens"]
    assert "milk" in vp["allergens"]
    assert "diabetes" in vp["diseases"]
    assert "hypertension" in vp["diseases"]


def test_recalc_plan_calories_skips_non_numeric_items():
    config = {
        "days": [{
            "day_label": "第1天",
            "meals": [{
                "meal_type": "午餐",
                "items": [{"food": "鸡胸肉", "calories": 200}, {"food": "未知菜品"}],
                "total_calories": 777,
            }],
        }],
    }
    _recalc_plan_calories(config)
    assert config["days"][0]["meals"][0]["total_calories"] == 200.0
    assert config["days"][0]["total_calories"] == 200.0


def test_extract_plan_foods():
    config = _safe_config()
    config["days"][0]["meals"].append({
        "meal_type": "晚餐",
        "items": [{"food": "杂粮饭", "calories": 210}],
    })
    foods = _extract_plan_foods(config)
    assert foods == ["燕麦粥", "水煮西兰花", "杂粮饭"]


# ============================================================
# 3. R08：解析失败不落 active
# ============================================================

def _stub_llm(monkeypatch, response_text):
    class _FakeLLM:
        def chat(self, messages, **kwargs):
            return response_text

    monkeypatch.setattr(
        "src.core.llm_client.get_llm_client", lambda: _FakeLLM())


def test_generate_plan_fallback_not_persisted(monkeypatch):
    """LLM 输出无法解析 → 返回兜底结构但不落库（plan_id=None, parse_failed=True）。"""
    _stub_llm(monkeypatch, "抱歉，我无法按格式输出 {{{ 这不是JSON")
    db = _FakeDB(user=SimpleNamespace(external_id="ext-1"), profile=_fake_profile())

    result = generate_plan(
        db=db,
        user_id="ext-1",
        goal_type="fat_loss",
        profile_override={"age": 30, "gender": "男", "height": 175, "weight": 80},
        guideline_context_override="测试指南内容",
        plan_duration="1_day",
    )

    assert result["parse_failed"] is True
    assert result["plan_id"] is None
    assert _is_fallback_plan(result["config"]) is True
    assert db.added == [] and db.committed is False, "兜底结构绝不允许落库"


def test_generate_plan_valid_json_persisted(monkeypatch):
    """LLM 输出可解析 → 正常落库（反向断言）。"""
    valid_plan = {
        "days": [{
            "day_label": "第1天",
            "meals": [{
                "meal_type": "早餐",
                "items": [{"food": "燕麦粥", "calories": 300}],
                "total_calories": 300,
            }],
        }],
    }
    _stub_llm(monkeypatch, json.dumps(valid_plan, ensure_ascii=False))
    db = _FakeDB(user=SimpleNamespace(external_id="ext-1"), profile=_fake_profile())

    result = generate_plan(
        db=db,
        user_id="ext-1",
        goal_type="fat_loss",
        profile_override={"age": 30, "gender": "男", "height": 175, "weight": 80},
        guideline_context_override="测试指南内容",
        plan_duration="1_day",
    )

    assert result["success"] is True
    assert _is_fallback_plan(result["config"]) is False
    assert db.committed is True
    assert db.added, "正常计划必须落库"
