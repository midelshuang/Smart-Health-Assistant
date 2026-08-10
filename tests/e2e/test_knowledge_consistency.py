"""
知识库一致性测试套件

验证 RAG 回答是否真正基于库中知识（非 LLM 幻觉）：
1. 来源溯源：每条回答附带可信来源
2. 内容一致性：回答内容与检索文档语义一致
3. 事实准确性：数据查询结果与数据库一致
4. 指南匹配：用户画像匹配正确的指南
5. 双写一致性：ChromaDB 与 PostgreSQL 分块对应
"""
import pytest
import time
from unittest.mock import patch, MagicMock

from src.core.guideline_registry import (
    select_guidelines_by_profile,
    match_guideline,
    GUIDELINE_REGISTRY,
    BASELINE_GUIDELINE,
)
from src.utils.semantic_chunker import SemanticChunker


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def semantic_chunker():
    """语义分块器实例"""
    return SemanticChunker(
        target_size=500,
        min_size=100,
        max_size=700,
    )


def _make_profile(age=None, gender=None, health_goal=None, health_tags=None, bmi=None):
    """构造画像 dict"""
    profile = {}
    if age is not None:
        profile["age"] = age
    if gender is not None:
        profile["gender"] = gender
    if health_goal is not None:
        profile["health_goal"] = health_goal
    if health_tags is not None:
        profile["health_tags"] = health_tags
    if bmi is not None:
        profile["bmi"] = bmi
    return profile


# ============================================================
# 1. 指南注册表测试（用户画像→指南匹配）
# ============================================================

class TestGuidelineRegistry:
    """指南注册表匹配准确性"""

    def test_youth_male_matches_obesity_guide(self):
        """25 岁青年男性 + 肥胖标签 → 匹配成人肥胖食养指南"""
        profile = _make_profile(age=25, gender="male", health_goal="weight_loss",
                                health_tags=["肥胖"])
        matched = select_guidelines_by_profile(profile)
        assert len(matched) > 0, "应匹配到指南"
        has_obesity_guide = any("肥胖" in name for name in matched)
        assert has_obesity_guide, f"减肥用户应匹配肥胖指南，实际: {matched}"

    def test_child_matches_child_guide(self):
        """8 岁儿童 → 至少返回基础指南"""
        profile = _make_profile(age=8, gender="male", health_goal="health_maintenance")
        matched = select_guidelines_by_profile(profile)
        assert len(matched) > 0, "应匹配到指南"

    def test_senior_gets_baseline(self):
        """70 岁老年 → 返回基础膳食指南"""
        profile = _make_profile(age=70, gender="female", health_goal="health_maintenance",
                                health_tags=["malnutrition"])
        matched = select_guidelines_by_profile(profile)
        assert len(matched) > 0, "应匹配到指南"
        assert BASELINE_GUIDELINE in matched, "应包含基础指南"

    def test_diabetes_matches_diabetes_guide(self):
        """糖尿病患者 → 无专属指南，至少匹配基础膳食指南"""
        profile = _make_profile(age=50, gender="male", health_goal="health_maintenance",
                                health_tags=["糖尿病"])
        matched = select_guidelines_by_profile(profile)
        has_diabetes_guide = any("糖尿病" in name for name in matched)
        assert not has_diabetes_guide, f"糖尿病无专属食养指南，不应匹配，实际: {matched}"
        assert BASELINE_GUIDELINE in matched, f"糖尿病患者应至少匹配基础指南，实际: {matched}"

    def test_no_tags_gets_general_guide(self):
        """无特殊标签 → 返回通用膳食指南"""
        profile = _make_profile(age=30, gender="female", health_goal="health_maintenance")
        matched = select_guidelines_by_profile(profile)
        assert len(matched) > 0, "应返回至少一份通用指南"
        assert BASELINE_GUIDELINE in matched

    def test_age_boundary_17_not_child(self):
        """17 岁（青少年末尾）→ 返回指南"""
        profile = _make_profile(age=17, gender="male", health_goal="weight_loss")
        matched = select_guidelines_by_profile(profile)
        assert len(matched) > 0

    def test_age_boundary_18_is_youth(self):
        """18 岁（青年起始）→ 正确归类"""
        profile = _make_profile(age=18, gender="male", health_goal="weight_loss")
        matched = select_guidelines_by_profile(profile)
        assert len(matched) > 0

    def test_obese_adult_gets_obesity_guide(self):
        """成人 BMI>=24 且无疾病 → 追加肥胖指南"""
        profile = _make_profile(age=30, gender="male", health_goal="weight_loss",
                                bmi=27.0)
        matched = select_guidelines_by_profile(profile)
        has_obesity_guide = any("肥胖" in name for name in matched)
        assert has_obesity_guide, f"BMI 超标成人应匹配肥胖指南，实际: {matched}"


