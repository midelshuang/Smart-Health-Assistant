#!/usr/bin/env python3
"""诊断RAG全流程：知识库内容 → 向量检索 → 计划生成"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json

def diagnose():
    print("=" * 60)
    print("【步骤1】诊断知识库内容")
    print("=" * 60)

    from src.repositories.vector_store import VectorStore
    from src.core.embedding import get_embedding_function

    try:
        vs = VectorStore()
        vs.connect()
        embedding_fn = get_embedding_function()
        collection = vs.get_or_create_collection(embedding_function=embedding_fn)
    except Exception as e:
        print(f"❌ 连接向量库失败: {e}")
        return

    count = collection.count()
    print(f"authoritative_sources 文档数: {count}")

    if count == 0:
        print("❌ 文档库为空！需要重新入库")
        return

    all_data = collection.get(include=["metadatas", "documents"])
    if all_data and all_data["metadatas"]:
        for i, meta in enumerate(all_data["metadatas"]):
            doc = all_data["documents"][i] if all_data["documents"] else ""
            doc_id = all_data["ids"][i] if all_data.get("ids") else ""
            print(f"\n  文档{i+1} (id={doc_id}):")
            print(f"    metadata: {json.dumps(meta, ensure_ascii=False, indent=6)}")
            print(f"    content前200字: {doc[:200] if doc else '(空)'}...")

    print("\n" + "=" * 60)
    print("【步骤2】模拟用户查询的向量检索")
    print("=" * 60)

    test_query = "深圳女生26岁155cm70kg减肥计划制定"
    print(f"\n查询: {test_query}")

    results = vs.similarity_search(
        query_text=test_query,
        top_k=10,
        embedding_function=embedding_fn,
    )
    print(f"\n检索结果数: {len(results)}")
    for i, r in enumerate(results):
        print(f"\n  结果{i+1}: score={r.get('score', 'N/A')}")
        meta = r.get("metadata", {})
        print(f"    metadata keys: {list(meta.keys())}")
        print(f"    metadata: {json.dumps(meta, ensure_ascii=False, indent=6)}")
        content = r.get("content", "")
        print(f"    content前300字: {content[:300]}...")

    # 用更精准的查询再试一次
    print("\n" + "-" * 40)
    print("用精准查询'肥胖症饮食治疗指南':")
    results2 = vs.similarity_search(
        query_text="肥胖症饮食治疗指南 成人",
        top_k=5,
        embedding_function=embedding_fn,
    )
    print(f"检索结果数: {len(results2)}")
    for i, r in enumerate(results2):
        print(f"  结果{i+1}: score={r.get('score', 'N/A')}")
        meta = r.get("metadata", {})
        print(f"    file_name: {meta.get('file_name', 'N/A')}")
        print(f"    source_type: {meta.get('source_type', 'N/A')}")
        print(f"    age_group: {meta.get('age_group', 'N/A')}")
        print(f"    region: {meta.get('region', 'N/A')}")
        print(f"    content前200字: {r.get('content', '')[:200]}...")

    print("\n" + "=" * 60)
    print("【步骤3】诊断plan_service的检索逻辑")
    print("=" * 60)

    try:
        from src.repositories.session import SessionLocal
        from src.services.plan_service import _build_plan_query, _retrieve_guidelines

        db = SessionLocal()

        profile = {
            "age": 26,
            "gender": "female",
            "weight": 70,
            "height": 155,
            "region": "深圳",
            "health_goal": "weight_loss",
            "bmi": 29.2,
        }

        print(f"\n用户档案: {json.dumps(profile, ensure_ascii=False, indent=2)}")

        plan_query = _build_plan_query(profile)
        print(f"\n构建的RAG查询: {plan_query}")

        guidelines = _retrieve_guidelines(plan_query)
        print(f"\n检索到的指南内容长度: {len(guidelines)} 字符")
        if guidelines:
            print(f"指南内容前800字: {guidelines[:800]}...")
        else:
            print("❌ 未检索到任何指南内容！")

        db.close()

    except Exception as e:
        print(f"诊断plan_service出错: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("【步骤4】诊断元数据过滤逻辑")
    print("=" * 60)

    try:
        from src.core.nodes import _build_plan_rag_query, _build_metadata_filter, _get_age_group, _get_region_info

        profile_info = {
            "age": 26,
            "gender": "female",
            "region": "深圳",
            "height": 155,
            "weight": 70,
            "health_goal": "weight_loss",
        }

        rag_query = _build_plan_rag_query(profile_info)
        print(f"\n构建的plan RAG查询: {rag_query}")

        filter_meta = _build_metadata_filter(profile_info)
        print(f"构建的元数据过滤: {json.dumps(filter_meta, ensure_ascii=False, indent=2)}")

        age_group = _get_age_group(26)
        print(f"\n26岁年龄组: {json.dumps(age_group, ensure_ascii=False, indent=2)}")

        region_info = _get_region_info("深圳")
        print(f"深圳地区映射: {json.dumps(region_info, ensure_ascii=False, indent=2)}")

        # 用元数据过滤试检索
        if filter_meta:
            print(f"\n使用元数据过滤检索:")
            results_filtered = vs.similarity_search(
                query_text=rag_query,
                top_k=5,
                where=filter_meta,
                embedding_function=embedding_fn,
            )
            print(f"过滤后结果数: {len(results_filtered)}")
            for i, r in enumerate(results_filtered):
                meta = r.get("metadata", {})
                print(f"  结果{i+1}: score={r.get('score', 'N/A')}, file={meta.get('file_name', 'N/A')}")

    except Exception as e:
        print(f"元数据过滤诊断出错: {e}")
        import traceback
        traceback.print_exc()

    print("\n✅ 诊断完成")

if __name__ == "__main__":
    diagnose()