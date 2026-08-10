"""
安全护栏模块（SafetyCheckNode）

对用户查询进行双重校验：
1. 正则规则匹配（快速、确定性强）
2. LLM 语义判断（处理隐晦、变体表达）

拦截类别：
- 处方药/药物推荐请求
- 极端饮食方式（极低热量、断食、催吐等）
- 疾病诊断/治疗方案请求
- 其他违反医疗合规的问题

命中拦截时返回标准化的拒绝回复 + 引导建议。

⚠️ 规则外置：所有正则规则、LLM 提示词、分类回复均从
   config/safety_rules.yaml 加载，支持热更新。
   修改 YAML 后无需重启服务，下次调用自动生效。
"""

import re
import json
from typing import Dict, Any, List, Tuple
from src.utils.logger import setup_logger
from src.config.safety_rule_loader import get_safety_rules

logger = setup_logger(name="SafetyChecker")


# ============================================================
# 兼容性常量（保留以避免外部模块 import 失败）
# 这些常量仅作为初始默认值的快捷引用，实际校验时使用 loader 加载的规则
# ============================================================

# 模块加载时预取一次规则，作为 DANGEROUS_PATTERNS 的初始值
_initial_rules = get_safety_rules()

# 兼容旧代码：DANGEROUS_PATTERNS / CATEGORY_RESPONSES / SAFETY_LLM_PROMPT
# 注意：这些常量是加载时的快照，不会随热更新而变化；
# 实际校验函数 check_safety 始终使用 loader 的最新规则。
DANGEROUS_PATTERNS: List[Tuple[str, re.Pattern, str]] = [
    (r.name, r.pattern, r.reason) for r in _initial_rules.regex_rules if r.enabled
]
CATEGORY_RESPONSES: Dict[str, Dict[str, str]] = {
    cat: {"reply": r.reply, "suggestion": r.suggestion}
    for cat, r in _initial_rules.category_responses.items()
}
SAFETY_LLM_PROMPT: str = _initial_rules.llm_prompt


# ============================================================
# 核心校验函数
# ============================================================

def check_safety(query: str, use_llm: bool = True) -> Dict[str, Any]:
    """
    对用户查询进行安全校验（正则 + LLM 双重）

    规则来源：config/safety_rules.yaml（通过 SafetyRuleLoader 热加载）

    Args:
        query: 用户的原始查询
        use_llm: 是否启用 LLM 二次校验（默认 True）

    Returns:
        dict: {
            "is_safe": bool,           # True=安全可继续，False=命中拦截
            "category": str,           # 命中类别
            "reason": str,             # 命中原因
            "reply": str,              # 拒绝回复（命中时）
            "suggestion": str,         # 安全引导建议（命中时）
            "matched_rule": str,       # 命中的正则规则（正则命中时）
            "method": str,             # "regex" / "llm" / "both" / "pass"
            "rules_version": str,      # 命中时使用的规则版本
        }
    """
    # 每次校验都重新获取规则（loader 内部有 mtime 检测与缓存）
    rules = get_safety_rules()

    # 全局开关：紧急情况可临时关闭安全护栏
    if not rules.global_enabled:
        logger.warning("安全护栏已被全局关闭（global_enabled=false），放行所有查询")
        return {
            "is_safe": True,
            "category": "none",
            "reason": "安全护栏已关闭",
            "reply": "",
            "suggestion": "",
            "matched_rule": "",
            "method": "disabled",
            "rules_version": rules.version,
        }

    if not query or not query.strip():
        return {
            "is_safe": True,
            "category": "none",
            "reason": "空查询",
            "reply": "",
            "suggestion": "",
            "matched_rule": "",
            "method": "pass",
            "rules_version": rules.version,
        }

    # 1. 正则规则匹配（快速）
    for rule in rules.regex_rules:
        if not rule.enabled:
            continue
        match = rule.pattern.search(query)
        if match:
            category_key = rule.name
            response = rules.get_response(category_key)
            logger.warning(
                f"安全拦截[正则]: category={category_key}, "
                f"query='{query[:50]}', matched='{match.group()}'"
            )
            return {
                "is_safe": False,
                "category": category_key,
                "reason": rule.reason,
                "reply": response.reply,
                "suggestion": response.suggestion,
                "matched_rule": match.group(),
                "method": "regex",
                "rules_version": rules.version,
            }

    # 2. LLM 语义校验（处理隐晦表达）
    if use_llm and rules.llm_prompt:
        try:
            llm_result = _llm_safety_check(query, rules.llm_prompt)
        except Exception as e:
            logger.warning(f"LLM 安全检查异常，降级放行: {e}")
            return {
                "is_safe": True,
                "category": "none",
                "reason": f"LLM 异常，降级放行: {str(e)[:100]}",
                "reply": "",
                "suggestion": "",
                "matched_rule": "",
                "method": "llm_fallback",
                "rules_version": rules.version,
            }
        if llm_result.get("is_dangerous", False):
            category = llm_result.get("category", "other")
            if category == "none":
                category = "other"
            response = rules.get_response(category)
            # 优先使用 LLM 给出的理由，回退到标准回复
            reply = llm_result.get("reason") or response.reply
            suggestion = llm_result.get("suggestion") or response.suggestion

            logger.warning(
                f"安全拦截[LLM]: category={category}, query='{query[:50]}'"
            )
            return {
                "is_safe": False,
                "category": category,
                "reason": reply,
                "reply": response.reply,
                "suggestion": suggestion,
                "matched_rule": "",
                "method": "llm",
                "rules_version": rules.version,
            }

    # 3. 全部通过
    return {
        "is_safe": True,
        "category": "none",
        "reason": "通过安全校验",
        "reply": "",
        "suggestion": "",
        "matched_rule": "",
        "method": "pass",
        "rules_version": rules.version,
    }


