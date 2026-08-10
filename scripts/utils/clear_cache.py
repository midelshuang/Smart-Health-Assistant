import sys
sys.path.insert(0, '.')
from src.utils.redis_client import get_redis_client

client = get_redis_client()
if client.is_available():
    # 扫描所有缓存key
    keys = list(client._client.scan_iter("healthai:*", count=100))
    print(f"找到 {len(keys)} 条缓存")
    for k in sorted(keys)[:20]:
        print(f"  {k}")
    
    # 清除所有 RAG 查询缓存
    deleted = client.delete_pattern("healthai:*")
    print(f"\n已清除 {deleted} 条缓存")
else:
    print("Redis 不可用")