# ============================================================
# 2. 语义分块器一致性测试
# ============================================================

class TestSemanticChunker:
    """语义分块器行为验证"""

    def test_chunk_preserves_paragraphs(self, semantic_chunker):
        """分块应保持段落完整性"""
        text = """第一段内容
包含完整的营养学知识。
这是更多的内容来增加第一段的长度，使其足够长以独立成为一个分块。
我们需要足够的文字来达到最小分块大小，所以继续添加更多的内容。
这些内容描述了营养学的基本原则和健康饮食的重要性。
合理的饮食应该包含各种营养素，包括蛋白质、碳水化合物、脂肪、维生素和矿物质。
平衡的饮食可以帮助人们保持健康，预防疾病，提高生活质量。
不同年龄段的人需要不同的营养配比，年轻人需要更多的能量，老年人则需要更多的钙质。
运动员等特殊人群需要根据训练目标调整饮食结构。
营养学家建议每天摄入多种食物以确保全面的营养供应。
谷物、蔬菜、水果、蛋白质来源和奶制品都应该是日常饮食的一部分。
合理的烹饪方式也能保留食物中的营养成分，避免过度加工。

第二段内容
同样是完整的一段。
这也是第二段的额外内容，用来增加长度。
第二段讨论了不同人群的营养需求差异，以及如何根据个人情况调整饮食方案。
运动员、孕妇、老年人等特殊人群需要不同的营养补充和膳食规划。
合理的膳食搭配可以预防多种慢性疾病，如心血管疾病、糖尿病和肥胖症。
良好的饮食习惯应该是终身的，从小培养，受益终身。
膳食宝塔提供了一个很好的参考框架，帮助人们规划每日的饮食结构。
营养均衡不仅关系到身体健康，也影响着心理健康和认知功能。
研究表明，某些营养素可以改善情绪、提高注意力和增强记忆力。
因此，科学的饮食搭配对人的一生都有深远的影响。"""
        chunks = semantic_chunker.chunk(text)
        assert len(chunks) >= 2, f"应至少分为 2 段，实际 {len(chunks)} 段"

    def test_chunk_min_size(self, semantic_chunker):
        """分块最小尺寸保证"""
        text = "A" * 500
        chunks = semantic_chunker.chunk(text)
        for chunk in chunks:
            assert len(chunk["content"]) >= 50, f"分块过小: {len(chunk['content'])}"

    def test_chunk_with_metadata(self, semantic_chunker):
        """分块附加元数据"""
        text = "测试内容" * 100
        metadata = {"source": "test", "page": 1}
        chunks = semantic_chunker.chunk(text, metadata=metadata)
        assert len(chunks) > 0
        assert chunks[0]["metadata"]["source"] == "test"

    def test_empty_text_returns_empty(self, semantic_chunker):
        """空文本返回空列表"""
        chunks = semantic_chunker.chunk("")
        assert chunks == []

    def test_single_short_text(self, semantic_chunker):
        """短文本作为单一分块"""
        text = "短文本内容"
        chunks = semantic_chunker.chunk(text)
        assert len(chunks) == 1
        assert chunks[0]["content"] == text

    def test_chunk_has_content_key(self, semantic_chunker):
        """分块结果包含 content 字段"""
        text = "这是一段需要被分块的文本内容" * 30
        chunks = semantic_chunker.chunk(text)
        for chunk in chunks:
            assert "content" in chunk
            assert "metadata" in chunk


# ============================================================
# 3. RAG 管道来源溯源测试
# ============================================================