def _llm_safety_check(query: str, prompt: str) -> Dict[str, Any]:
    """
    调用 LLM 进行语义级安全判断

    Args:
        query: 用户查询
        prompt: 当前生效的 LLM 提示词（来自 YAML）

    Returns:
        dict: LLM 返回的判断结果
    """
    try:
        from src.core.llm_client import get_llm_client

        llm = get_llm_client()
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"用户提问：{query}"},
        ]

        # 低温度保证稳定性
        response = llm.chat(messages, temperature=0.0, max_tokens=512)
        if not response:
            return {"is_dangerous": False, "category": "none", "reason": "LLM 无响应"}

        # 解析 JSON
        result = _parse_json_response(response)
        return result

    except Exception as e:
        logger.error(f"LLM 安全校验异常: {e}")
        # 异常时降级为放行（避免误伤正常用户），但记录日志
        return {"is_dangerous": False, "category": "none", "reason": f"LLM 异常: {e}"}


def _parse_json_response(text: str) -> Dict[str, Any]:
    """从 LLM 响应中提取 JSON"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        logger.error(f"无法解析 LLM 安全判断响应: {text[:200]}")
        return {"is_dangerous": False, "category": "none", "reason": "JSON 解析失败"}


# ============================================================
# 4. 标准化拒绝回复生成
# ============================================================

def build_safety_reply(safety_result: Dict[str, Any], user_query: str) -> str:
    """
    基于安全校验结果生成完整的人类可读拒绝回复

    Args:
        safety_result: check_safety 返回值
        user_query: 原始用户查询

    Returns:
        完整回复文本
    """
    if safety_result.get("is_safe", True):
        return ""

    reply = safety_result.get("reply", "")
    suggestion = safety_result.get("suggestion", "")
    category = safety_result.get("category", "other")

    parts = []
    if reply:
        parts.append(reply)
    if suggestion:
        parts.append(suggestion)

    # 根据类别追加额外引导
    if category == "prescription_drug":
        parts.append("如需了解药物与食物的相互作用，我可以为您提供饮食方面的建议。")
    elif category == "extreme_diet":
        parts.append("如果您想科学减重，我可以为您生成个性化的饮食计划，请说『帮我生成减脂饮食计划』。")
    elif category == "diagnosis_request":
        parts.append("如果您想了解某疾病的饮食调理原则，欢迎随时询问。")

    return "\n\n".join(parts)
