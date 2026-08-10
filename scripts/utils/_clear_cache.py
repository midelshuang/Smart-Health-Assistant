"""
清空 RAG 相关 Redis 缓存。

清除所有 rag:* 前缀的缓存键，包括：
  - rag:query:*   RAG 查询结果缓存
  - rag:embed:*   嵌入向量缓存
  - rag:food:*    食物检索缓存
  - rag:tdee:*    TDEE 计算缓存
  - rag:plan:*    饮食计划缓存
  - rag:safety:*  安全护栏判断缓存

适用场景：
  - 重建 ChromaDB（reindex_guidelines.py）后清除旧查询结果
  - 排查缓存导致的异常行为
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from src.utils.redis_client import get_redis_client


def main() -> None:
    client = get_redis_client()
    if not client.is_available():
        print("Redis 不可用，跳过清理")
        return
    deleted = client.delete_pattern("rag:*")
    print(f"Cleared {deleted} rag:* cache keys")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
