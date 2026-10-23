"""
测试"苹果适合减肥吃吗"查询
"""
import requests
import time

query = '苹果适合减肥吃吗？'
print(f'查询: {query}')
print('-' * 50)

t0 = time.time()
try:
    r = requests.post('http://localhost:8000/api/v1/rag/query', json={
        'query': query,
        'stream': False
    }, timeout=60)
    elapsed = (time.time() - t0) * 1000
    print(f'状态码: {r.status_code}')
    print(f'耗时: {elapsed:.0f}ms')

    if r.status_code == 200:
        data = r.json()
        print(f'\n成功: {data.get("success")}')
        print(f'意图: {data.get("intent")}')
        print(f'答案来源: {data.get("answer_source")}')
        answer = data.get('answer', '')
        print(f'\n答案: {answer[:600]}')
        sources = data.get('sources', [])
        print(f'\n来源数: {len(sources)}')
        for i, source in enumerate(sources[:5], 1):
            print(f'  {i}. {source.get("file_name")} (类型: {source.get("source_type")})')
    else:
        print(f'错误响应: {r.text[:800]}')
except Exception as e:
    print(f'请求失败: {e}')
