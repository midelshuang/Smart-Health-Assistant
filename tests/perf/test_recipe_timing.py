"""
测试食谱做法和烹饪步骤功能
"""
import httpx
import time
import json

BASE_URL = "http://localhost:8000"
QUERY_URL = f"{BASE_URL}/api/v1/rag/query"

def test_recipe_query():
    """测试1：指南问答 - 食谱做法"""
    print("=" * 70)
    print("测试1：指南问答 - 查询食谱做法")
    print("=" * 70)

    payload = {
        "query": "肥胖患者食谱做法，给我一个食养方",
        "conversation_context": [],
    }

    print(f"\n请求: {payload['query']}")
    print("等待响应中...\n")

    t0 = time.perf_counter()
    try:
        r = httpx.post(
            QUERY_URL,
            json=payload,
            timeout=120,
        )
        elapsed = time.perf_counter() - t0
        print(f"HTTP {r.status_code} | 耗时: {elapsed:.1f}s\n")

        data = r.json()
        answer = data.get("answer", "")
        sources = data.get("sources", [])
        intent = data.get("intent", "")

        print(f"意图: {intent}")
        print(f"来源数: {len(sources)}")
        print(f"\n--- 回答 ---\n{answer}")

        if sources:
            print(f"\n--- 来源 ---")
            for i, s in enumerate(sources, 1):
                print(f"  {i}. {s.get('file_name', '?')} | {s.get('source_label', '?')} | score={s.get('score', '?')}")

        # 检查关键内容
        print(f"\n--- 检查 ---")
        has_recipe = any(kw in answer for kw in ["食材", "做法", "烹饪", "步骤"])
        has_source = any(kw in answer for kw in ["来源", "指南", "参考"])
        print(f"包含食谱/做法: {has_recipe}")
        print(f"包含来源引用: {has_source}")

    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"请求失败 ({elapsed:.1f}s): {e}")


def test_plan_generation():
    """测试2：计划生成 - 含烹饪步骤"""
    print("\n" + "=" * 70)
    print("测试2：计划生成 - 含烹饪步骤和食养方")
    print("=" * 70)

    payload = {
        "query": "我是30岁女性，身高165cm，体重70kg，BMI 25.7，想减脂，帮我制定饮食计划",
        "conversation_context": [],
    }

    print(f"\n请求: {payload['query']}")
    print("等待响应中...\n")

    t0 = time.perf_counter()
    try:
        r = httpx.post(
            QUERY_URL,
            json=payload,
            timeout=120,
        )
        elapsed = time.perf_counter() - t0
        print(f"HTTP {r.status_code} | 耗时: {elapsed:.1f}s\n")

        data = r.json()
        answer = data.get("answer", "")
        sources = data.get("sources", [])
        intent = data.get("intent", "")

        print(f"意图: {intent}")
        print(f"来源数: {len(sources)}")
        print(f"\n--- 回答 ---\n{answer}")

        if sources:
            print(f"\n--- 来源 ---")
            for i, s in enumerate(sources, 1):
                print(f"  {i}. {s.get('file_name', '?')} | {s.get('source_label', '?')}")

        # 检查关键内容
        print(f"\n--- 检查 ---")
        has_cooking = "👨‍🍳" in answer or "cooking" in answer.lower()
        has_steps = "📝" in answer or "做法步骤" in answer or "recipe_steps" in answer.lower()
        has_source = "📖" in answer or "参考指南" in answer
        print(f"包含烹饪方法(👨‍🍳): {has_cooking}")
        print(f"包含做法步骤(📝): {has_steps}")
        print(f"包含参考指南(📖): {has_source}")

    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"请求失败 ({elapsed:.1f}s): {e}")


if __name__ == "__main__":
    test_recipe_query()
    test_plan_generation()
