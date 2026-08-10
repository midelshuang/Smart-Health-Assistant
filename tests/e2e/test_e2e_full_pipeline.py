"""
完整端到端测试 - 验证从用户查询到 LLM 生成饮食计划的全流程

测试查询: 成都25岁男生178cm160斤减肥食养规划
验证目标:
  1. 意图正确识别为 plan_generation
  2. info_collector 正确提取用户信息
  3. 向量+关键词混合检索返回指南文档
  4. plan_generator 调用 LLM 生成包含指南内容的计划
"""
import os
import sys
import json
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# ============================================================
# Monkey-patch: 绕过 chromadb auth 检查
# ============================================================
import chromadb.api.client as _client_module
from chromadb.api.client import UserIdentity

_orig_get_user_identity = _client_module.Client.get_user_identity

def _safe_get_user_identity(self):
    try:
        return _orig_get_user_identity(self)
    except Exception:
        return UserIdentity(subject=None, tenants=[], databases=[])

_client_module.Client.get_user_identity = _safe_get_user_identity

from src.utils.logger import setup_logger
logger = setup_logger(name="E2E_Full")

COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_CYAN = "\033[96m"
COLOR_RESET = "\033[0m"


def step(title: str):
    print(f"\n{'='*60}")
    print(f"  {COLOR_CYAN}{title}{COLOR_RESET}")
    print(f"{'='*60}")


def check(condition: bool, ok_msg: str, fail_msg: str):
    if condition:
        print(f"  {COLOR_GREEN}[PASS]{COLOR_RESET} {ok_msg}")
    else:
        print(f"  {COLOR_RED}[FAIL]{COLOR_RESET} {fail_msg}")
    return condition