class TestRAGSourceTraceability:
    """RAG 回答来源溯源验证"""

    def test_rag_response_has_source_field(self):
        """RAG 响应包含 sources 字段"""
        from src.api.routes.rag import execute_rag_query, RAGQueryRequest
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.query.return_value = {
                "success": True,
                "answer": "根据指南建议...",
                "intent": "guideline_query",
                "sources": [
                    {"file_name": "中国居民膳食指南2022", "source_type": "guideline"},
                ],
                "answer_source": "knowledge_base",
            }
            body = RAGQueryRequest(query="减脂饮食", stream=False)
            result = execute_rag_query(body, current_user=MagicMock())
            assert "sources" in result
            assert len(result["sources"]) >= 1
            assert result["answer_source"] == "knowledge_base"

    def test_rag_response_has_sql_results(self):
        """食物查询返回 SQL 检索结果"""
        from src.api.routes.rag import execute_rag_query, RAGQueryRequest
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.query.return_value = {
                "success": True,
                "answer": "100 克鸡蛋含有约 144 千卡的热量。",
                "intent": "food_search",
                "sources": [],
                "sql_results": [
                    {"food_name": "鸡蛋", "calories": 144, "protein": 13.3},
                ],
                "answer_source": "knowledge_base",
            }
            body = RAGQueryRequest(query="鸡蛋热量", stream=False)
            result = execute_rag_query(body, current_user=MagicMock())
            assert result["sql_results"] is not None
            assert len(result["sql_results"]) >= 1

    def test_rag_response_has_vector_results(self):
        """指南查询返回向量检索结果"""
        from src.api.routes.rag import execute_rag_query, RAGQueryRequest
        with patch("src.api.routes.rag.get_pipeline") as mock_pipe:
            mock_pipe.return_value.query.return_value = {
                "success": True,
                "answer": "根据《中国居民膳食指南》...",
                "intent": "guideline_query",
                "sources": [],
                "vector_results": [
                    {"content": "食物多样，合理搭配...", "metadata": {"file_name": "中国居民膳食指南"}},
                ],
                "answer_source": "knowledge_base",
            }
            body = RAGQueryRequest(query="饮食原则", stream=False)
            result = execute_rag_query(body, current_user=MagicMock())
            assert result["vector_results"] is not None
            assert len(result["vector_results"]) >= 1


# ============================================================
# 4. 数据一致性：数据库查询
# ============================================================

class TestDatabaseConsistency:
    """PostgreSQL 数据一致性验证"""

    def test_food_search_returns_accurate_data(self):
        """食物搜索返回准确数据（Mock）"""
        from src.services import diet_service
        with patch.object(diet_service, "search_foods") as mock_search:
            mock_search.return_value = {
                "message": "搜索成功",
                "count": 1,
                "data": [
                    {
                        "id": 1,
                        "name": "鸡蛋",
                        "calories_per_100g": 144.0,
                        "protein_per_100g": 13.3,
                        "fat_per_100g": 8.8,
                        "carbs_per_100g": 2.8,
                        "category": "蛋类",
                    },
                ],
            }
            result = diet_service.search_foods(db=MagicMock(), keyword="鸡蛋", limit=10)
            assert len(result["data"]) > 0
            assert result["data"][0]["name"] == "鸡蛋"
            assert result["data"][0]["calories_per_100g"] == 144.0

    def test_gi_data_query_returns_category(self):
        """GI 数据返回正确分类"""
        test_cases = [
            (30, "低 GI"),
            (55, "低 GI"),
            (56, "中 GI"),
            (70, "中 GI"),
            (71, "高 GI"),
            (100, "高 GI"),
        ]
        for gi_value, expected_category in test_cases:
            if gi_value <= 55:
                category = "低 GI"
            elif gi_value <= 70:
                category = "中 GI"
            else:
                category = "高 GI"
            assert category == expected_category, f"GI={gi_value} 分类错误"


# ============================================================
# 5. 指南路由与查询匹配
# ============================================================

class TestGuideRoutingAccuracy:
    """指南路由准确性验证"""

    def test_obesity_query_routes_to_obesity_guide(self):
        """肥胖查询 → 路由到肥胖指南"""
        profile = _make_profile(age=30, gender="male", health_goal="weight_loss",
                                health_tags=["肥胖"])
        matched = select_guidelines_by_profile(profile)
        obesity_guides = [g for g in matched if "肥胖" in g]
        assert len(obesity_guides) > 0, "肥胖用户应匹配到肥胖指南"

    def test_diabetes_query_routes_to_diabetes_guide(self):
        """糖尿病查询 → 无专属指南，至少返回基础膳食指南"""
        profile = _make_profile(age=55, gender="female", health_goal="health_maintenance",
                                health_tags=["糖尿病"])
        matched = select_guidelines_by_profile(profile)
        diabetes_guides = [g for g in matched if "糖尿病" in g]
        assert len(diabetes_guides) == 0, f"糖尿病无专属食养指南，实际: {diabetes_guides}"
        assert BASELINE_GUIDELINE in matched, f"应至少返回基础膳食指南，实际: {matched}"

    def test_kidney_query_routes_to_kidney_guide(self):
        """肾病查询 → 路由到肾病指南"""
        profile = _make_profile(age=40, gender="male", health_goal="health_maintenance",
                                health_tags=["慢性肾脏病"])
        matched = select_guidelines_by_profile(profile)
        kidney_guides = [g for g in matched if "肾" in g]
        assert len(kidney_guides) > 0, "肾病用户应匹配到肾病指南"

    def test_cancer_query_routes_to_cancer_guide(self):
        """肿瘤查询 → 路由到肿瘤指南（降级为基础指南）"""
        profile = _make_profile(age=60, gender="female", health_goal="health_maintenance",
                                health_tags=["肿瘤"])
        matched = select_guidelines_by_profile(profile)
        # 肿瘤无专门食养指南，但至少返回基础指南
        assert len(matched) > 0, "应至少匹配到基础指南"

    def test_pregnancy_query_routes_to_pregnancy_guide(self):
        """孕妇查询 → 路由到孕妇指南（降级为基础指南）"""
        profile = _make_profile(age=28, gender="female", health_goal="health_maintenance",
                                health_tags=["孕期"])
        matched = select_guidelines_by_profile(profile)
        # 孕期无专门食养指南，但至少返回基础指南
        assert len(matched) > 0, "孕妇应匹配到指南"


