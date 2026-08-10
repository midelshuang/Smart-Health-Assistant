"""
RAG_src/core/safety_checker.py 单元测试

覆盖：
- check_safety 主函数
- 正则规则匹配（4 类危险模式）
- LLM 二次校验（通过 mock）
- _parse_json_response 多种格式
- build_safety_reply 生成拒绝回复
- 边界情况（空查询/正常查询）
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from src.core.safety_checker import (
    check_safety,
    _parse_json_response,
    build_safety_reply,
    DANGEROUS_PATTERNS,
    CATEGORY_RESPONSES,
    SAFETY_LLM_PROMPT,
)


class TestCheckSafetyRegex:
    """正则规则匹配"""

    @pytest.mark.parametrize("query", [
        "帮我开点降压药",
        "给我开些二甲双胍",
        "需要吃阿司匹林吗",
        "胰岛素怎么用",
    ])
    def test_prescription_drug_blocked(self, query):
        result = check_safety(query, use_llm=False)
        assert result["is_safe"] is False
        assert result["category"] == "prescription_drug"
        assert result["method"] == "regex"
        assert result["reply"]
        assert result["suggestion"]

    @pytest.mark.parametrize("query", [
        "帮我诊断一下我是不是得了糖尿病",
        "我是不是患了高血压",
        "帮我看看这个化验单",
        "帮我制定治疗方案",
    ])
    def test_diagnosis_request_blocked(self, query):
        result = check_safety(query, use_llm=False)
        assert result["is_safe"] is False
        assert result["category"] == "diagnosis_request"

    @pytest.mark.parametrize("query", [
        "我想辟谷7天",
        "催吐能不能减肥",
        "每天只吃300卡",
        "想断食21天",
        "想禁食7天",
    ])
    def test_extreme_diet_blocked(self, query):
        result = check_safety(query, use_llm=False)
        assert result["is_safe"] is False
        assert result["category"] == "extreme_diet"

    @pytest.mark.parametrize("query", [
        "孕妇能吃药吗",
        "哺乳期能不能吃药",
        "儿童能吃感冒药吗",
        "孕妇用药咨询",
    ])
    def test_special_population_blocked(self, query):
        result = check_safety(query, use_llm=False)
        assert result["is_safe"] is False
        assert result["category"] == "special_population_intervention"

    def test_matched_rule_returned(self):
        result = check_safety("帮我开降压药", use_llm=False)
        assert result["matched_rule"]
        assert "降压药" in result["matched_rule"] or "开" in result["matched_rule"]


class TestCheckSafetySafe:
    """安全查询应放行"""

    @pytest.mark.parametrize("query", [
        "鸡蛋的热量是多少？",
        "糖尿病患者能吃水果吗？",
        "减肥时如何安排一日三餐？",
        "蛋白质对人体的作用是什么？",
    ])
    def test_safe_queries_pass(self, query):
        result = check_safety(query, use_llm=False)
        assert result["is_safe"] is True
        assert result["category"] == "none"
        assert result["method"] == "pass"

    def test_empty_query_passes(self):
        result = check_safety("", use_llm=False)
        assert result["is_safe"] is True
        assert result["method"] == "pass"

    def test_whitespace_query_passes(self):
        result = check_safety("   ", use_llm=False)
        assert result["is_safe"] is True


class TestCheckSafetyLLM:
    """LLM 二次校验"""

    def test_llm_blocks_dangerous_query(self, mock_llm):
        """LLM 标记为危险时应拦截"""
        # 正则放行但 LLM 标记危险
        mock_llm.chat_responses = [
            json.dumps({
                "is_dangerous": True,
                "category": "prescription_drug",
                "reason": "请求开具处方药",
                "suggestion": "请咨询医生",
            })
        ]
        # 用一个不会被正则匹配的隐晦查询
        result = check_safety("我能不能用点那个东西控制血糖", use_llm=True)
        assert result["is_safe"] is False
        assert result["method"] == "llm"

    def test_llm_passes_safe_query(self, mock_llm):
        mock_llm.chat_responses = [
            json.dumps({
                "is_dangerous": False,
                "category": "none",
                "reason": "正常饮食咨询",
                "suggestion": "",
            })
        ]
        result = check_safety("早餐吃什么好", use_llm=True)
        assert result["is_safe"] is True

    def test_llm_exception_falls_back_to_pass(self):
        """LLM 异常时应放行（避免误伤）"""
        with patch("src.core.llm_client.get_llm_client",
                   side_effect=RuntimeError("API error")):
            result = check_safety("正常问题", use_llm=True)
        assert result["is_safe"] is True

    def test_regex_takes_priority_over_llm(self, mock_llm):
        """正则命中时不再调用 LLM"""
        mock_llm.chat_responses = [
            json.dumps({"is_dangerous": False, "category": "none"})
        ]
        result = check_safety("帮我开降压药", use_llm=True)
        # 正则命中，不会用 LLM 的判断
        assert result["is_safe"] is False
        assert result["method"] == "regex"
        # LLM 应该没被调用
        assert len(mock_llm.chat_responses) == 1  # 仍有一份未消费


class TestParseJsonResponse:
    """_parse_json_response"""

    def test_plain_json(self):
        text = '{"is_dangerous": true, "category": "none"}'
        result = _parse_json_response(text)
        assert result["is_dangerous"] is True

    def test_json_with_code_fence(self):
        text = '```json\n{"is_dangerous": false, "category": "none"}\n```'
        result = _parse_json_response(text)
        assert result["is_dangerous"] is False

    def test_json_with_plain_code_fence(self):
        text = '```\n{"is_dangerous": true, "category": "extreme_diet"}\n```'
        result = _parse_json_response(text)
        assert result["is_dangerous"] is True

    def test_json_embedded_in_text(self):
        text = '一些文字 {"is_dangerous": true, "category": "none"} 更多文字'
        result = _parse_json_response(text)
        assert result["is_dangerous"] is True

    def test_invalid_json_returns_safe_default(self):
        text = "完全不是 JSON 的文字"
        result = _parse_json_response(text)
        assert result["is_dangerous"] is False
        assert result["category"] == "none"


class TestBuildSafetyReply:
    """build_safety_reply"""

    def test_safe_result_returns_empty(self):
        result = {
            "is_safe": True,
            "category": "none",
            "reply": "",
            "suggestion": "",
        }
        assert build_safety_reply(result, "any query") == ""

    def test_blocked_result_returns_reply(self):
        result = {
            "is_safe": False,
            "category": "prescription_drug",
            "reply": "药物决策需医生决定。",
            "suggestion": "请前往医院。",
        }
        reply = build_safety_reply(result, "帮我开药")
        assert "药物决策需医生决定" in reply
        assert "请前往医院" in reply

    def test_extreme_diet_adds_plan_suggestion(self):
        result = {
            "is_safe": False,
            "category": "extreme_diet",
            "reply": "极端饮食有害。",
            "suggestion": "请勿尝试。",
        }
        reply = build_safety_reply(result, "催吐减肥")
        assert "饮食计划" in reply

    def test_diagnosis_adds_diet_advice_hint(self):
        result = {
            "is_safe": False,
            "category": "diagnosis_request",
            "reply": "AI 无法诊断。",
            "suggestion": "请就诊。",
        }
        reply = build_safety_reply(result, "我是不是得了糖尿病")
        assert "饮食调理" in reply or "饮食" in reply


class TestDangerousPatterns:
    """DANGEROUS_PATTERNS 配置"""

    def test_patterns_cover_main_categories(self):
        categories = {p[0] for p in DANGEROUS_PATTERNS}
        assert "prescription_drug" in categories
        assert "diagnosis_request" in categories
        assert "extreme_diet" in categories

    def test_responses_cover_core_categories(self):
        """核心 category 必须有显式响应；其他 category 可 fallback 到 other"""
        core_categories = {"prescription_drug", "diagnosis_request", "extreme_diet", "other", "none"}
        for cat in core_categories:
            assert cat in CATEGORY_RESPONSES
            assert "reply" in CATEGORY_RESPONSES[cat]
            assert "suggestion" in CATEGORY_RESPONSES[cat]

    def test_unknown_category_falls_back_to_other(self):
        """未知 category 应该 fallback 到 other"""
        # special_population_intervention 不在 CATEGORY_RESPONSES 中，
        # 但 check_safety 通过 .get(key, other) 实现了 fallback
        result = check_safety("孕妇能吃药吗", use_llm=False)
        assert result["is_safe"] is False
        # 应有非空 reply（来自 other 或显式响应）
        assert result["reply"]
