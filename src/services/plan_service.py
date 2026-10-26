"""
个性化饮食计划服务

职责：
1. 读取用户档案（身高/体重/BMI/体脂率/健康目标/省份/过敏史/运动爱好等）
2. 调用 RAG 管道检索相关权威指南（减脂/糖尿病/高血压等）
3. 调用 LLM 基于档案 + 指南生成结构化饮食计划
4. 持久化到 Plan 表（JSONB config）
5. 提供 CRUD + 打卡接口

生成的计划 config 结构（JSONB）：
{
  "summary": "计划摘要",
  "target_calories": 1800,
  "macros": {"protein_g": 90, "fat_g": 60, "carbs_g": 220},
  "meals": [
    {"meal_type": "breakfast", "time": "07:00-08:00",
     "items": [{"food": "全麦面包", "quantity": "2片", "calories": 200}],
     "total_calories": 450}
  ],
  "advice": "总体建议",
  "precautions": "注意事项"
}
"""

from typing import Dict, Any, List, Optional
from datetime import date, datetime
from decimal import Decimal
import json

from sqlalchemy.orm import Session

from src.models.db_models import Plan, PlanCheckIn, UserProfile, FitnessGoal
from src.utils.logger import setup_logger

logger = setup_logger(name="PlanService")


# ============================================================
# 1. 用户档案读取
# ============================================================

def _safe_float(value) -> Optional[float]:
    """安全转换 Decimal/None → float"""
    if value is None:
        return None
    return float(value)


def get_user_profile_dict(db: Session, user_id: str) -> Optional[Dict[str, Any]]:
    """
    读取用户档案，返回用于生成计划的字典

    Args:
        db: 数据库会话
        user_id: 用户 external_id

    Returns:
        用户档案字典，未找到返回 None
    """
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile:
        return None

    goal = db.query(FitnessGoal).filter(
        FitnessGoal.user_id == user_id,
        FitnessGoal.status == "active"
    ).order_by(FitnessGoal.id.desc()).first()

    return {
        "user_id": profile.user_id,
        "nickname": profile.nickname,
        "age": profile.age,
        "gender": profile.gender,
        "height": _safe_float(profile.height),
        "weight": _safe_float(profile.weight),
        "bmi": _safe_float(profile.bmi),
        "body_fat_percentage": _safe_float(profile.body_fat_percentage),
        "health_goal": profile.health_goal,
        "province": profile.province,
        "allergies": profile.allergies,
        "sports_hobby": profile.sports_hobby,
        "health_tags": profile.health_tags if profile.health_tags else [],
        "active_goal": {
            "goal_type": goal.goal_type if goal else None,
            "target_value": _safe_float(goal.target_value) if goal else None,
            "current_value": _safe_float(goal.current_value) if goal else None,
            "target_date": str(goal.target_date) if goal and goal.target_date else None,
        } if goal else None,
    }


# ============================================================
# 2. 计划生成（核心：LLM + RAG + 用户档案）
#    支持 3 种时长：1_day（快速首屏）/ 7_days（完整周计划）/ 30_days（月度计划框架）
# ============================================================

from typing import Literal
PlanDuration = Literal["1_day", "7_days", "30_days"]


