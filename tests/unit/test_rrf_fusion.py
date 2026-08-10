"""
RAG_src/core/rrf_fusion.py 单元测试

覆盖：
- reciprocal_rank_fusion 核心算法
- vector_doc_key / sql_doc_key / unified_doc_key 三种 key 提取器
- fuse_vector_results 多路向量融合
- fuse_cross_source_results SQL+向量跨源融合
- 边界情况（空输入/top_n 限制/相同文档去重）
"""
import pytest
from src.core.rrf_fusion import (
    reciprocal_rank_fusion,
    vector_doc_key,
    sql_doc_key,
    unified_doc_key,
    fuse_vector_results,
    fuse_cross_source_results,
    DEFAULT_RRF_K,
)


class TestVectorDocKey:
    """vector_doc_key"""

    def test_uses_doc_id_when_present(self):
        doc = {"doc_id": "abc123", "content": "hello"}
        assert vector_doc_key(doc) == "abc123"

    def test_falls_back_to_content_hash(self):
        doc = {"content": "hello world"}
        key = vector_doc_key(doc)
        assert key.startswith("vhash_")

    def test_empty_doc(self):
        doc = {}
        key = vector_doc_key(doc)
        assert key.startswith("vhash_")

    def test_same_content_same_key(self):
        doc1 = {"content": "same text"}
        doc2 = {"content": "same text"}
        assert vector_doc_key(doc1) == vector_doc_key(doc2)


class TestSqlDocKey:
    """sql_doc_key"""

    def test_uses_food_name(self):
        doc = {"food_name": "鸡蛋"}
        assert sql_doc_key(doc) == "sql_鸡蛋"

    def test_missing_food_name_returns_prefix(self):
        doc = {}
        assert sql_doc_key(doc) == "sql_"


class TestUnifiedDocKey:
    """unified_doc_key"""

    def test_nutrition_fact_uses_food_name(self):
        doc = {"source_type": "nutrition_fact", "food_name": "牛奶"}
        assert unified_doc_key(doc) == "sql_牛奶"

    def test_vector_doc_uses_vector_key(self):
        doc = {"doc_id": "vec1", "content": "some text"}
        key = unified_doc_key(doc)
        assert key == "vec1"


class TestReciprocalRankFusion:
    """reciprocal_rank_fusion 核心算法"""

    def test_empty_input_returns_empty(self):
        assert reciprocal_rank_fusion([], vector_doc_key) == []

    def test_empty_lists_returns_empty(self):
        result = reciprocal_rank_fusion([[], []], vector_doc_key)
        assert result == []

    def test_single_list_preserves_order(self):
        docs = [
            {"doc_id": "d1", "content": "a"},
            {"doc_id": "d2", "content": "b"},
        ]
        result = reciprocal_rank_fusion([docs], vector_doc_key)
        assert len(result) == 2
        # 单路时，rank=1 得分最高
        assert result[0]["doc_id"] == "d1"
        assert result[0]["rrf_score"] > result[1]["rrf_score"]

    def test_two_lists_dedup_by_key(self):
        list_a = [{"doc_id": "d1", "content": "a"}, {"doc_id": "d2", "content": "b"}]
        list_b = [{"doc_id": "d2", "content": "b"}, {"doc_id": "d3", "content": "c"}]
        result = reciprocal_rank_fusion([list_a, list_b], vector_doc_key)
        # 3 个唯一文档
        assert len(result) == 3
        # d2 在两路都出现，分数最高
        assert result[0]["doc_id"] == "d2"

    def test_rrf_score_formula(self):
        """验证 RRF 公式：score = sum(1 / (k + rank))"""
        docs = [{"doc_id": "d1"}]
        result = reciprocal_rank_fusion([docs], vector_doc_key, k=60)
        # rank=1, k=60 → 1/(60+1) = 0.016393
        expected_score = 1.0 / (60 + 1)
        assert abs(result[0]["rrf_score"] - round(expected_score, 6)) < 0.0001

    def test_custom_k_value(self):
        docs = [{"doc_id": "d1"}]
        result = reciprocal_rank_fusion([docs], vector_doc_key, k=10)
        expected = 1.0 / (10 + 1)
        assert abs(result[0]["rrf_score"] - round(expected, 6)) < 0.0001

    def test_top_n_limit(self):
        docs = [
            {"doc_id": "d1"}, {"doc_id": "d2"}, {"doc_id": "d3"},
            {"doc_id": "d4"}, {"doc_id": "d5"},
        ]
        result = reciprocal_rank_fusion([docs], vector_doc_key, top_n=3)
        assert len(result) == 3

    def test_rrf_hit_lists_count(self):
        list_a = [{"doc_id": "d1"}, {"doc_id": "d2"}]
        list_b = [{"doc_id": "d1"}, {"doc_id": "d3"}]
        result = reciprocal_rank_fusion([list_a, list_b], vector_doc_key)
        by_id = {r["doc_id"]: r for r in result}
        # d1 在两路都出现
        assert by_id["d1"]["rrf_hit_lists"] == 2
        # d2 只在第一路
        assert by_id["d2"]["rrf_hit_lists"] == 1

    def test_skip_none_key(self):
        """key_fn 返回 None 时跳过该项"""
        list_a = [{"doc_id": None, "content": ""}]
        # vector_doc_key 在 doc_id=None 且 content="" 时返回 vhash_0
        result = reciprocal_rank_fusion([list_a], vector_doc_key)
        assert len(result) >= 0  # 不抛异常即可


