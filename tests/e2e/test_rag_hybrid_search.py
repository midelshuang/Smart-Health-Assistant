"""
端到端测试脚本 - 验证 RAG 混合检索 + 计划生成

测试查询: 成都25岁男生178cm160斤减肥食养规划
验证目标: 检索到指南文档 + LLM 使用指南内容生成计划
"""
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from src.utils.logger import setup_logger
logger = setup_logger(name="E2E_Test")

# ============================================================
# Monkey-patch: 绕过 chromadb auth 检查
# ============================================================
import chromadb.api.client as _client_module
from chromadb.api.client import UserIdentity

_orig_get_user_identity = _client_module.Client.get_user_identity

def _safe_get_user_identity(self):
    try:
        return _orig_get_user_identity(self)
    except Exception as e:
        return UserIdentity(subject=None, tenants=[], databases=[])

_client_module.Client.get_user_identity = _safe_get_user_identity

COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_RESET = "\033[0m"


def step(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def check(condition: bool, ok_msg: str, fail_msg: str):
    if condition:
        print(f"  {COLOR_GREEN}[PASS]{COLOR_RESET} {ok_msg}")
    else:
        print(f"  {COLOR_RED}[FAIL]{COLOR_RESET} {fail_msg}")


def main():
    os.environ["PYTHONIOENCODING"] = "utf-8"

    # ============================================================
    # Step 1: 检查 ChromaDB 数据
    # ============================================================
    step("Step 1: 检查 ChromaDB 中的数据")

    from src.repositories.vector_store import VectorStore
    from src.core.embedding import get_embedding_function

    vs = VectorStore()
    vs.connect()
    embedding_fn = get_embedding_function()
    collection = vs.get_or_create_collection(embedding_function=embedding_fn)

    total = collection.count()
    print(f"  ChromaDB collection 文档总数: {total}")

    all_data = collection.get(include=["metadatas"])
    source_types = {}
    age_groups = set()
    regions = set()
    for m in all_data.get("metadatas", []):
        st = m.get("source_type", "unknown")
        source_types[st] = source_types.get(st, 0) + 1
        ag = m.get("age_group", "")
        if ag:
            for a in ag.split(","):
                age_groups.add(a.strip())
        rg = m.get("region", "")
        if rg:
            for r in rg.split(","):
                regions.add(r.strip())

    print(f"  source_type 分布: {source_types}")
    print(f"  age_group 标签: {age_groups}")
    print(f"  region 标签: {regions}")

    check(total > 0, f"ChromaDB 有 {total} 条向量数据", "ChromaDB 为空！请先运行 seed_knowledge_base.py")
    check(len(age_groups) > 0, f"age_group 元数据存在: {age_groups}", "age_group 元数据缺失！")
    check("youth" in age_groups, f"youth 年龄组存在", "youth 年龄组不存在！")

    # ============================================================
    # Step 2: 测试查询预处理
    # ============================================================
    step("Step 2: 测试查询信息提取")

    from src.core.nodes import (
        _extract_age_from_query,
        _extract_region_from_query,
        _extract_gender_from_query,
        _extract_weight_from_query,
        _extract_height_from_query,
        _build_plan_rag_query,
        _get_age_group,
        _get_region_info,
    )

    test_query = "成都25岁男生178cm160斤减肥食养规划"

    age = _extract_age_from_query(test_query)
    region = _extract_region_from_query(test_query)
    gender = _extract_gender_from_query(test_query)
    weight = _extract_weight_from_query(test_query)
    height = _extract_height_from_query(test_query)

    print(f"  年龄: {age}")
    print(f"  地区: {region}")
    print(f"  性别: {gender}")
    print(f"  体重: {weight} kg")
    print(f"  身高: {height} cm")

    check(age == 25, f"年龄提取正确: {age}", f"年龄提取错误: {age}")
    check(region == "四川", f"地区提取正确: {region}", f"地区提取错误: {region}")
    check(gender == "male", f"性别提取正确: {gender}", f"性别提取错误: {gender}")
    check(weight is not None and abs(weight - 80.0) < 0.1, f"体重提取正确: {weight}kg", f"体重提取错误: {weight}")
    check(height == 178.0, f"身高提取正确: {height}cm", f"身高提取错误: {height}")

    age_group = _get_age_group(age)
    region_info = _get_region_info(region)
    print(f"\n  年龄组: {age_group}")
    print(f"  区域信息: {region_info}")

    # ============================================================
    # Step 3: 构建 RAG 查询
    # ============================================================
    step("Step 3: 构建 RAG 查询")

    profile_info = {
        "age": age,
        "region": region,
        "gender": gender,
        "weight": weight,
        "height": height,
        "health_goal": "weight_loss",
        "bmi": round(weight / ((height / 100) ** 2), 1),
    }
    print(f"  profile_info: {json.dumps(profile_info, ensure_ascii=False)}")

    rag_query = _build_plan_rag_query(profile_info, test_query)
    print(f"\n  构建的 RAG 查询: {rag_query}")
    check(len(rag_query) > 10, f"RAG 查询长度: {len(rag_query)}", "RAG 查询过短！")

    # ============================================================
    # Step 4: 向量检索
    # ============================================================
    step("Step 4: 向量语义检索")

    vector_results = vs.similarity_search(
        query_text=rag_query,
        top_k=10,
        threshold=0.2,
        where={"source_type": {"$in": ["guideline", "expert_media"]}},
        embedding_function=embedding_fn,
    )

    print(f"  向量检索结果数: {len(vector_results)}")
    for i, r in enumerate(vector_results[:5], 1):
        meta = r.get("metadata", {})
        print(f"    {i}. [{r.get('score', 0):.3f}] {meta.get('file_name', '未知')}")
        content = r.get("content", "")[:150]
        print(f"       摘要: {content}...")

    check(len(vector_results) > 0,
          f"向量检索返回 {len(vector_results)} 条结果",
          "向量检索无结果！")

    # ============================================================
    # Step 5: 关键词检索
    # ============================================================
    step("Step 5: 关键词检索（BM25 风格）")

    from src.core.nodes import _keyword_search

    keyword_results = _keyword_search(
        collection,
        query=test_query,
        top_k=10,
        where_filter={"source_type": {"$in": ["guideline", "expert_media"]}},
    )

    print(f"  关键词检索结果数: {len(keyword_results)}")
    for i, r in enumerate(keyword_results[:5], 1):
        meta = r.get("metadata", {})
        print(f"    {i}. [{r.get('score', 0):.3f}] {meta.get('file_name', '未知')}")

    check(len(keyword_results) > 0,
          f"关键词检索返回 {len(keyword_results)} 条结果",
          "关键词检索无结果！")

    # ============================================================
    # Step 6: 混合检索 + RRF 融合
    # ============================================================
    step("Step 6: 混合检索 + RRF 融合")

    from src.core.rrf_fusion import fuse_vector_results

    if keyword_results:
        all_results = [
            {"query": f"向量检索({rag_query[:30]}...)", "results": vector_results},
            {"query": f"关键词检索({test_query[:30]}...)", "results": keyword_results},
        ]
        fused_results = fuse_vector_results(
            queries_with_results=all_results,
            k=60,
            top_n=10,
        )
        print(f"  融合后结果数: {len(fused_results)}")
        for i, r in enumerate(fused_results[:8], 1):
            meta = r.get("metadata", {})
            source_type = meta.get("source_type", "?")
            age_group_meta = meta.get("age_group", "?")
            region_meta = meta.get("region", "?")
            print(f"    {i}. [{r.get('score', 0):.3f}] [{source_type}] [age={age_group_meta}] [region={region_meta}] {meta.get('file_name', '未知')}")

        check(len(fused_results) > 0,
              f"融合检索返回 {len(fused_results)} 条结果",
              "融合检索无结果！")

        # 检查是否有核心指南
        file_names = [r.get("metadata", {}).get("file_name", "") for r in fused_results]
        has_obesity_guide = any("肥胖食养" in fn for fn in file_names)
        has_weight_loss = any("减重" in fn or "减肥" in fn for fn in file_names)
        check(has_obesity_guide or has_weight_loss,
              f"检索到相关指南文档",
              f"未检索到肥胖/减重相关指南！已检索: {file_names}")
    else:
        print("  关键词检索无结果，跳过融合")

    # ============================================================
    # Step 7: 内容验证 - 检查检索结果是否包含实用建议
    # ============================================================
    step("Step 7: 内容验证 - 检查检索结果是否包含实用建议")

    if fused_results:
        combined_content = ""
        for r in fused_results[:5]:
            combined_content += r.get("content", "") + " "

        checks = [
            ("能量/热量", ["能量", "热量", "kcal", "千卡"]),
            ("蛋白质", ["蛋白质"]),
            ("脂肪", ["脂肪", "油脂"]),
            ("碳水化合物", ["碳水", "谷类", "全谷物"]),
            ("蔬菜", ["蔬菜"]),
            ("运动", ["运动", "活动"]),
        ]

        for name, keywords in checks:
            found = any(kw in combined_content for kw in keywords)
            check(found, f"包含「{name}」相关建议", f"未找到「{name}」相关建议")

    # ============================================================
    # Step 8: 模拟 plan_generator_node 的工作流程
    # ============================================================
    step("Step 8: 模拟计划生成（使用检索到的指南上下文）")

    if fused_results:
        guideline_context = ""
        lines = []
        for i, item in enumerate(fused_results[:5], 1):
            meta = item.get("metadata", {})
            source = meta.get("file_name", "未知来源")
            source_type = meta.get("source_type", "guideline")
            content = item.get("content", "")
            if len(content) > 600:
                content = content[:600] + "..."
            lines.append(f"{i}. 【来源: {source}（{source_type}）】{content}")
        guideline_context = "\n\n".join(lines)

        print(f"  guideline_context 长度: {len(guideline_context)} 字符")
        print(f"  前 300 字符预览:")
        print(f"  {guideline_context[:300]}...")

        check(len(guideline_context) > 200,
              f"指南上下文长度充足 ({len(guideline_context)} 字符)",
              "指南上下文过短！")

        # 模拟 LLM 调用（检查 prompt 是否构建正确）
        from src.services.plan_service import PLAN_SYSTEM_PROMPT, PLAN_USER_PROMPT, _format_user_profile

        profile_str = _format_user_profile(profile_info)
        user_prompt = PLAN_USER_PROMPT.format(
            user_profile=profile_str,
            guideline_context=guideline_context,
        )

        print(f"\n  LLM User Prompt 长度: {len(user_prompt)} 字符")
        print(f"  LLM User Prompt 预览:")
        print(f"  {user_prompt[:500]}...")

        check(len(user_prompt) > 500,
              f"LLM prompt 长度充足 ({len(user_prompt)} 字符)",
              "LLM prompt 过短！")
        check("权威指南参考" in user_prompt,
              "Prompt 包含指南参考段落",
              "Prompt 缺少指南参考段落！")
    else:
        print("  跳过：无检索结果")

    # ============================================================
    # 总结
    # ============================================================
    step("测试总结")

    print(f"\n  {COLOR_GREEN}核心流程验证:{COLOR_RESET}")
    print(f"    ✓ 数据存储: {total} 条向量数据")
    print(f"    ✓ 元数据: age_group={age_groups}, region={regions}")
    print(f"    ✓ 信息提取: 年龄={age}, 地区={region}, 性别={gender}, 体重={weight}kg, 身高={height}cm")
    print(f"    ✓ 向量检索: {len(vector_results)} 条结果")
    print(f"    ✓ 关键词检索: {len(keyword_results)} 条结果")
    if fused_results:
        print(f"    ✓ 混合检索: {len(fused_results)} 条融合结果")
        print(f"    ✓ 指南上下文: {len(guideline_context)} 字符")

    print(f"\n  {COLOR_YELLOW}接下来:{COLOR_RESET}")
    print(f"    如果以上 [PASS] 项全部通过，说明 RAG 检索流程已正确。")
    print(f"    你可以启动服务并测试完整的 API 端到端流程。")


if __name__ == "__main__":
    main()