def _build_plan_prompts(plan_duration: PlanDuration):
    """
    根据计划时长动态构建系统提示词和用户提示词模板。

    设计思路（解决超时问题）：
    - 1_day   : 仅生成 DAY 1，详细但体量小，20-30s 即可返回（安全）
    - 7_days  : 完整 DAY 1-7，多样化不重复，100-150s
    - 30_days : 4 周主题框架 + 第 1 周完整 7 天食谱 + 第 2-4 周换菜建议
                避免一次性写 28 天导致超时
    """
    # --- 通用核心要求（所有模式共享） ---
    COMMON_RULES = (
        "【核心要求 - 必须遵守】\n"
        "1. 仔细阅读【权威指南参考】，计划必须直接引用指南中的具体建议、食物、烹饪方式\n"
        "2. 必须以纯 JSON 格式输出，不要输出任何 JSON 之外的文字（包括注释、解释）\n"
        "3. 食物要考虑中国本地饮食习惯和用户所在地区\n"
        "4. 严格规避用户的过敏食物；如用户有糖尿病/高血压等疾病，需在 precautions 中标注专项注意事项\n"
        "5. 每个食物必须附带简单的 cooking_method（如「清蒸鲈鱼」→「清蒸8分钟」）\n"
        "6. 优先使用指南中附录的食谱示例和食养方，适当调整适用\n"
        "7. 若指南内容不足可基于通用营养学补充，但需在 advice 中标注「⚠️ 部分基于通用营养学知识」\n"
    )

    # --- 结构示例（1天 3 餐 —— 精简格式避免 7 天计划 token 爆炸） ---
    # 注意：不包含 recipe_steps，让模型只输出 food/quantity/calories/cooking_method 四要素
    ONE_DAY_EXAMPLE = (
        '    {{\n'
        '      "day": 1, "day_label": "周一",\n'
        '      "meals": [\n'
        '        {{"meal_type":"breakfast","time":"07:30","items":[\n'
        '          {{"food":"燕麦粥","quantity":"50g","calories":190,"cooking_method":"水煮"}},\n'
        '          {{"food":"煮鸡蛋","quantity":"1个","calories":70,"cooking_method":"沸水煮6分钟"}}],\n'
        '         "total_calories":260}},\n'
        '        {{"meal_type":"lunch","time":"12:00","items":[\n'
        '          {{"food":"糙米饭","quantity":"100g","calories":110,"cooking_method":"蒸"}},\n'
        '          {{"food":"清蒸鸡胸肉","quantity":"120g","calories":165,"cooking_method":"姜片料酒蒸"}},\n'
        '          {{"food":"清炒西兰花","quantity":"200g","calories":60,"cooking_method":"少油快炒"}}],\n'
        '         "total_calories":335}},\n'
        '        {{"meal_type":"dinner","time":"18:30","items":[\n'
        '          {{"food":"杂粮馒头","quantity":"1个","calories":180,"cooking_method":"蒸热"}},\n'
        '          {{"food":"豆腐蔬菜汤","quantity":"400g","calories":120,"cooking_method":"煮"}}],\n'
        '         "total_calories":300}}\n'
        '      ]\n'
        '    }}\n'
    )
    # 🆕 Day 2 示例食材必须和 Day 1 完全不同——否则 LLM 学到"复制模板"模式导致 7 天一样！
    DAY_TWO_EXAMPLE = (
        '    {{\n'
        '      "day": 2, "day_label": "周二",\n'
        '      "meals": [\n'
        '        {{"meal_type":"breakfast","time":"07:30","items":[\n'
        '          {{"food":"全麦面包片","quantity":"2片","calories":140,"cooking_method":"吐司"}},\n'
        '          {{"food":"无糖牛奶","quantity":"250ml","calories":135,"cooking_method":"温"}},\n'
        '          {{"food":"凉拌菠菜","quantity":"150g","calories":45,"cooking_method":"焯水凉拌"}}],\n'
        '         "total_calories":320}},\n'
        '        {{"meal_type":"lunch","time":"12:00","items":[\n'
        '          {{"food":"藜麦饭","quantity":"80g","calories":120,"cooking_method":"蒸"}},\n'
        '          {{"food":"清蒸鲈鱼","quantity":"150g","calories":180,"cooking_method":"葱姜蒸"}},\n'
        '          {{"food":"蒜蓉油麦菜","quantity":"200g","calories":55,"cooking_method":"快炒"}}],\n'
        '         "total_calories":355}},\n'
        '        {{"meal_type":"dinner","time":"18:30","items":[\n'
        '          {{"food":"蒸红薯","quantity":"150g","calories":125,"cooking_method":"蒸"}},\n'
        '          {{"food":"虾仁冬瓜汤","quantity":"400g","calories":110,"cooking_method":"煮"}}],\n'
        '         "total_calories":235}}\n'
        '      ]\n'
        '    }}\n'
    )

    # --- 不同时长的差异化配置 ---
    if plan_duration == "1_day":
        system_prompt = (
            "你是一位专业的注册营养师。你的任务是严格基于用户档案和权威指南文档，"
            "制定个性化的**首日（1天）示范饮食计划**——用于快速预览。\n\n"
            + COMMON_RULES
            + "8. 本次只需生成 DAY 1（1 天）示范日即可，务必写得详细、专业、可直接落地\n"
               "9. 输出 JSON 合法，days 数组长度 = 1，day.meals 数组长度 >= 3\n"
               "10. source_guidelines 必须**严格使用【权威指南参考】下方给出的指南名称**，禁止编造或照搬模板示例\n"
        )
        user_template = (
            "【用户档案】\n{user_profile}\n\n"
            "【权威指南参考 - 必须严格基于以下指南生成计划】\n{guideline_context}\n\n"
            "【系统自动识别到的引用指南名称 - source_guidelines 必须完整抄录以下列表】\n"
            "{guideline_titles_block}\n\n"
            "请基于以上信息，**先快速生成 DAY 1（1天）的详细示范饮食计划**（仅 1 天，用于预览）。\n"
            "在 summary 和 advice 中引用指南中的具体建议（标注「参考指南完整名称」）。\n\n"
            "输出纯 JSON（严格如下结构，不要包含 ```json 标记或其他文字）：\n"
            "{{{{\n"
            '  "duration_hint": "1_day",\n'
            '  "summary": "一句话摘要：首日示范计划，含目标热量，注明实际引用的指南名称",\n'
            '  "target_calories": 1800,\n'
            '  "macros": {{{{"protein_g": 90, "fat_g": 60, "carbs_g": 220}}}},\n'
            '  "days": [\n'
            + ONE_DAY_EXAMPLE
            + '  ],\n'
            '  "advice": "饮食建议，引用指南原则。末尾注明：本计划仅为第1天示范，可升级为完整7天/月度计划",\n'
            '  "precautions": "注意事项，含过敏/疾病提醒。",\n'
            '  "source_guidelines": [{guideline_titles_json}]\n'
            "}}}}\n"
            "【强制要求】\n"
            "- days 数组**只有 1 个元素**（DAY 1），不要写第 2 天及以后\n"
            "- day=1 的 meals 至少 breakfast、lunch、dinner 三项\n"
            "- source_guidelines 必须使用【系统自动识别】下面的指南名称，禁止写「引用的指南名称列表」这类占位文字\n"
            "- 输出只有 JSON，不要任何解释文字"
        )
        return system_prompt, user_template

    elif plan_duration == "7_days":
        system_prompt = (
            "你是一位专业的注册营养师。你的任务是严格基于用户档案和权威指南文档，"
            "制定个性化的**完整一周（7天）饮食计划**。\n\n"
            + COMMON_RULES
            + "8. 本次必须生成完整 DAY 1-7，每天至少早餐+午餐+晚餐三个餐次\n"
               "9. 7 天的食物必须多样化，避免连续两天重复相同的主菜\n"
               "10. 输出 JSON 合法，days 数组长度 = 7，每个 day.meals 长度 >= 3\n"
               "11. 不要省略任何一天的任何一餐，哪怕前几天写得很长，也要完整写满 7 天\n"
               "12. source_guidelines 必须**严格使用【权威指南参考】下方给出的指南名称**，禁止编造或照搬模板示例\n"
        )
        user_template = (
            "【用户档案】\n{user_profile}\n\n"
            "【权威指南参考 - 必须严格基于以下指南生成计划】\n{guideline_context}\n\n"
            "【系统自动识别到的引用指南名称 - source_guidelines 必须完整抄录以下列表】\n"
            "{guideline_titles_block}\n\n"
            "请基于以上信息，**生成完整的一周（DAY 1-7）个性化饮食计划**。\n"
            "在 summary 和 advice 中引用指南中的具体建议（标注「参考指南完整名称」）。\n\n"
            "输出纯 JSON（严格如下结构，不要包含 ```json 标记或其他文字）：\n"
            "{{{{\n"
            '  "duration_hint": "7_days",\n'
            '  "summary": "一句话摘要（含目标热量、注明实际引用的指南名称）",\n'
            '  "target_calories": 1800,\n'
            '  "macros": {{{{"protein_g": 90, "fat_g": 60, "carbs_g": 220}}}},\n'
            '  "days": [\n'
            # 🆕 Day 1 和 Day 2 用**完全不同的食材**示例 → 让 LLM 学到"每天不一样"的模式
            + ONE_DAY_EXAMPLE
            + ","
            + DAY_TWO_EXAMPLE
            + '  ],\n'
            '  "advice": "总体饮食建议，引用指南中的具体原则。",\n'
            '  "precautions": "注意事项，含过敏/疾病提醒。",\n'
            '  "source_guidelines": [{guideline_titles_json}]\n'
            "}}}}\n"
            "【强制要求 - 严格遵守否则无法生成】\n"
            "1. 上面模板示例写了 2 天（周一鸡胸+周二鲈鱼，食材完全不同），你必须完整写满 **DAY 1-7 共 7 天**\n"
            "2. day_label 依次使用：周一/周二/周三/周四/周五/周六/周日\n"
            "3. 每天至少 3 餐：breakfast、lunch、dinner，可另加 snack 加餐\n"
            "4. 7 天食物必须多样化：周一鸡胸→周二鱼→周三牛肉→周四虾→周五豆腐→周六鸡肉→周日菌菇（参考这个轮换节奏）\n"
            "5. ⚠️ 严禁连续两天出现相同主菜/主食/主要蔬菜，每天都必须换成新食材！\n"
            "6. 每天的 items 里 food/quantity/calories/cooking_method 四个字段**每个都必须填**\n"
            "7. 不要写 recipe_steps 字段（不输出烹饪步骤，节省 token）\n"
            "- 输出只有 JSON，不要任何解释文字，不要开头说「好的，为您生成...」\n"
        )
        return system_prompt, user_template

    else:  # 30_days
        # 🆕 用户方案：月度 = 完整 7 天基准周 + food_swap_pool（可置换食物池）
        # 不再要求 LLM 写 4 周主题框架——太容易偷懒只写 1 天，也浪费 token
        # 用户拿着 7 天基准 + 每类食物 5-10 个替换选项 → 自己组合 4 周不重复
        system_prompt = (
            "你是一位专业的注册营养师。你的任务是严格基于用户档案和权威指南文档，"
            "制定一个可执行 4 周的饮食计划，分两部分输出：\n"
            "⚠️ food_swap_pool 和 days 两者同等重要，都是必须项，不要遗漏任何一项！\n"
            "1. 完整的**第1周（7天）基准食谱**（每天三餐，可直接执行）\n"
            "2. **food_swap_pool（可置换食物池）**：按食物类别给出 5-10 个健康替代选项\n\n"
            + COMMON_RULES
            + "8. days 数组 = 7（周一~周日，每天三餐完整）\n"
               "9. food_swap_pool 必须包含以下类别，每类 5-10 个具体食物：\n"
               "   - 主食（全谷物/杂豆/薯类）、优质蛋白（鱼/禽/肉/蛋/豆）、蔬菜（深色叶菜/彩色蔬菜）、\n"
               "     水果（低糖/中糖）、健康脂肪（坚果/牛油果/橄榄油）、低卡饮品\n"
               "10. 输出 JSON 必须同时包含 days 和 food_swap_pool，任何一个缺失都是不合格\n"
        )
        user_template = (
            "【用户档案】\n{user_profile}\n\n"
            "【权威指南参考 - 必须严格基于以下指南生成计划】\n{guideline_context}\n\n"
            "【系统自动识别到的引用指南名称 - source_guidelines 必须完整抄录以下列表】\n"
            "{guideline_titles_block}\n\n"
            "请生成一个可执行 4 周的饮食计划，**两部分同等重要，缺一不可**：\n"
            "- 📅 第一部分：完整 7 天基准食谱（DAY 1-7，每天 breakfast+lunch+dinner 三餐完整）\n"
            "- 🔄 第二部分：food_swap_pool 可置换食物池（6 大类，每类 5-10 个替代选项）\n"
            "用户可用换菜池在 7 天基准上自由替换，实现 4 周不重复。\n"
            "在 summary 和 advice 中引用指南中的具体建议（标注「参考指南完整名称」）。\n\n"
            "输出纯 JSON（严格如下结构，不要包含 ```json 标记或其他文字）：\n"
            "{{{{\n"
            '  "duration_hint": "30_days",\n'
            '  "summary": "一句话月度摘要：目标热量+基准周说明+换菜池说明",\n'
            '  "target_calories": 1800,\n'
            '  "macros": {{{{"protein_g": 90, "fat_g": 60, "carbs_g": 220}}}},\n'
            '  "food_swap_pool": {{\n'
            '    "主食替代": ["糙米","燕麦","藜麦","小米","红薯","玉米","红豆",...],\n'
            '    "优质蛋白替代": ["清蒸鲈鱼","香煎三文鱼","虾仁","瘦牛肉","豆腐",...],\n'
            '    "蔬菜替代": ["菠菜","西兰花","油麦菜","胡萝卜","番茄",...],\n'
            '    "水果替代": ["蓝莓","苹果","橙子","草莓","猕猴桃",...],\n'
            '    "健康脂肪替代": ["杏仁","牛油果","橄榄油","花生","核桃",...],\n'
            '    "低卡饮品": ["无糖豆浆","乌龙茶","黑咖啡","无糖气泡水","绿茶",...]\n'
            '  }},\n'
            '  "days": [\n'
            + ONE_DAY_EXAMPLE
            + ","
            + DAY_TWO_EXAMPLE
            + '  ],\n'
            '  "advice": "月度总体饮食建议，引用指南原则；说明如何用换菜池实现4周不重复（例如：每周固定餐次替换，每餐只换1-2种食材）",\n'
            '  "precautions": "注意事项，含过敏/疾病提醒及各类食物禁忌",\n'
            '  "source_guidelines": [{guideline_titles_json}]\n'
            "}}}}\n"
            "【强制要求 - 两项都必须有！】\n"
            "- ✅ days 必须 = 7 条（DAY 1-7，完整周基准食谱，day_label=周一~周日，每天至少 3 餐）\n"
            "- ✅ food_swap_pool 必须 >= 6 个类别，每类 5-10 个具体食物名\n"
            "- ⚠️ 任何一项缺失都算失败，必须重新生成\n"
            "- 输出只有 JSON，不要任何解释文字"
        )
        return system_prompt, user_template