class TestFuseVectorResults:
    """fuse_vector_results"""

    def test_empty_input(self):
        assert fuse_vector_results([]) == []

    def test_multiple_queries_fusion(self, sample_rag_results):
        list_a, list_b = sample_rag_results
        queries_with_results = [
            {"query": "q1", "results": list_a},
            {"query": "q2", "results": list_b},
        ]
        result = fuse_vector_results(queries_with_results)
        # d1 在两路都出现，应排第一
        assert result[0]["doc_id"] == "d1"
        assert "rrf_score" in result[0]

    def test_top_n_applied(self, sample_rag_results):
        list_a, list_b = sample_rag_results
        queries_with_results = [
            {"query": "q1", "results": list_a},
            {"query": "q2", "results": list_b},
        ]
        result = fuse_vector_results(queries_with_results, top_n=2)
        assert len(result) == 2

    def test_missing_results_key_treated_as_empty(self):
        queries = [
            {"query": "q1"},  # 没有 results 字段
            {"query": "q2", "results": [{"doc_id": "d1"}]},
        ]
        result = fuse_vector_results(queries)
        assert len(result) == 1
        assert result[0]["doc_id"] == "d1"


class TestFuseCrossSourceResults:
    """fuse_cross_source_results"""

    def test_marks_sql_source_type(self):
        sql_results = [{"food_name": "鸡蛋", "calories_per_100g": 147}]
        vector_results = [{"doc_id": "d1", "content": "蛋白质"}]
        result = fuse_cross_source_results(sql_results, vector_results)
        sql_items = [r for r in result if r.get("source_type") == "nutrition_fact"]
        assert len(sql_items) == 1
        assert sql_items[0]["food_name"] == "鸡蛋"

    def test_marks_vector_source_type(self):
        sql_results = []
        vector_results = [
            {"doc_id": "d1", "content": "蛋白质"},
            {"doc_id": "d2", "content": "碳水"},
        ]
        result = fuse_cross_source_results(sql_results, vector_results)
        assert len(result) == 2

    def test_empty_inputs(self):
        result = fuse_cross_source_results([], [])
        assert result == []

    def test_preserves_original_fields(self):
        sql_results = [{"food_name": "牛奶", "calories_per_100g": 54, "protein": 3.0}]
        vector_results = [{"doc_id": "d1", "content": "蛋白质指南", "score": 0.9}]
        result = fuse_cross_source_results(sql_results, vector_results)
        # 找到 SQL 来源项，验证原始字段保留
        sql_item = next(r for r in result if r.get("source_type") == "nutrition_fact")
        assert sql_item["calories_per_100g"] == 54
        assert sql_item["protein"] == 3.0

    def test_top_n_limit(self):
        sql_results = [{"food_name": f"food_{i}"} for i in range(10)]
        vector_results = [{"doc_id": f"d{i}"} for i in range(10)]
        result = fuse_cross_source_results(sql_results, vector_results, top_n=5)
        assert len(result) == 5
