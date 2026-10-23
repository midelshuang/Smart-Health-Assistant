import requests
import time

query = '有什么水果升糖慢的吗？'
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
        print(f'成功: {data.get("success")}')
        print(f'意图: {data.get("intent")}')
        answer = data.get('answer', '')
        print(f'答案: {answer[:500]}')
        print(f'来源数: {len(data.get("sources", []))}')
    else:
        print(f'错误响应: {r.text[:500]}')
except Exception as e:
    print(f'请求失败: {e}')