def _format_user_profile(profile: Dict[str, Any]) -> str:
    """将用户档案字典格式化为 LLM 易读的文本"""
    lines = []
    lines.append(f"- 昵称: {profile.get('nickname', '未知')}")
    lines.append(f"- 年龄: {profile.get('age', '未知')}")
    lines.append(f"- 性别: {profile.get('gender', '未知')}")
    lines.append(f"- 身高: {profile.get('height', '未知')} cm")
    lines.append(f"- 体重: {profile.get('weight', '未知')} kg")
    lines.append(f"- BMI: {profile.get('bmi', '未知')}")
    lines.append(f"- 体脂率: {profile.get('body_fat_percentage', '未知')} %")
    lines.append(f"- 健康目标: {profile.get('health_goal', '未指定')}")
    lines.append(f"- 所在省份: {profile.get('province', '未知')}")
    lines.append(f"- 过敏史: {profile.get('allergies', '无')}")
    lines.append(f"- 运动爱好: {profile.get('sports_hobby', '未指定')}")
    if profile.get("health_tags"):
        lines.append(f"- 健康标签: {', '.join(profile['health_tags'])}")
    goal = profile.get("active_goal")
    if goal and goal.get("goal_type"):
        lines.append(f"- 当前健身目标: {goal['goal_type']}")
        if goal.get("target_value"):
            lines.append(f"  目标值: {goal['target_value']}")
        if goal.get("current_value"):
            lines.append(f"  当前值: {goal['current_value']}")
    return "\n".join(lines)


def _format_guideline_titles_block(titles: Optional[List[str]]) -> str:
    """格式化指南名称为可读列表（给 LLM 看）"""
    if not titles:
        return "（未识别到权威指南，请基于通用营养学知识生成计划，并在 source_guidelines 中写「通用营养学知识」）"
    lines = []
    for i, t in enumerate(titles, 1):
        lines.append(f"  {i}. 《{t}》")
    return "\n".join(lines)


def _format_guideline_titles_json(titles: Optional[List[str]]) -> str:
    """格式化指南名称为 JSON 数组字面量（可直接嵌入 prompt 的 JSON 示例里）"""
    if not titles:
        return '"通用营养学知识"'
    return ", ".join(f'"{t}"' for t in titles)


def _build_plan_query(profile: Dict[str, Any]) -> str:
    """基于用户档案构建用于 RAG 检索的查询语句"""
    parts = []
    age = profile.get("age")
    gender = profile.get("gender")
    region = profile.get("province") or profile.get("region")
    bmi = profile.get("bmi")
    health_goal = profile.get("health_goal")
    health_tags = profile.get("health_tags", [])

    # 1. 年龄路由
    if age is not None:
        if age < 2:
            parts.append("婴幼儿喂养指南")
        elif age < 6:
            parts.append("学龄前儿童膳食指南")
        elif age < 12:
            parts.append("学龄儿童膳食指南")
        elif age < 18:
            parts.append("儿童青少年肥胖食养指南")
            parts.append("儿童青少年膳食指南")
        elif age < 50:
            if bmi and bmi >= 24:
                parts.append("成人肥胖食养指南")
            else:
                parts.append("中国居民膳食指南")
        elif age < 65:
            parts.append("中老年人营养指南")
        else:
            parts.append("老年人膳食指南")

    # 2. 地区路由
    if region:
        parts.append(f"{region}地区饮食")
        parts.append(f"{region}食谱")

    # 3. 健康目标/标签 → 附加食谱关键词
    if health_goal:
        goal_map = {
            "weight_loss": "肥胖减重饮食 减脂食谱 食养方",
            "muscle_gain": "增肌营养饮食 高蛋白食谱",
            "health_maintenance": "健康饮食维持 平衡膳食",
            "blood_sugar_control": "糖尿病饮食 糖尿病食谱 低糖食谱",
            "blood_pressure_control": "高血压饮食 高血压食谱 低盐食谱",
        }
        mapped = goal_map.get(health_goal, health_goal)
        parts.append(mapped)

    if health_tags:
        for tag in health_tags:
            if tag in ("糖尿病", "高血压", "高血脂", "肥胖", "超重"):
                parts.append(f"{tag}饮食指南 {tag}食谱")

    # 4. BMI 补充
    if bmi is not None:
        if bmi >= 28:
            parts.append("重度肥胖饮食 减肥食谱")
        elif bmi >= 24:
            parts.append("超重肥胖饮食 减脂食谱")
        elif bmi < 18.5:
            parts.append("低体重增重饮食 增肌食谱")

    # 5. 食谱/食养方/做法关键词（专项召回）
    parts.append("食谱 食养方 做法 烹饪步骤 食材")

    if not parts:
        parts.append("健康饮食指南")

    return " ".join(parts)


