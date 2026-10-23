"""测试文档溯源接口"""
import httpx
import json

BASE_URL = "http://localhost:8000"

# 1. 先发一个查询请求，获取 sources 中的 chunk_id
print("1. 发送 RAG 查询请求...")
r = httpx.post(
    f"{BASE_URL}/api/v1/rag/query",
    json={"query": "肥胖患者食谱做法", "conversation_context": []},
    timeout=120,
)
data = r.json()
sources = data.get("sources", [])
print(f"   HTTP {r.status_code} | 来源数: {len(sources)}")

if not sources:
    print("   无来源，退出")
    exit()

# 打印 sources 结构
print("\n2. Sources 结构：")
for i, s in enumerate(sources[:3], 1):
    print(f"   {i}. file_name: {s.get('file_name', '')}")
    print(f"      chunk_id: {s.get('chunk_id', '')}")
    print(f"      chunk_index: {s.get('chunk_index', -1)}")
    print(f"      guideline_title: {s.get('guideline_title', '')}")
    print(f"      file_path: {s.get('file_path', '')}")
    print()

# 3. 用第一个 chunk_id 调用文档溯源接口
chunk_id = sources[0].get("chunk_id", "")
if not chunk_id:
    print("3. chunk_id 为空，无法测试溯源接口")
    exit()

print(f"3. 调用文档溯源接口: GET /api/v1/rag/chunk/{chunk_id}")
r2 = httpx.get(f"{BASE_URL}/api/v1/rag/chunk/{chunk_id}", timeout=30)
print(f"   HTTP {r2.status_code}")

if r2.status_code == 200:
    chunk_data = r2.json()
    print(f"   chunk_id: {chunk_data.get('chunk_id', '')}")
    print(f"   file_name: {chunk_data.get('file_name', '')}")
    print(f"   file_path: {chunk_data.get('file_path', '')}")
    print(f"   source_type: {chunk_data.get('source_type', '')}")
    print(f"   guideline_title: {chunk_data.get('guideline_title', '')}")
    print(f"   chunk_index: {chunk_data.get('chunk_index', -1)}")
    print(f"   char_count: {chunk_data.get('char_count', 0)}")
    content = chunk_data.get("content", "")
    print(f"   content (前300字): {content[:300]}...")
    print(f"   metadata: {json.dumps(chunk_data.get('metadata', {}), ensure_ascii=False, indent=2)}")
else:
    print(f"   错误: {r2.text}")
