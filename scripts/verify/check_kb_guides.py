"""
检查知识库中的指南文档
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.repositories.vector_store import VectorStore
from src.core.embedding import get_embedding_function
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def check_knowledge_base():
    """检查知识库内容"""
    try:
        vs = VectorStore()
        vs.connect()
        embedding_fn = get_embedding_function()
        collection = vs.get_or_create_collection(embedding_function=embedding_fn)
        
        # 获取所有文档
        all_docs = collection.get()
        
        print(f"知识库总文档数: {len(all_docs.get('ids', []))}")
        
        if all_docs.get('ids'):
            print("\n指南文档列表:")
            for i, (doc_id, doc, metadata) in enumerate(zip(
                all_docs['ids'], 
                all_docs.get('documents', []), 
                all_docs.get('metadatas', [])
            ), 1):
                print(f"\n{i}. ID: {doc_id}")
                print(f"   文件名: {metadata.get('file_name', '未知')}")
                print(f"   来源类型: {metadata.get('source_type', '未知')}")
                print(f"   标签: {metadata.get('tags', [])}")
                print(f"   预览: {doc[:150]}...")
                
    except Exception as e:
        logger.error(f"检查知识库失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_knowledge_base()