def _retrieve_guidelines(query: str) -> str:
    """
    直接调用向量+关键词混合检索相关权威指南（不走完整RAG管道）

    Args:
        query: 检索查询语句

    Returns:
        格式化后的指南上下文文本
    """
    try:
        from src.repositories.vector_store import VectorStore
        from src.core.embedding import get_embedding_function
        from src.config import settings

        vs = VectorStore()
        vs.connect()
        embedding_fn = get_embedding_function()
        collection = vs.get_or_create_collection(embedding_function=embedding_fn)

        # 1. 向量检索
        vector_results = vs.similarity_search(
            query_text=query,
            top_k=settings.RAG_TOP_K,
            threshold=0.2,
            where={"source_type": {"$in": ["guideline", "expert_media"]}},
            embedding_function=embedding_fn,
        )

        # 2. 关键词检索
        keyword_results = []
        try:
            import re
            all_data = collection.get(
                include=["documents", "metadatas"],
                where={"source_type": {"$in": ["guideline", "expert_media"]}},
            )
            if all_data and all_data["ids"]:
                query_lower = query.lower()
                keywords = set()
                for token in re.split(r'\s+', query_lower):
                    if len(token) >= 2:
                        keywords.add(token)
                        for i in range(len(token) - 1):
                            keywords.add(token[i:i+2])

                for i, doc_id in enumerate(all_data["ids"]):
                    doc = all_data["documents"][i] or ""
                    meta = all_data["metadatas"][i] or {}
                    doc_lower = doc.lower()
                    score = 0.0
                    for kw in keywords:
                        if kw in doc_lower:
                            score += doc_lower.count(kw) * 0.1
                    file_name = meta.get("file_name", "").lower()
                    for kw in keywords:
                        if kw in file_name:
                            score += 0.5
                    if score > 0:
                        keyword_results.append({
                            "id": doc_id,
                            "content": doc,
                            "score": min(score, 1.0),
                            "metadata": meta,
                        })
                keyword_results.sort(key=lambda x: x["score"], reverse=True)
                keyword_results = keyword_results[:settings.RAG_TOP_K]
        except Exception:
            pass

        # 3. 合并结果
        all_results = vector_results + keyword_results
        if not all_results:
            return "（未检索到相关指南）"

        # 去重（按id）
        seen_ids = set()
        unique_results = []
        for r in all_results:
            rid = r.get("id", "")
            if rid not in seen_ids:
                seen_ids.add(rid)
                unique_results.append(r)

        # 按分数排序
        unique_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        unique_results = unique_results[:8]

        lines = []
        for i, item in enumerate(unique_results, 1):
            meta = item.get("metadata", {})
            source = meta.get("file_name", "未知来源")
            source_type = meta.get("source_type", "guideline")
            content = item.get("content", "")
            if len(content) > 600:
                content = content[:600] + "..."
            lines.append(f"{i}. 【来源: {source}（{source_type}）】{content}")

        context = "\n\n".join(lines)
        logger.info(f"_retrieve_guidelines: 向量{len(vector_results)}条 + 关键词{len(keyword_results)}条 → 去重后{len(unique_results)}条, 上下文{len(context)}字符")
        return context

    except Exception as e:
        logger.warning(f"RAG 检索指南失败，将仅基于档案生成: {e}")
        return "（RAG 检索失败，请基于专业知识生成）"


def _fix_llm_json_dirty(text: str) -> str:
    """
    对 LLM 输出的疑似 JSON 做「脏数据修复」：
    1) 去除控制字符（\n\r\t 保留）
    2) 去除数组/对象字面量末尾的尾逗号
    3) 启发式修复字符串内未转义的双引号（中文描述里夹带英文双引号的情况）
    4) 去除 markdown 中 ```json ... ``` 外层再出现的 ` 号残留
    5) 将 ...}} , ...]] 中间奇怪字符清理
    """
    import re
    if not text:
        return text

    # 1) 控制字符清理（保留换行/回车/tab）
    cleaned_chars = []
    for ch in text:
        code = ord(ch)
        if code < 0x20 and ch not in "\n\r\t":
            continue
        if code == 0x7F:
            continue
        cleaned_chars.append(ch)
    s = "".join(cleaned_chars)

    # 2) 尾逗号去除：`,\s*}` 和 `,\s*]`（非字符串内部场景，简单正则近似）
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)

    # 3) 启发式：字符串内未转义引号修复
    #    扫描字符，跟踪 in_string 状态。进入字符串后，如果再遇到 " 且下一个字符
    #    既不是合法分隔符（,: 空白 } ]），也不是字符串结尾位置，则视为字符串内部
    #    裸引号，插入转义。
    out = []
    i = 0
    n = len(s)
    in_string = False
    escape = False
    while i < n:
        ch = s[i]
        if in_string:
            if escape:
                out.append(ch)
                escape = False
                i += 1
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                i += 1
                continue
            if ch == '"':
                # 检查这是否是合理的字符串结束引号
                # 向后看：跳过空白，应该是 , : } ] EOF 之一
                j = i + 1
                while j < n and s[j] in " \t\r\n":
                    j += 1
                nxt = s[j] if j < n else ""
                # 向前看：上一个非空字符，排除行首引号（新字段）
                k = i - 1
                while k >= 0 and s[k] in " \t\r\n":
                    k -= 1
                prev = s[k] if k >= 0 else ""
                valid_end = (nxt in ",:}]" or nxt == "") and (prev not in "[{,:")
                # 也允许 "key": "value" 情形的引号闭合（prev 是 :、合法分隔符之一）
                key_val_case = (prev == ":") or (prev == "{") or (prev == ",") or (prev == "[")
                if valid_end or key_val_case:
                    # 大概率是合法闭合
                    out.append('"')
                    in_string = False
                else:
                    # 视为字符串内裸引号 → 转义
                    out.append('\\"')
                i += 1
                continue
            # 其他字符：字符串内 \n → \\n（JSON 字符串里不能直接有换行）
            if ch == "\n":
                out.append("\\n")
                i += 1
                continue
            if ch == "\r":
                out.append("\\r")
                i += 1
                continue
            if ch == "\t":
                out.append("\\t")
                i += 1
                continue
            out.append(ch)
            i += 1
        else:
            # 不在字符串里
            if ch == '"':
                out.append('"')
                in_string = True
                i += 1
                continue
            # 跳过反引号残留
            if ch == "`":
                i += 1
                continue
            out.append(ch)
            i += 1
    s = "".join(out)

    # 再次清理尾逗号（转义过程可能引入的）
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)
    return s


def _fallback_plan_structure(raw_text: str, plan_duration: str = "1_day") -> Dict[str, Any]:
    """
    LLM JSON 完全无法解析时的最小可渲染兜底结构：
    - 保证至少能显示表格/头部信息
    - 把 raw_text 截取塞到 summary / advice 中，便于用户和调试者看到实际内容
    """
    excerpt = raw_text.strip()
    if len(excerpt) > 1500:
        excerpt = excerpt[:1500] + "\n\n...(内容过长已截断，请在日志中查看完整 LLM 输出)"
    # 粗略提取餐次关键词做示意
    meals_demo = [
        {
            "meal_type": "breakfast",
            "time": "07:30",
            "items": [
                {"food": "LLM 输出 JSON 解析失败", "quantity": "—", "calories": 0, "cooking_method": "请查看建议中的原文"},
            ],
            "total_calories": 0,
        }
    ]
    days_arr = []
    if plan_duration == "1_day":
        days_arr = [{
            "day": 1,
            "day_label": "第1天（解析兜底）",
            "meals": meals_demo,
        }]
    elif plan_duration == "7_days":
        days_arr = [{
            "day": d,
            "day_label": f"第{d}天（解析兜底）",
            "meals": meals_demo,
        } for d in range(1, 8)]
    else:
        days_arr = [{
            "day": d,
            "day_label": f"第{d}天（解析兜底）",
            "meals": meals_demo,
        } for d in range(1, 8)]

    return {
        "summary": "⚠️ 计划已生成，但 AI 返回的结构化 JSON 未能被成功解析。下方「建议」中展示了 AI 的原始回答，供您参考。您也可以点击「🔄 重新生成」再试一次。",
        "target_calories": "—",
        "macros": {"protein_g": "—", "fat_g": "—", "carbs_g": "—"},
        "days": days_arr,
        "advice": excerpt or "（AI 返回内容为空）",
        "precautions": "如果频繁出现此提示，建议将 plan_duration 先设为 1_day 示范（或检查 LLM 连接）",
        "source_guidelines": [],
        "duration_hint": plan_duration,
    }