def main():
    os.environ["PYTHONIOENCODING"] = "utf-8"

    # ============================================================
    # Step 1: 清除缓存，确保走完整 RAG 流程
    # ============================================================
    step("Step 1: 清除查询缓存")

    try:
        import redis
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        # 清除所有 query_cache 键
        keys = r.keys("query_cache:*")
        if keys:
            r.delete(*keys)
            print(f"  已清除 {len(keys)} 个查询缓存")
        else:
            print(f"  无查询缓存需要清除")
    except Exception as e:
        print(f"  Redis 缓存清除跳过: {e}")

    # ============================================================
    # Step 2: 调用完整 RAG 管道
    # ============================================================
    step("Step 2: 调用完整 RAG 管道")

    from src.services.rag_service import RAGPipeline

    pipeline = RAGPipeline()
    test_query = "成都25岁男生178cm160斤减肥食养规划"

    print(f"  测试查询: {test_query}")
    print(f"  正在执行 RAG 流程（可能需要 10-30 秒）...")

    start_time = time.time()
    result = pipeline.query(user_query=test_query, user_id="guest_user", stream=False)
    elapsed = time.time() - start_time

    print(f"  耗时: {elapsed:.1f}s")
    print(f"  intent: {result.get('intent', 'unknown')}")
    print(f"  success: {result.get('success', False)}")

    check(result.get("intent") == "plan_generation",
          f"意图识别正确: plan_generation",
          f"意图识别错误: {result.get('intent')}")

    if result.get("error"):
        print(f"  {COLOR_RED}错误: {result['error']}{COLOR_RESET}")

    # ============================================================
    # Step 3: 检查向量检索结果
    # ============================================================
    step("Step 3: 检查向量检索结果")

    vector_results = result.get("vector_results", [])
    print(f"  向量检索结果数: {len(vector_results)}")

    for i, r in enumerate(vector_results[:5], 1):
        meta = r.get("metadata", {})
        score = r.get("score", 0)
        file_name = meta.get("file_name", "未知")
        source_type = meta.get("source_type", "?")
        age_group = meta.get("age_group", "?")
        region = meta.get("region", "?")
        print(f"    {i}. [{score:.3f}] [{source_type}] [age={age_group}] [region={region}]")
        print(f"       来源: {file_name}")
        content = r.get("content", "")[:120]
        print(f"       摘要: {content}...")

    check(len(vector_results) > 0,
          f"向量检索返回 {len(vector_results)} 条结果",
          "向量检索无结果！")

    # 检查是否检索到关键指南
    file_names = [r.get("metadata", {}).get("file_name", "") for r in vector_results]
    has_obesity_guide = any("肥胖食养" in fn for fn in file_names)
    has_weight_loss = any("减重" in fn or "减肥" in fn for fn in file_names)
    has_general_guide = any("膳食指南" in fn for fn in file_names)

    check(has_obesity_guide or has_weight_loss,
          f"检索到肥胖/减重相关指南",
          f"未检索到肥胖/减重相关指南！已检索: {file_names}")

    # ============================================================
    # Step 4: 检查生成的计划内容
    # ============================================================
    step("Step 4: 检查生成的饮食计划")

    answer = result.get("answer", "")
    print(f"  生成答案长度: {len(answer)} 字符")

    if answer:
        print(f"\n  {COLOR_YELLOW}--- 生成的计划内容 ---{COLOR_RESET}")
        print(f"  {answer[:2000]}")
        if len(answer) > 2000:
            print(f"  ...（共 {len(answer)} 字符）")
        print(f"  {COLOR_YELLOW}--- 内容结束 ---{COLOR_RESET}\n")

    check(len(answer) > 100,
          f"生成答案长度充足 ({len(answer)} 字符)",
          "生成答案过短！")

    # ============================================================
    # Step 5: 验证计划是否使用了指南内容
    # ============================================================
    step("Step 5: 验证计划是否使用了指南内容")

    # 检查答案是否引用了指南
    guide_refs = []
    for fn in file_names:
        short_name = fn.split("（")[0] if "（" in fn else fn
        if short_name and short_name in answer:
            guide_refs.append(short_name)

    check(len(guide_refs) > 0,
          f"计划中引用了指南: {guide_refs}",
          "计划中未引用任何指南名称！")

    # 检查是否包含指南中的具体建议
    guide_content = ""
    for r in vector_results[:3]:
        guide_content += r.get("content", "") + " "

    guide_keywords = []
    checks = [
        ("能量/kcal控制", ["能量", "kcal", "千卡", "热量"]),
        ("蛋白质比例", ["蛋白质", "protein"]),
        ("脂肪比例", ["脂肪", "fat"]),
        ("碳水比例", ["碳水", "谷类", "全谷物"]),
        ("蔬菜摄入", ["蔬菜"]),
        ("全谷物", ["全谷物", "糙米", "燕麦", "杂粮"]),
        ("运动建议", ["运动", "活动", "锻炼"]),
    ]

    for name, keywords in checks:
        in_guide = any(kw in guide_content for kw in keywords)
        in_answer = any(kw in answer for kw in keywords)
        if in_guide and in_answer:
            check(True, f"「{name}」: 指南有建议 → 计划中体现", "")
        elif in_guide and not in_answer:
            check(False, "", f"「{name}」: 指南有建议但计划中未体现")

    # ============================================================
    # Step 6: 检查来源溯源
    # ============================================================
    step("Step 6: 检查来源溯源")

    sources = result.get("sources", [])
    answer_source = result.get("answer_source", "unknown")
    print(f"  answer_source: {answer_source}")
    print(f"  sources 数量: {len(sources)}")

    for i, s in enumerate(sources[:5], 1):
        print(f"    {i}. {s.get('file_name', '未知')} ({s.get('source_type', '?')})")

    check(answer_source == "knowledge_base",
          f"溯源标识正确: knowledge_base",
          f"溯源标识异常: {answer_source}")
    check(len(sources) > 0,
          f"来源数量: {len(sources)}",
          "无来源信息！")

    # ============================================================
    # 总结
    # ============================================================
    step("测试总结")

    all_pass = True
    print(f"\n  {COLOR_GREEN}端到端流程验证结果:{COLOR_RESET}")
    print(f"    意图识别: {result.get('intent', 'unknown')}")
    print(f"    向量检索: {len(vector_results)} 条结果")
    print(f"    生成答案: {len(answer)} 字符")
    print(f"    来源溯源: {answer_source} ({len(sources)} 个来源)")
    print(f"    耗时: {elapsed:.1f}s")

    if guide_refs:
        print(f"    引用指南: {guide_refs}")

    print(f"\n  {COLOR_YELLOW}提示:{COLOR_RESET}")
    print(f"    如果计划内容中包含指南的具体建议（如能量控制范围、")
    print(f"    蛋白质比例、全谷物推荐等），说明 RAG 流程已正确工作。")
    print(f"    如果计划内容仍然千篇一律，请检查 LLM prompt 是否被正确构造。")


if __name__ == "__main__":
    main()
