"""快速测试文档溯源接口"""
import httpx
import json

chunk_id = "guide_189f83c136f0_25"
r = httpx.get(f"http://localhost:8000/api/v1/rag/chunk/{chunk_id}", timeout=30)
print(f"HTTP {r.status_code}")

if r.status_code == 200:
    data = r.json()
    print(f"chunk_id: {data.get('chunk_id')}")
    print(f"file_name: {data.get('file_name')}")
    print(f"file_path: {data.get('file_path')}")
    print(f"source_type: {data.get('source_type')}")
    print(f"guideline_title: {data.get('guideline_title')}")
    print(f"chunk_index: {data.get('chunk_index')}")
    print(f"char_count: {data.get('char_count')}")
    content = data.get("content", "")
    print(f"content (前300字):")
    print(content[:300])
    print(f"\nmetadata: {json.dumps(data.get('metadata', {}), ensure_ascii=False, indent=2)}")
else:
    print(f"错误: {r.text}")