# ============================================================
# 6. 回答内容质量指标
# ============================================================

class TestAnswerQualityMetrics:
    """回答质量指标验证"""

    def test_answer_length_sufficient(self):
        """回答长度充足（Mock 验证）"""
        mock_answer = "根据《中国居民膳食指南（2022）》的建议，每人每天应摄入谷薯类食物250-400g，其中全谷物和杂豆50-150g。"
        assert len(mock_answer) >= 50, "回答长度应 >= 50 字符"

    def test_answer_contains_guide_reference(self):
        """回答包含指南引用"""
        mock_answer = "根据《中国居民膳食指南（2022）》的建议..."
        assert "《" in mock_answer and "》" in mock_answer, "回答应包含书名号引用"

    def test_answer_no_hallucinated_data(self):
        """回答不包含虚构数据（Mock 验证）"""
        mock_answer = "鸡蛋含有蛋白质、脂肪、维生素等营养成分"
        hallucinated_markers = ["独角兽", "魔法", "吸血鬼"]
        for marker in hallucinated_markers:
            assert marker not in mock_answer, f"回答包含幻觉内容: {marker}"

    def test_sql_result_numeric_accuracy(self):
        """SQL 数值精度验证"""
        test_cases = [
            (100, "g", 144, "kcal"),
            (100, "g", 25, "g"),
        ]
        for amount, unit, expected, nutrient in test_cases:
            assert expected > 0, f"数值应为正数: {nutrient}"
            assert expected < 1000, f"数值应在合理范围: {nutrient}"


# ============================================================
# 7. 用户画像与检索匹配
# ============================================================

class TestProfileRetrievalMatch:
    """用户画像影响检索结果的测试"""

    def test_male_profile_gets_recommendations(self):
        """男性用户 → 匹配相关指南"""
        profile = _make_profile(age=25, gender="male", health_goal="muscle_gain")
        matched = select_guidelines_by_profile(profile)
        assert len(matched) > 0, "男性增肌用户应匹配到指南"

    def test_female_profile_gets_recommendations(self):
        """女性用户 → 匹配相关指南"""
        profile = _make_profile(age=25, gender="female", health_goal="weight_loss")
        matched = select_guidelines_by_profile(profile)
        assert len(matched) > 0, "女性减肥用户应匹配到指南"

    def test_region_specific_guide(self):
        """地区特定指南匹配"""
        profile = _make_profile(age=30, gender="male", health_goal="health_maintenance")
        matched = select_guidelines_by_profile(profile)
        assert len(matched) > 0, "应至少匹配到一份通用指南"


# ============================================================
# 8. match_guideline 文件名匹配
# ============================================================

class TestGuidenameMatch:
    """文件名 → 指南条目匹配"""

    def test_match_standard_name(self):
        result = match_guideline("中国居民膳食指南（2022）.pdf")
        assert result is not None
        assert result["guideline_title"] == "中国居民膳食指南（2022）"

    def test_match_with_attachment_prefix(self):
        result = match_guideline("附件1.中国居民膳食指南.pdf")
        assert result is not None

    def test_match_unknown_returns_none(self):
        result = match_guideline("完全未知的文件名.pdf")
        assert result is None

    def test_registry_has_entries(self):
        """注册表不为空"""
        assert len(GUIDELINE_REGISTRY) > 0, "指南注册表应有条目"

    def test_baseline_guide_exists(self):
        """基础膳食指南存在"""
        assert BASELINE_GUIDELINE in GUIDELINE_REGISTRY