def _parse_plan_json(raw_text: str, plan_duration: str = "1_day") -> Optional[Dict[str, Any]]:
    """
    从 LLM 响应中提取并解析 JSON 计划（超鲁棒版本）

    处理多种 LLM 输出风格：
    1. 纯 JSON
    2. ```json ... ``` / ``` ... ``` 包裹
    3. 前后附加解释文字
    4. 控制字符 / 尾逗号 / 字符串内未转义引号 等脏数据
    5. JSON 截断 → 补全缺失的闭合括号
    6. 全部失败 → 返回一个可渲染的兜底结构（_fallback_plan_structure），
       此时仍算「解析成功」以免前端直接显示失败文字
    """
    if raw_text is None:
        logger.error("_parse_plan_json: raw_text 为 None，走兜底结构")
        return _fallback_plan_structure("", plan_duration)

    text = raw_text.strip()
    if not text:
        logger.error("_parse_plan_json: 输入为空，走兜底结构")
        return _fallback_plan_structure("", plan_duration)

    # 1. 移除 markdown 代码块标记（支持 ```json / ``` / ````json 等多种写法）
    import re
    text = re.sub(r"^\s*```+\w*\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```+\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()

    def _try_parse(t: str, label: str):
        try:
            return json.loads(t)
        except json.JSONDecodeError as e:
            logger.info(f"_parse_plan_json[{label}]: 失败: {e}")
            return None

    # 2. 直接解析
    parsed = _try_parse(text, "step2-直接解析")
    if parsed is not None:
        logger.info(f"_parse_plan_json: 直接解析成功 ({len(text)}字符)")
        return parsed

    # 3. 提取第一个 { ... } 块（匹配最外层大括号）
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        logger.warning("_parse_plan_json: 找不到{}包裹，先尝试清理脏数据再找一次")
        cleaned = _fix_llm_json_dirty(text)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            logger.error(f"_parse_plan_json: 清理后仍无{{}}包裹, 走兜底结构。原文前300字符: {text[:300]}")
            return _fallback_plan_structure(raw_text, plan_duration)
        text = cleaned

    candidate = text[start:end + 1]

    # 4. 提取 {} 块后解析
    parsed = _try_parse(candidate, "step4-提取{}块")
    if parsed is not None:
        logger.info(f"_parse_plan_json: 提取{{}}块后解析成功 ({len(candidate)}字符)")
        return parsed

    # 5. 修复脏数据（控制字符 / 尾逗号 / 字符串内裸引号 / 字符串内换行）后解析
    logger.info("_parse_plan_json: 运行 _fix_llm_json_dirty 修复脏数据...")
    fixed = _fix_llm_json_dirty(candidate)
    parsed = _try_parse(fixed, "step5-脏数据修复后")
    if parsed is not None:
        logger.warning(f"_parse_plan_json: 脏数据修复后解析成功 ({len(fixed)}字符，建议检查 LLM 输出格式)")
        return parsed

    # 6. 兜底：补齐未闭合的 [ 和 { 后再尝试（输出截断场景）
    open_braces = fixed.count("{") - fixed.count("}")
    open_brackets = fixed.count("[") - fixed.count("]")
    if open_braces > 0 or open_brackets > 0:
        filled = fixed
        while open_brackets > 0:
            filled += "]"
            open_brackets -= 1
        while open_braces > 0:
            filled += "}"
            open_braces -= 1
        parsed = _try_parse(filled, "step6-补齐闭合")
        if parsed is not None:
            logger.warning(f"_parse_plan_json: 补齐闭合括号后解析成功，但内容可能不完整！{len(filled)}字符")
            return parsed

    # 7. 最终兜底：返回一个可渲染结构，不要前端看到失败
    logger.error(
        f"_parse_plan_json 全部解析策略失败 → 走 fallback 可渲染结构。\n"
        f"  原文总长度: {len(raw_text)} 字符\n"
        f"  前300字符: {raw_text[:300]}\n"
        f"  末300字符: ...{raw_text[-300:] if len(raw_text)>300 else raw_text}"
    )
    fallback = _fallback_plan_structure(raw_text, plan_duration)
    return fallback


def generate_plan(
    db: Session,
    user_id: str,
    goal_type: str = "weight_loss",
    start_date: date = None,
    end_date: date = None,
    profile_override: Dict[str, Any] = None,
    guideline_context_override: str = None,
    style_directive: str = None,
    plan_duration: PlanDuration = "1_day",
    guideline_titles: Optional[List[str]] = None,
    sources_override: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    生成个性化饮食计划（核心入口，分阶段生成避免超时）

    Args:
        guideline_titles: 从检索元数据中提取的真实指南标题列表，用于 LLM
                          填充 source_guidelines 字段（避免 LLM 抄模板占位符）
    """
    # 1. 读取用户档案（或使用覆盖）
    if profile_override:
        profile = profile_override
    else:
        profile = get_user_profile_dict(db, user_id)
        if not profile:
            return {"success": False, "error": f"未找到用户档案: {user_id}"}

    # 规范 plan_duration（容错）
    if plan_duration not in ("1_day", "7_days", "30_days"):
        logger.warning(f"未知 plan_duration={plan_duration}，回退到 1_day（安全）")
        plan_duration = "1_day"

    logger.info(
        f"开始为用户 {user_id} 生成饮食计划 (goal={goal_type}, duration={plan_duration})"
    )

    # 2. RAG 检索权威指南（使用覆盖或内部检索）
    if guideline_context_override is not None:
        guideline_context = guideline_context_override
    else:
        rag_query = _build_plan_query(profile)
        guideline_context = _retrieve_guidelines(rag_query)

    # 安全上限：指南上下文过长会导致 prompt 爆炸或模型输出提前截断
    # 从 2000 提到 3500 —— 7天/月度计划 prompt 预算足够
    MAX_GUIDELINE_CHARS = 3500
    if len(guideline_context) > MAX_GUIDELINE_CHARS:
        # 不是简单截前缀 → 按指南标题均匀保留，每个指南至少 400 chars
        import re as _re
        # 按 "N. 【来源:" 分段
        segments = _re.split(r'\n(?=\d+\.\s*【来源:)', guideline_context)
        if len(segments) >= 2:
            # 先保留分数最高的前 N 段，每段限制长度
            kept = []
            budget_per = 500
            total = 0
            for seg in segments[:10]:
                seg_short = seg[:budget_per] + ("..." if len(seg) > budget_per else "")
                if total + len(seg_short) > MAX_GUIDELINE_CHARS:
                    break
                kept.append(seg_short)
                total += len(seg_short)
            guideline_context = "\n\n".join(kept)
        else:
            guideline_context = guideline_context[:MAX_GUIDELINE_CHARS] + "\n...（内容已截断）"
        logger.warning(
            f"指南上下文过长 → 智能截断: 原{len(guideline_context)}字符 → 保留{len(guideline_context)}字符"
        )
    logger.info(f"最终指南上下文长度: {len(guideline_context)} 字符")

    # 3. 按时长构建差异化 prompt + 超时配置
    from src.core.llm_client import get_llm_client
    llm_client = get_llm_client()

    system_content_tpl, user_template = _build_plan_prompts(plan_duration)
    if style_directive:
        system_content_tpl = system_content_tpl + "\n\n" + style_directive

    messages = [
        {"role": "system", "content": system_content_tpl},
        {"role": "user", "content": user_template.format(
            user_profile=_format_user_profile(profile),
            guideline_context=guideline_context,
            guideline_titles_block=_format_guideline_titles_block(guideline_titles),
            guideline_titles_json=_format_guideline_titles_json(guideline_titles),
        )},
    ]

    # 按时长配 timeout + max_tokens
    # glm-4-flash 约 30-50 tokens/s，智谱服务端超时 ≈ 180s
    # 30_days 已改成 7天基准 + food_swap_pool，和 7_days 工作量相当，不需要更多 tokens
    # max_tokens 压到 4096 → 输出更短 → 速度更快 → 避免智谱服务端 180s 超时
    timeout_by_duration = {"1_day": 120, "7_days": 240, "30_days": 240}
    max_tokens_by_duration = {"1_day": 2048, "7_days": 4096, "30_days": 4096}
    timeout = timeout_by_duration[plan_duration]
    max_tokens = max_tokens_by_duration[plan_duration]

    import time as _time
    _t_llm = _time.perf_counter()
    _prompt_total_len = sum(len(m.get("content", "")) for m in messages)
    logger.info(
        f"计划生成({plan_duration}): 发起LLM调用 | prompt总长={_prompt_total_len}字符 | "
        f"max_tokens={max_tokens} | timeout={timeout}s"
    )

    # 加速：temperature 按配置（1天示范需要创意，7天/月度需要确定性）
    temperature_by_duration = {"1_day": 0.6, "7_days": 0.4, "30_days": 0.4}

    # 🆕 两次尝试策略：先正常 max_tokens，500/超时则减半重试
    raw_response = None
    for attempt_idx, (_mt, _to) in enumerate([
        (max_tokens, timeout),                          # 第1次：正常
        (max(max_tokens // 2, 1024), min(timeout, 180)), # 第2次：减半兜底
    ]):
        try:
            logger.info(f"计划生成({plan_duration}): LLM尝试 #{attempt_idx+1} | max_tokens={_mt} timeout={_to}s")
            raw_response = llm_client.chat(
                messages,
                temperature=temperature_by_duration[plan_duration],
                max_tokens=_mt,
                timeout=_to,
            )
            break  # 成功 → 跳出
        except Exception as _e:
            logger.warning(f"计划生成({plan_duration}): LLM尝试 #{attempt_idx+1} 失败: {_e}")
            if attempt_idx == 1:  # 两次都挂
                raise

    _llm_elapsed = (_time.perf_counter() - _t_llm) * 1000
    logger.info(
        f"计划生成({plan_duration}): LLM调用完成 | 耗时={_llm_elapsed:.0f}ms | "
        f"输出长度={len(raw_response) if raw_response else 0}字符"
    )

    if not raw_response:
        return {
            "success": False,
            "error": f"LLM 返回为空，无法生成计划（可能是超时，建议先生成1天示范计划再升级）",
            "plan_duration": plan_duration,
        }

    # 5. 解析 JSON
    plan_config = _parse_plan_json(raw_response, plan_duration=plan_duration)

    # 确保 config 中带 duration_hint（前端用来判断是否展示升级按钮）
    if "duration_hint" not in plan_config:
        plan_config["duration_hint"] = plan_duration

    # 🆕 把 sources（含 expert_media 视频、GI 数据等非指南来源）塞进 plan_config
    # 前端会渲染成"参考资料卡片"
    if sources_override:
        plan_config["sources"] = sources_override

    # 🆕 防偷懒兜底：7天/月度计划 → days 数组长度不够自动补到 7
    if plan_duration in ("7_days", "30_days"):
        _days = plan_config.get("days") or []
        if len(_days) < 7:
            _DAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            _logger = __import__("logging").getLogger(__name__)
            _logger.warning(f"LLM 偷懒只生成了 {len(_days)} 天 → 自动复制补全到 7 天")
            while len(_days) < 7:
                # 循环复制已有天
                _src = _days[len(_days) % max(len(_days), 1)] if _days else {}
                _copy = __import__("copy").deepcopy(_src)
                _copy["day"] = len(_days) + 1
                _copy["day_label"] = _DAY_LABELS[len(_days)]
                _days.append(_copy)
            plan_config["days"] = _days

    # 🆕 7天兜底：如果 7 天内容高度重复（LLM 偷懒）→ 按语义类别自动变异！
    if len(plan_config.get("days", [])) >= 7:
        import re as _re
        _days = plan_config["days"]
        _logger = __import__("logging").getLogger(__name__)

        # === 语义类别关键词映射（不用精确匹配！）===
        _CATEGORY_KEYWORDS = {
            "主食": ["米", "饭", "面", "馒头", "面包", "红薯", "玉米", "燕麦", "藜麦", "小米", "杂粮", "粥", "糙米", "地瓜"],
            "蛋白": ["鸡胸", "鸡", "蛋", "鱼", "虾", "肉", "牛肉", "豆腐", "豆", "鸭", "瘦", "里脊", "三文鱼", "鲈鱼"],
            "蔬菜": ["菜", "花", "瓜", "茄", "笋", "菇", "椒", "豆", "菠菜", "西兰花", "番茄", "南瓜", "胡萝卜", "油麦菜", "黄瓜", "茄子"],
            "汤品": ["汤", "羹", "粥"],
        }
        # === 每类的替代食材池（减脂友好）===
        _CATEGORY_ALTS = {
            "主食": ["糙米饭", "燕麦粥", "藜麦饭", "小米饭", "蒸红薯", "玉米棒", "杂粮馒头", "全麦面包片"],
            "蛋白": ["清蒸鲈鱼", "香煎三文鱼", "白灼虾仁", "卤牛肉片", "嫩豆腐", "煎鸡胸肉", "水煮蛋", "清炖鸭肉"],
            "蔬菜": ["凉拌菠菜", "清炒西兰花", "蒜蓉油麦菜", "清蒸南瓜", "番茄炒蛋", "清炒胡萝卜丝", "凉拌黄瓜", "蒜蓉茄子"],
            "汤品": ["紫菜蛋花汤", "番茄豆腐汤", "冬瓜虾皮汤", "菠菜蛋花汤"],
        }

        def _classify_food(food_name):
            """食材名 → 语义类别"""
            if not food_name:
                return None
            for cat, keywords in _CATEGORY_KEYWORDS.items():
                for kw in keywords:
                    if kw in food_name:
                        return cat
            return None  # 不在任何类别 → 当作蔬菜处理

        def _food_signature(day):
            """计算一天的食材签名（用于检测是否相同）"""
            sig = []
            for m in (day.get("meals") or []):
                for it in (m.get("items") or []):
                    if it and it.get("food"):
                        sig.append(it["food"])
            return tuple(sorted(sig))

        # 检测有多少天完全相同
        _sigs = [_food_signature(d) for d in _days]
        _unique_count = len(set(_sigs))
        _logger.info(f"🌱 7天计划去重检测: unique_days={_unique_count}/7")

        _changed_total = 0
        # 从 Day2 开始，每天替换 2-3 种食材（覆盖不同餐次）
        for _i, _day in enumerate(_days[1:], start=1):
            _meals = _day.get("meals", []) or []
            _day_changed = 0
            # 遍历所有餐次所有食材
            for _meal_idx, _meal in enumerate(_meals):
                if _day_changed >= 2:  # 每天至少改 2 项
                    break
                _items = _meal.get("items", []) or []
                for _item_idx, _item in enumerate(_items):
                    if _day_changed >= 2:
                        break
                    if not _item or not _item.get("food"):
                        continue
                    _orig = _item["food"]
                    _cat = _classify_food(_orig) or "蔬菜"
                    _pool = _CATEGORY_ALTS.get(_cat, _CATEGORY_ALTS["蔬菜"])
                    # 找一个不同的替代
                    for _attempt in range(len(_pool)):
                        _alt = _pool[(_attempt + _i * 2 + _meal_idx) % len(_pool)]
                        if _alt != _orig and _alt not in [
                            it.get("food") for m2 in _meals for it in (m2.get("items") or []) if it
                        ]:
                            _item["food"] = _alt
                            _day_changed += 1
                            _changed_total += 1
                            break

        if _changed_total > 0:
            _logger.info(f"🌱 7天计划去重变异完成：共替换 {_changed_total} 处食材，让每天不同")
            # 重算每天热量
            for _day in _days:
                _total = sum(
                    (m.get("total_calories") or sum((it.get("calories") or 0) for it in m.get("items", []) if it))
                    for m in _day.get("meals", []) if m
                )
                if _total > 0:
                    _day["_auto_total"] = _total
            # 再次检查 unique 数
            _new_sigs = [_food_signature(d) for d in _days]
            _logger.info(f"🌱 变异后 unique_days={len(set(_new_sigs))}/7")

    # 🆕 月度兜底：food_swap_pool 缺失则自动补默认换菜池
    if plan_duration == "30_days" and not plan_config.get("food_swap_pool"):
        _logger = __import__("logging").getLogger(__name__)
        _logger.warning("LLM 漏了 food_swap_pool → 自动补默认换菜池")
        plan_config["food_swap_pool"] = {
            "主食替代": ["糙米", "燕麦", "藜麦", "小米", "红薯", "玉米", "红豆", "全麦面包"],
            "优质蛋白替代": ["清蒸鲈鱼", "香煎三文鱼", "虾仁", "瘦牛肉", "豆腐", "鸡胸肉", "鸡蛋", "鸭肉"],
            "蔬菜替代": ["菠菜", "西兰花", "油麦菜", "胡萝卜", "番茄", "南瓜", "茄子", "黄瓜"],
            "水果替代": ["蓝莓", "苹果", "橙子", "草莓", "猕猴桃", "梨", "葡萄柚", "樱桃"],
            "健康脂肪替代": ["杏仁", "牛油果", "橄榄油", "花生", "核桃", "腰果", "奇亚籽", "亚麻籽"],
            "低卡饮品": ["无糖豆浆", "乌龙茶", "黑咖啡", "无糖气泡水", "绿茶", "茉莉花茶", "柠檬水", "菊花茶"],
        }

    # 6. 持久化到数据库（如果用户存在）
    # R08：解析失败的兜底结构不允许落库为 active——垃圾计划不能占用
    # 用户的 active 计划位（签到/升级等都挂在 active 计划上）
    if _is_fallback_plan(plan_config):
        logger.error(
            f"计划 JSON 解析失败（返回兜底结构），跳过落库: user_id={user_id}")
        return {
            "success": True,
            "plan_id": None,
            "config": plan_config,
            "goal_type": goal_type,
            "plan_duration": plan_duration,
            "start_date": str(start_date or date.today()),
            "end_date": str(end_date) if end_date else None,
            "parse_failed": True,
        }

    try:
        from src.models.db_models import User
        user_exists = db.query(User).filter(User.external_id == user_id).first()
        if user_exists:
            new_plan = Plan(
                user_id=user_id,
                goal_type=goal_type,
                config=plan_config,
                start_date=start_date or date.today(),
                end_date=end_date,
                status="active",
            )
            db.add(new_plan)
            db.commit()
            db.refresh(new_plan)
            logger.info(f"计划生成成功: plan_id={new_plan.id}, duration={plan_duration}")
            return {
                "success": True,
                "plan_id": new_plan.id,
                "config": plan_config,
                "goal_type": goal_type,
                "plan_duration": plan_duration,
                "start_date": str(new_plan.start_date),
                "end_date": str(new_plan.end_date) if new_plan.end_date else None,
            }
        else:
            logger.info(f"用户 {user_id} 不存在，跳过持久化，仅返回生成结果 (duration={plan_duration})")
            return {
                "success": True,
                "plan_id": None,
                "config": plan_config,
                "goal_type": goal_type,
                "plan_duration": plan_duration,
                "start_date": str(start_date or date.today()),
                "end_date": str(end_date) if end_date else None,
            }
    except Exception as e:
        logger.warning(f"计划持久化失败（返回生成结果）: {e}")
        return {
            "success": True,
            "plan_id": None,
            "config": plan_config,
            "goal_type": goal_type,
            "plan_duration": plan_duration,
            "start_date": str(start_date or date.today()),
            "end_date": str(end_date) if end_date else None,
        }


# ============================================================
# 3. 计划 CRUD
# ============================================================

def list_plans(db: Session, user_id: str, status: str = None) -> Dict[str, Any]:
    """列出用户的所有计划"""
    query = db.query(Plan).filter(Plan.user_id == user_id)
    if status:
        query = query.filter(Plan.status == status)
    plans = query.order_by(Plan.created_at.desc()).all()
    plan_list = [_plan_to_dict(p) for p in plans]

    return {
        "success": True,
        "count": len(plan_list),
        "plans": plan_list,   # 🆕 前端读 plans 字段
        "data": plan_list,    # 兼容字段
    }


def get_plan_detail(db: Session, user_id: str, plan_id: int) -> Dict[str, Any]:
    """获取单个计划详情"""
    plan = db.query(Plan).filter(
        Plan.id == plan_id,
        Plan.user_id == user_id,
    ).first()
    if not plan:
        return {"success": False, "error": f"未找到计划 {plan_id}"}
    return {"success": True, "data": _plan_to_dict(plan)}


def update_plan_config(
    db: Session,
    user_id: str,
    plan_id: int,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """更新计划的 config（用户在 UI 上修改后调用）"""
    plan = db.query(Plan).filter(
        Plan.id == plan_id,
        Plan.user_id == user_id,
    ).first()
    if not plan:
        return {"success": False, "error": f"未找到计划 {plan_id}"}

    # R01：换菜/编辑后强制校验——过敏原/疾病禁忌命中则拒绝保存，
    # 原 config 保持不变（前端展示的还是原菜，即"失败回退原菜"）
    is_safe, risk_messages, _foods = _validate_plan_config_foods(db, user_id, config)
    if not is_safe:
        return {
            "success": False,
            "error": "计划包含过敏/禁忌食物，已保留原计划",
            "risks": risk_messages,
        }

    # R01：换菜后重算每餐/每日热量汇总
    _recalc_plan_calories(config)

    plan.config = config
    db.commit()
    db.refresh(plan)

    logger.info(f"计划 {plan_id} 的 config 已更新")
    return {"success": True, "data": _plan_to_dict(plan)}


def delete_plan(db: Session, user_id: str, plan_id: int) -> Dict[str, Any]:
    """删除计划"""
    plan = db.query(Plan).filter(
        Plan.id == plan_id,
        Plan.user_id == user_id,
    ).first()
    if not plan:
        return {"success": False, "error": f"未找到计划 {plan_id}"}

    db.delete(plan)
    db.commit()
    logger.info(f"计划 {plan_id} 已删除")
    return {"success": True, "message": f"计划 {plan_id} 已删除"}


# ============================================================
# 4. 计划打卡
# ============================================================

def checkin_plan(
    db: Session,
    user_id: str,
    plan_id: int,
    checkin_date_val: date = None,
    actual_calories: float = None,
    notes: str = None,
) -> Dict[str, Any]:
    """
    计划打卡（记录当日执行情况）

    同一天同一计划只能打卡一次（数据库唯一约束保证）。
    """
    # 校验计划归属
    plan = db.query(Plan).filter(
        Plan.id == plan_id,
        Plan.user_id == user_id,
    ).first()
    if not plan:
        return {"success": False, "error": f"未找到计划 {plan_id}"}

    target_date = checkin_date_val or date.today()

    # 检查是否已打卡
    existing = db.query(PlanCheckIn).filter(
        PlanCheckIn.plan_id == plan_id,
        PlanCheckIn.checkin_date == target_date,
    ).first()
    if existing:
        return {
            "success": False,
            "error": f"计划 {plan_id} 在 {target_date} 已打卡",
            "existing_checkin_id": existing.id,
        }

    new_checkin = PlanCheckIn(
        plan_id=plan_id,
        checkin_date=target_date,
        status="completed",
        actual_calories=actual_calories,
        notes=notes,
    )
    db.add(new_checkin)
    db.commit()
    db.refresh(new_checkin)

    logger.info(f"计划 {plan_id} 打卡成功: checkin_id={new_checkin.id}")

    return {
        "success": True,
        "checkin_id": new_checkin.id,
        "plan_id": plan_id,
        "checkin_date": str(new_checkin.checkin_date),
        "actual_calories": _safe_float(new_checkin.actual_calories),
    }


def list_checkins(db: Session, user_id: str, plan_id: int) -> Dict[str, Any]:
    """列出某计划的所有打卡记录"""
    plan = db.query(Plan).filter(
        Plan.id == plan_id,
        Plan.user_id == user_id,
    ).first()
    if not plan:
        return {"success": False, "error": f"未找到计划 {plan_id}"}

    checkins = db.query(PlanCheckIn).filter(
        PlanCheckIn.plan_id == plan_id,
    ).order_by(PlanCheckIn.checkin_date.desc()).all()

    return {
        "success": True,
        "count": len(checkins),
        "data": [
            {
                "id": c.id,
                "checkin_date": str(c.checkin_date),
                "status": c.status,
                "actual_calories": _safe_float(c.actual_calories),
                "notes": c.notes,
            }
            for c in checkins
        ],
    }


# ============================================================
# 辅助函数
# ============================================================

def _plan_to_dict(plan: Plan) -> Dict[str, Any]:
    """将 Plan ORM 对象转为字典"""
    return {
        "id": plan.id,
        "user_id": plan.user_id,
        "goal_type": plan.goal_type,
        "config": plan.config,
        "start_date": str(plan.start_date) if plan.start_date else None,
        "end_date": str(plan.end_date) if plan.end_date else None,
        "status": plan.status,
        "created_at": str(plan.created_at) if plan.created_at else None,
    }


# ============================================================
# R01：计划写路径强制安全校验（换菜/编辑/手动保存）
# ============================================================
# 过敏史自由文本 → food_validator 过敏原键 的映射提示词
_ALLERGEN_TEXT_HINTS: Dict[str, tuple] = {
    "milk": ("牛奶", "乳糖", "奶制品", "乳制品", "羊奶", "奶油"),
    "eggs": ("鸡蛋", "蛋类", "蛋清", "蛋黄"),
    "peanut": ("花生",),
    "nuts": ("坚果", "核桃", "杏仁", "腰果", "榛子"),
    "seafood": ("海鲜", "虾", "蟹", "鱼", "贝类", "蛤", "蚝"),
    "gluten": ("麸质", "小麦", "面粉", "面筋"),
    "soy": ("大豆", "黄豆", "豆腐"),
}

# 健康标签/疾病文本 → food_validator 疾病键 的映射提示词
_DISEASE_TEXT_HINTS: Dict[str, tuple] = {
    "diabetes": ("糖尿病", "血糖高", "高血糖"),
    "hypertension": ("高血压",),
    "hyperlipidemia": ("高血脂", "血脂异常", "高胆固醇"),
    "fatty_liver": ("脂肪肝",),
    "gout": ("痛风", "高尿酸"),
}


def _extract_plan_foods(config: Dict[str, Any]) -> List[str]:
    """从 plan_config.days[].meals[].items[] 中提取全部食物名"""
    foods: List[str] = []
    for day in (config.get("days") or []):
        if not isinstance(day, dict):
            continue
        for meal in (day.get("meals") or []):
            if not isinstance(meal, dict):
                continue
            for item in (meal.get("items") or []):
                if isinstance(item, dict) and item.get("food"):
                    foods.append(str(item["food"]))
    return foods


def _profile_to_validator_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """把 get_user_profile_dict 的档案字典适配为 food_validator 需要的结构"""
    allergies_text = str(profile.get("allergies") or "")
    tags = profile.get("health_tags") or []
    tag_text = " ".join(str(t) for t in tags if t)

    allergens = [
        key for key, hints in _ALLERGEN_TEXT_HINTS.items()
        if any(h in allergies_text for h in hints)
    ]
    diseases = [
        key for key, hints in _DISEASE_TEXT_HINTS.items()
        if any(h in tag_text for h in hints)
    ]
    return {"allergens": allergens, "diseases": diseases, "medications": []}


def _validate_plan_config_foods(
    db: Session,
    user_id: str,
    config: Dict[str, Any],
) -> tuple:
    """
    对计划 config 里的全部食物做过敏原/疾病禁忌/药物相互作用校验（R01）

    Returns:
        (is_safe, risk_messages, foods)
        - is_safe: False 时调用方必须拒绝保存（原计划保持不变 = 回退原菜）
        - risk_messages: 人类可读的风险描述列表
    """
    from src.utils.food_validator import validate_food_for_user

    foods = _extract_plan_foods(config)
    if not foods:
        return True, [], foods

    profile = get_user_profile_dict(db, user_id)
    if not profile:
        # 无档案无法校验 → 放行（保持可用性），不阻塞正常保存
        logger.info(f"用户 {user_id} 无档案，跳过计划食物校验")
        return True, [], foods

    result = validate_food_for_user(foods, _profile_to_validator_profile(profile))
    is_safe = result.get("is_safe", True)
    risk_messages = [r.get("message", "") for r in result.get("all_risks", []) if r.get("message")]
    if not is_safe:
        logger.warning(
            f"计划食物校验未通过: user={user_id}, risks={risk_messages[:5]}")
    return is_safe, risk_messages, foods


def _recalc_plan_calories(config: Dict[str, Any]) -> None:
    """
    换菜/编辑后重算每餐与每日热量汇总（就地修改 config）

    前端聊天表格读取 meal.total_calories，计划详情读取 day.total_calories。
    仅在存在数值型 item.calories 时重算，避免覆盖 LLM 给出的可信汇总。
    """
    for day in (config.get("days") or []):
        if not isinstance(day, dict):
            continue
        day_total = 0.0
        day_has_value = False
        for meal in (day.get("meals") or []):
            if not isinstance(meal, dict):
                continue
            meal_total = 0.0
            meal_has_value = False
            for item in (meal.get("items") or []):
                if isinstance(item, dict) and isinstance(item.get("calories"), (int, float)):
                    meal_total += float(item["calories"])
                    meal_has_value = True
            if meal_has_value:
                meal["total_calories"] = round(meal_total, 1)
                day_total += meal_total
                day_has_value = True
        if day_has_value:
            day["total_calories"] = round(day_total, 1)


def _is_fallback_plan(plan_config: Dict[str, Any]) -> bool:
    """检测 plan_config 是否为解析失败的兜底结构（_fallback_plan_structure 产物，R08）"""
    days = plan_config.get("days") or []
    if not days:
        return True
    labels = [
        str(d.get("day_label") or "")
        for d in days
        if isinstance(d, dict)
    ]
    return bool(labels) and all("解析兜底" in lb for lb in labels)


def save_plan_config(
    db: Session,
    user_id: str,
    goal_type: str,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    将已有的 plan_config 直接入库（不走 LLM 重新生成）。

    用于 RAG 聊天里生成的计划因为某种原因未自动持久化时，
    前端拿到 config 后调此接口兜底保存。
    """
    try:
        # R01：手动保存同样强制安全校验（LLM 生成结果可能含用户过敏原）
        is_safe, risk_messages, _foods = _validate_plan_config_foods(db, user_id, config)
        if not is_safe:
            return {
                "success": False,
                "error": "计划包含过敏/禁忌食物，保存被拒绝",
                "risks": risk_messages,
            }

        _recalc_plan_calories(config)

        new_plan = Plan(
            user_id=user_id,
            goal_type=goal_type,
            config=config,
            start_date=date.today(),
            status="active",
        )
        db.add(new_plan)
        db.commit()
        db.refresh(new_plan)
        logger.info(f"save_plan_config: 手动保存成功, plan_id={new_plan.id}, user={user_id}")
        return {
            "success": True,
            "plan_id": new_plan.id,
        }
    except Exception as e:
        db.rollback()
        logger.error(f"save_plan_config 失败: {e}")
        return {"success": False, "error": str(e)}
