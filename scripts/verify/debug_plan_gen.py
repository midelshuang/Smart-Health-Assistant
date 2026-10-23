"""
调试计划生成失败问题
直接调用 LLM 测试不同 max_tokens 下的响应
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.config import settings
from src.core.llm_client import get_llm_client

def test_llm_basic():
    """测试基本 LLM 调用"""
    print("=" * 60)
    print("1. 测试基本 LLM 调用（max_tokens=512）")
    print("=" * 60)
    llm = get_llm_client()
    print(f"  配置: model={llm.model}, base_url={llm.base_url}")
    print(f"  api_key 已设置: {bool(llm.api_key)}")

    messages = [
        {"role": "system", "content": "你是一个测试助手。"},
        {"role": "user", "content": "请用一句话回答：1+1等于几？"},
    ]
    try:
        result = llm.chat(messages, temperature=0.1, max_tokens=512)
        print(f"  成功! 响应长度={len(result)}")
        print(f"  响应内容: {result[:200]}")
        return True
    except Exception as e:
        print(f"  失败! 错误: {e}")
        return False

def test_llm_large_max_tokens():
    """测试大 max_tokens（计划生成用 8192）"""
    print("\n" + "=" * 60)
    print("2. 测试大 max_tokens 调用（max_tokens=8192）")
    print("=" * 60)
    llm = get_llm_client()
    messages = [
        {"role": "system", "content": "你是一个JSON生成助手。只输出JSON，不要其他文字。"},
        {"role": "user", "content": '请输出一个简单的JSON，格式：{"name":"测试","items":[1,2,3]}'},
    ]
    try:
        import time
        t0 = time.perf_counter()
        result = llm.chat(messages, temperature=0.1, max_tokens=8192)
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"  成功! 耗时={elapsed:.0f}ms, 响应长度={len(result)}")
        print(f"  响应内容: {result[:300]}")
        return True
    except Exception as e:
        print(f"  失败! 错误: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_plan_json_generation():
    """测试实际生成简化版计划 JSON"""
    print("\n" + "=" * 60)
    print("3. 测试简化版计划 JSON 生成（max_tokens=4096）")
    print("=" * 60)
    llm = get_llm_client()
    system_prompt = (
        "你是一位专业的注册营养师。"
        "必须以 JSON 格式输出，不要输出任何其他文字。"
    )
    user_prompt = (
        "请生成一个简化版的减肥饮食计划（只需要1天，3餐）。\n"
        "输出 JSON 格式（严格如下结构，不要包含 ```json 标记）：\n"
        '{\n'
        '  "summary": "测试计划",\n'
        '  "target_calories": 1800,\n'
        '  "macros": {"protein_g": 90, "fat_g": 60, "carbs_g": 220},\n'
        '  "days": [\n'
        '    {\n'
        '      "day": 1,\n'
        '      "day_label": "周一",\n'
        '      "meals": [\n'
        '        {"meal_type": "breakfast", "items": [{"food": "全麦面包", "quantity": "2片", "calories": 200}], "total_calories": 200}\n'
        '      ]\n'
        '    }\n'
        '  ],\n'
        '  "advice": "建议",\n'
        '  "precautions": "注意事项"\n'
        '}\n'
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    for mt in [4096, 6144, 8192]:
        print(f"\n  尝试 max_tokens={mt}...")
        try:
            import time
            t0 = time.perf_counter()
            result = llm.chat(messages, temperature=0.7, max_tokens=mt)
            elapsed = (time.perf_counter() - t0) * 1000
            if result:
                print(f"  ✅ 成功! 耗时={elapsed:.0f}ms, 响应长度={len(result)}")
                print(f"  前200字符: {result[:200]}")
                import json
                try:
                    parsed = json.loads(result)
                    print(f"  ✅ JSON 解析成功! keys={list(parsed.keys())}")
                except Exception as e:
                    print(f"  ⚠️ JSON 解析失败: {e}")
                    # 尝试提取
                    start = result.find("{")
                    end = result.rfind("}")
                    if start != -1 and end != -1:
                        try:
                            parsed = json.loads(result[start:end+1])
                            print(f"  ✅ 提取后 JSON 解析成功! keys={list(parsed.keys())}")
                        except:
                            pass
                return True
            else:
                print(f"  ❌ 失败! 返回为空字符串")
        except Exception as e:
            print(f"  ❌ 异常! {e}")
    return False

def test_full_plan_generation(plan_duration="1_day", label=None):
    """测试完整的 generate_plan 流程（支持不同 plan_duration）"""
    label = label or plan_duration
    print("\n" + "=" * 60)
    print(f"4. 测试 plan_service.generate_plan（plan_duration={label}）")
    print("=" * 60)
    try:
        from src.services.plan_service import generate_plan
        from src.repositories.session import SessionLocal

        db = SessionLocal()
        # Mock profile（不需要用户存在于数据库，使用 profile_override）
        mock_profile = {
            "user_id": "test_debug_user",
            "nickname": "测试用户",
            "age": 30,
            "gender": "男",
            "height": 175.0,
            "weight": 80.0,
            "bmi": 26.1,
            "body_fat_percentage": 25.0,
            "health_goal": "weight_loss",
            "province": "北京",
            "allergies": "无",
            "sports_hobby": "跑步",
            "health_tags": ["超重"],
            "active_goal": None,
        }
        # 简短指南上下文，避免过长
        mock_guideline = (
            "【来源: 成人肥胖食养指南（2024年版）】\n"
            "1. 控制总能量摄入，每日减少300-500kcal\n"
            "2. 保证蛋白质摄入，每日1.2-1.6g/kg体重\n"
            "3. 减少精制碳水，增加全谷物和膳食纤维\n"
            "4. 早餐：全谷物+优质蛋白，如燕麦粥+鸡蛋\n"
            "5. 午餐：瘦肉+蔬菜+少量主食，如鸡胸肉沙拉+糙米饭\n"
            "6. 晚餐：清淡为主，蔬菜+豆制品，如豆腐蔬菜汤\n"
        )
        print(f"  plan_duration={plan_duration}，开始调用 generate_plan...")
        import time
        t0 = time.perf_counter()
        result = generate_plan(
            db=db,
            user_id="guest_user",  # 不保存到数据库
            goal_type="weight_loss",
            profile_override=mock_profile,
            guideline_context_override=mock_guideline,
            plan_duration=plan_duration,
        )
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"  耗时: {elapsed:.0f}ms ({elapsed/1000:.1f}s)")
        print(f"  返回 plan_duration 字段: {result.get('plan_duration')}")
        print(f"  结果 success: {result.get('success')}")
        if result.get("success"):
            print(f"  ✅ 计划生成成功! plan_id={result.get('plan_id')}")
            config = result.get("config", {})
            print(f"  summary: {str(config.get('summary', ''))[:100]}")
            days = config.get("days", [])
            meals = config.get("meals", [])
            if days:
                print(f"  days 数组长度: {len(days)}")
                for i, d in enumerate(days[:3]):
                    m_cnt = len(d.get("meals", []))
                    cal = sum(m.get("total_calories", 0) for m in d.get("meals", []))
                    d_label = d.get("day_label") or f"第{d.get('day')}天"
                    print(f"    - {d_label}: 餐次={m_cnt}, 热量≈{cal} kcal")
                if len(days) > 3:
                    print(f"    ...（共{len(days)}天，其余略）")
            elif meals:
                print(f"  meals 数组长度（单日格式兼容）: {len(meals)}")
            else:
                print(f"  ⚠️  config 中无 days/meals 字段，完整 keys={list(config.keys())}")
            print(f"  duration_hint (config内): {config.get('duration_hint')}")
        else:
            print(f"  ❌ 失败! 错误: {result.get('error')}")
            if "raw_response" in result:
                raw = str(result.get("raw_response", ""))
                print(f"  raw_response (前500): {raw[:500]}")
        db.close()
        return result.get("success", False)
    except Exception as e:
        print(f"  ❌ 异常! {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  计划生成调试工具（分阶段模式：1_day → 7_days → 30_days）")
    print("=" * 60)
    print(f"\n当前 settings.LLM_MAX_TOKENS 默认值: {settings.LLM_MAX_TOKENS}")
    print(f"plan_service 默认超时 1_day=180s / 7_days=300s / 30_days=300s")

    results = {}
    results["basic_llm"] = test_llm_basic()
    results["large_max_tokens"] = test_llm_large_max_tokens()
    results["plan_json"] = test_plan_json_generation()

    # 先跑 1 天（快，20-40 秒一般能出，用来快速验证）
    results["full_plan_1_day"] = test_full_plan_generation("1_day", label="1_day (快速示范)")

    # 可选：7 天（一般需 60-150 秒；如果赶时间可以注释掉）
    # results["full_plan_7_days"] = test_full_plan_generation("7_days", label="7_days (完整周计划)")

    # 可选：30 天（300 秒上限，仅做烟雾测试时再开）
    # results["full_plan_30_days"] = test_full_plan_generation("30_days", label="30_days (月度框架)")

    print("\n" + "=" * 60)
    print("  总结")
    print("=" * 60)
    for name, ok in results.items():
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {name}: {status}")

    print("\n💡 使用建议:")
    print("  1) 首次只跑 1_day 确认流程 & LLM 返回格式 OK")
    print("  2) 取消注释 full_plan_7_days 再跑一次完整周计划")
    print("  3) 前端交互: 用户输入\"帮我制定减肥计划\" → 默认 1_day, 展示后点按钮升级 7d/30d")
