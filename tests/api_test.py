"""
API 接口手动测试脚本

用途：快速验证 HealthAI-Platform 各接口是否正常响应
运行：python tests/api_test.py [--base-url URL]

示例：
    python tests/api_test.py
    python tests/api_test.py --base-url http://localhost:8000
"""

import argparse
import json
import sys
import time

import requests


def test_health(base_url: str) -> dict:
    """健康检查"""
    t0 = time.time()
    try:
        r = requests.get(f"{base_url}/api/health", timeout=10)
        elapsed = (time.time() - t0) * 1000
        return {
            "接口": "GET /api/health",
            "状态": r.status_code,
            "耗时ms": round(elapsed, 1),
            "结果": "✅" if r.status_code == 200 else "❌",
            "详情": r.json().get("status", ""),
        }
    except requests.RequestException as e:
        return {"接口": "GET /api/health", "状态": "-", "耗时ms": "-", "结果": "❌", "详情": str(e)}


def test_rag_query(base_url: str, query: str = "糖尿病人的饮食原则") -> dict:
    """RAG 指南查询"""
    t0 = time.time()
    try:
        r = requests.post(
            f"{base_url}/api/v1/rag/query",
            json={"query": query, "stream": False},
            timeout=60,
        )
        elapsed = (time.time() - t0) * 1000
        data = r.json()
        return {
            "接口": "POST /api/v1/rag/query",
            "状态": r.status_code,
            "耗时ms": round(elapsed, 1),
            "结果": "✅" if r.status_code == 200 and data.get("success") else "❌",
            "意图": data.get("intent", ""),
            "来源数": len(data.get("sources", [])),
            "答案前100字": data.get("answer", "")[:100],
        }
    except requests.RequestException as e:
        return {"接口": "POST /api/v1/rag/query", "状态": "-", "耗时ms": "-", "结果": "❌", "详情": str(e)}


def test_food_search(base_url: str, query: str = "米饭的热量") -> dict:
    """食物 SQL 检索"""
    t0 = time.time()
    try:
        r = requests.post(
            f"{base_url}/api/v1/rag/query",
            json={"query": query, "stream": False},
            timeout=60,
        )
        elapsed = (time.time() - t0) * 1000
        data = r.json()
        return {
            "接口": "POST /api/v1/rag/query (食物)",
            "状态": r.status_code,
            "耗时ms": round(elapsed, 1),
            "结果": "✅" if r.status_code == 200 and data.get("success") else "❌",
            "意图": data.get("intent", ""),
            "答案前100字": data.get("answer", "")[:100],
        }
    except requests.RequestException as e:
        return {"接口": "POST /api/v1/rag/query", "状态": "-", "耗时ms": "-", "结果": "❌", "详情": str(e)}


def test_safety(base_url: str, query: str = "我想减肥，每天只吃一顿饭") -> dict:
    """安全护栏测试（低热量拦截）"""
    t0 = time.time()
    try:
        r = requests.post(
            f"{base_url}/api/v1/rag/query",
            json={"query": query, "stream": False},
            timeout=30,
        )
        elapsed = (time.time() - t0) * 1000
        data = r.json()
        return {
            "接口": "POST /api/v1/rag/query (安全)",
            "状态": r.status_code,
            "耗时ms": round(elapsed, 1),
            "结果": "✅" if r.status_code == 200 else "❌",
            "是否拦截": "🚧" if data.get("safety_blocked") else "放行",
            "答案前100字": data.get("answer", "")[:100],
        }
    except requests.RequestException as e:
        return {"接口": "POST /api/v1/rag/query", "状态": "-", "耗时ms": "-", "结果": "❌", "详情": str(e)}


def test_hybrid(base_url: str, query: str = "高血压患者早餐吃什么好") -> dict:
    """混合检索测试"""
    t0 = time.time()
    try:
        r = requests.post(
            f"{base_url}/api/v1/rag/query",
            json={"query": query, "stream": False},
            timeout=60,
        )
        elapsed = (time.time() - t0) * 1000
        data = r.json()
        return {
            "接口": "POST /api/v1/rag/query (混合)",
            "状态": r.status_code,
            "耗时ms": round(elapsed, 1),
            "结果": "✅" if r.status_code == 200 and data.get("success") else "❌",
            "意图": data.get("intent", ""),
            "来源数": len(data.get("sources", [])),
            "答案前100字": data.get("answer", "")[:100],
        }
    except requests.RequestException as e:
        return {"接口": "POST /api/v1/rag/query", "状态": "-", "耗时ms": "-", "结果": "❌", "详情": str(e)}


def main():
    parser = argparse.ArgumentParser(description="HealthAI-Platform API 接口测试")
    parser.add_argument("--base-url", default="http://localhost:8000", help="服务地址")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    print(f"测试目标: {base_url}")
    print("=" * 70)

    results = [
        test_health(base_url),
        test_rag_query(base_url, "糖尿病人的饮食原则"),
        test_food_search(base_url, "米饭的热量"),
        test_safety(base_url, "我想减肥，每天只吃一顿饭"),
        test_hybrid(base_url, "高血压患者早餐吃什么好"),
    ]

    for r in results:
        print(f"【{r.get('接口', '')}】")
        for k, v in r.items():
            if k != "接口":
                print(f"  {k}: {v}")
        print("-" * 70)

    passed = sum(1 for r in results if "✅" in str(r.get("结果", "")))
    total = len(results)
    print(f"\n通过率: {passed}/{total} ({passed * 100 // total}%)")

    if passed == total:
        print("🎉 全部测试通过！")
    else:
        print(f"⚠️  有 {total - passed} 个测试失败，请检查日志")
        sys.exit(1)


if __name__ == "__main__":
    main()