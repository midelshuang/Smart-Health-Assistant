"""
测试年龄和地区映射的RAG检索功能
模拟: 16岁郑州男孩肥胖饮食规划

流程:
1. _extract_region_from_query("郑州") → "河南" (城市→省份)
2. _get_region_info("河南") → {"region": "华中", ...} (省份→区域)
3. _build_metadata_filter → ChromaDB where 过滤条件
4. _rerank_results_by_metadata → 检索结果重排序
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.repositories.vector_store import VectorStore
from src.core.embedding import get_embedding_function
from src.core.nodes import (
    _get_age_group, 
    _get_region_info, 
    _build_metadata_filter,
    _extract_region_from_query,
    _rerank_results_by_metadata,
)
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_mapping():
    """测试年龄和地区映射"""
    print("=" * 60)
    print("测试1: 年龄映射")
    print("=" * 60)
    
    test_ages = [0, 1, 4, 8, 13, 16, 20, 35, 50, 65, 80]
    for age in test_ages:
        age_group = _get_age_group(age)
        if age_group:
            print(f"  {age}岁 → {age_group['label']} (group={age_group['group']})")
        else:
            print(f"  {age}岁 → 未匹配")
    
    print("\n" + "=" * 60)
    print("测试2: 地区映射（城市→省份→区域）")
    print("=" * 60)
    
    test_cities = ["郑州", "北京", "上海", "广州", "成都", "武汉", "西安", "南京", "哈尔滨", "昆明"]
    for city in test_cities:
        province = _extract_region_from_query(city)
        if province:
            region_info = _get_region_info(province)
            print(f"  {city} → {province} → {region_info['region']}")
        else:
            print(f"  {city} → 未匹配")


def test_vector_search_with_rerank():
    """测试向量检索+重排序"""
    print("\n" + "=" * 60)
    print("测试3: 向量检索 + 元数据重排序")
    print("=" * 60)
    
    try:
        vs = VectorStore()
        vs.connect()
        embedding_fn = get_embedding_function()
        collection = vs.get_or_create_collection(embedding_function=embedding_fn)
        
        # 模拟16岁郑州男孩
        profile = {
            "age": 16,
            "region": "河南",
            "gender": "男",
        }
        
        # 查询青少年肥胖饮食
        query = "儿童青少年肥胖饮食"
        where_filter = {"source_type": {"$in": ["guideline", "expert_media"]}}
        
        print(f"  用户: 16岁, 河南 (华中)")
        print(f"  查询: {query}")
        
        # 先检索所有指南
        results = vs.similarity_search(
            query_text=query,
            top_k=10,
            threshold=0.2,
            where=where_filter,
            embedding_function=embedding_fn,
        )
        
        print(f"\n  初始检索结果 ({len(results)} 条):")
        for i, r in enumerate(results):
            print(f"  [{i+1}] 得分: {r['score']:.4f} | {r['metadata'].get('file_name', 'N/A')} | age_group: {r['metadata'].get('age_group', 'N/A')}")
        
        # 应用元数据重排序
        reranked_results = _rerank_results_by_metadata(results, profile)
        
        print(f"\n  重排序后结果 ({len(reranked_results)} 条):")
        for i, r in enumerate(reranked_results):
            print(f"  [{i+1}] 得分: {r['score']:.4f} | {r['metadata'].get('file_name', 'N/A')} | age_group: {r['metadata'].get('age_group', 'N/A')}")
        
        # 验证重排序效果
        if reranked_results:
            top = reranked_results[0]
            top_age_group = top['metadata'].get('age_group', '')
            if 'adolescent' in str(top_age_group):
                print(f"\n  ✅ 重排序成功: 顶部文档匹配青少年 (age_group={top_age_group})")
            else:
                print(f"\n  ⚠️  顶部文档不匹配青少年 (age_group={top_age_group})")
        
    except Exception as e:
        logger.error(f"测试失败: {e}")
        import traceback
        traceback.print_exc()


def test_full_flow():
    """测试完整流程：从用户查询到检索结果"""
    print("\n" + "=" * 60)
    print("测试4: 完整流程模拟")
    print("=" * 60)
    
    # 模拟用户查询
    user_query = "郑州16岁男孩肥胖饮食规划"
    
    print(f"  用户查询: {user_query}")
    
    # 1. 提取信息
    from src.core.nodes import (
        _extract_age_from_query,
        _extract_region_from_query,
        _extract_gender_from_query,
    )
    
    age = _extract_age_from_query(user_query)
    region_city = _extract_region_from_query(user_query)
    gender = _extract_gender_from_query(user_query)
    
    print(f"  提取: 年龄={age}, 城市={region_city}, 性别={gender}")
    
    # 2. 城市→省份→区域
    if region_city:
        region_info = _get_region_info(region_city)
        print(f"  地区映射: {region_city} → {region_city} → {region_info['region']}")
    
    # 3. 年龄→群体
    if age:
        age_group = _get_age_group(age)
        print(f"  年龄映射: {age}岁 → {age_group['label']} (group={age_group['group']})")
        print(f"  指南关键词: {age_group['guideline_keywords']}")
    
    # 4. 构建profile
    profile = {
        "age": age,
        "region": region_city,  # 省份
        "gender": gender,
    }
    
    print(f"\n  用户档案: {profile}")
    print(f"  → 将用于向量检索和重排序")


if __name__ == "__main__":
    test_mapping()
    test_vector_search_with_rerank()
    test_full_flow()