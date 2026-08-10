"""
为知识库中的指南文档添加age_group和region标签
- 使用字符串存储（逗号分隔），支持ChromaDB的where过滤
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.repositories.vector_store import VectorStore
from src.core.embedding import get_embedding_function
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 指南文档标签映射 - 使用字符串存储，便于ChromaDB过滤
# key 用于模糊匹配文件名（包含匹配）
GUIDE_METADATA = {
    "中国居民膳食指南": {
        "age_group": "youth,middle_aged,elderly",
        "region": "全国",
        "guide_tags": "膳食指南,通用",
    },
    "中国居民膳食指南（2022）核心": {
        "age_group": "youth,middle_aged,elderly",
        "region": "全国",
        "guide_tags": "膳食指南,2022版,通用",
    },
    "成人肥胖食养指南": {
        "age_group": "youth,middle_aged",
        "region": "全国",
        "guide_tags": "肥胖,减重,成人",
    },
    "高血压": {
        "age_group": "middle_aged,elderly",
        "region": "全国",
        "guide_tags": "高血压,慢病",
    },
    "糖尿病": {
        "age_group": "youth,middle_aged,elderly",
        "region": "全国",
        "guide_tags": "糖尿病,慢病",
    },
    "高血脂": {
        "age_group": "youth,middle_aged,elderly",
        "region": "全国",
        "guide_tags": "高血脂,慢病",
    },
    "高尿酸": {
        "age_group": "youth,middle_aged,elderly",
        "region": "全国",
        "guide_tags": "痛风,高尿酸",
    },
    "慢性阻塞性肺疾病": {
        "age_group": "middle_aged,elderly",
        "region": "全国",
        "guide_tags": "慢阻肺,呼吸系统",
    },
    "减重饮食": {
        "age_group": "youth,middle_aged",
        "region": "全国",
        "guide_tags": "减重,科普",
    },
    "运动营养": {
        "age_group": "youth,middle_aged,adolescent",
        "region": "全国",
        "guide_tags": "运动,营养补充",
    },
    "蛋白质的吸收与利用": {
        "age_group": "youth,middle_aged,elderly",
        "region": "全国",
        "guide_tags": "蛋白质,营养",
    },
    "膳食纤维与健康": {
        "age_group": "youth,middle_aged,elderly",
        "region": "全国",
        "guide_tags": "膳食纤维,营养",
    },
    "儿童青少年肥胖": {
        "age_group": "child,adolescent",
        "region": "全国",
        "guide_tags": "儿童青少年,肥胖,2025版",
    },
    "儿童青少年膳食指南": {
        "age_group": "child,adolescent",
        "region": "全国",
        "guide_tags": "儿童青少年,膳食指南,2022版",
    },
    "孕期和哺乳期": {
        "age_group": "youth",
        "region": "全国",
        "guide_tags": "孕期,哺乳期,女性",
    },
    "老年人膳食指南": {
        "age_group": "elderly,middle_aged_plus",
        "region": "全国",
        "guide_tags": "老年人,膳食指南,2022版",
    },
    "江南地区": {
        "age_group": "youth,middle_aged,elderly",
        "region": "华东,江南",
        "guide_tags": "地区,江南,四季",
    },
}

def update_guide_metadata():
    """更新指南文档的元数据"""
    try:
        vs = VectorStore()
        vs.connect()
        embedding_fn = get_embedding_function()
        collection = vs.get_or_create_collection(embedding_function=embedding_fn)
        
        # 获取所有文档
        all_docs = collection.get()
        
        if not all_docs.get('ids'):
            logger.warning("知识库为空")
            return
        
        updated_count = 0
        skipped_count = 0
        
        # 按key长度降序排序，确保更长的key优先匹配
        sorted_keys = sorted(GUIDE_METADATA.keys(), key=len, reverse=True)
        
        for i, doc_id in enumerate(all_docs['ids']):
            metadata = all_docs['metadatas'][i]
            file_name = metadata.get('file_name', '')
            
            # 查找匹配的指南元数据（最长匹配优先）
            matched_meta = None
            matched_key = None
            for guide_name in sorted_keys:
                if guide_name in file_name:
                    matched_meta = GUIDE_METADATA[guide_name]
                    matched_key = guide_name
                    break
            
            if matched_meta:
                # 更新元数据 - 保留原有字段，添加新字段
                new_metadata = dict(metadata)
                new_metadata['age_group'] = matched_meta['age_group']
                new_metadata['region'] = matched_meta['region']
                new_metadata['guide_tags'] = matched_meta['guide_tags']
                
                # 更新文档元数据
                collection.update(
                    ids=[doc_id],
                    metadatas=[new_metadata],
                )
                
                logger.info(f"✅ 更新: {file_name} (匹配: {matched_key})")
                logger.info(f"   age_group: {matched_meta['age_group']}")
                logger.info(f"   region: {matched_meta['region']}")
                logger.info(f"   guide_tags: {matched_meta['guide_tags']}")
                updated_count += 1
            else:
                # 未匹配的文档也添加默认值
                if 'age_group' not in metadata:
                    new_metadata = dict(metadata)
                    new_metadata['age_group'] = 'youth,middle_aged,elderly'
                    new_metadata['region'] = '全国'
                    new_metadata['guide_tags'] = '通用'
                    
                    collection.update(
                        ids=[doc_id],
                        metadatas=[new_metadata],
                    )
                    logger.info(f"🔄 设为默认值: {file_name}")
                    updated_count += 1
                else:
                    logger.debug(f"ℹ️  跳过: {file_name}")
                    skipped_count += 1
        
        logger.info(f"\n更新完成！共更新 {updated_count} 条，跳过 {skipped_count} 条")
        
        # 验证更新结果
        logger.info("\n验证更新结果:")
        verify_docs = collection.get()
        age_groups = set()
        regions = set()
        for meta in verify_docs.get('metadatas', []):
            if meta.get('age_group'):
                for ag in meta['age_group'].split(','):
                    age_groups.add(ag.strip())
            if meta.get('region'):
                regions.add(meta['region'])
        
        logger.info(f"  age_group 值: {age_groups}")
        logger.info(f"  region 值: {regions}")
        
    except Exception as e:
        logger.error(f"更新元数据失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    update_guide_metadata()