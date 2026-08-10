import sys
sys.path.insert(0, '.')
from src.repositories.chunk_repository import get_chunk_count, keyword_search

total = get_chunk_count()
print(f"PostgreSQL document_chunks 总数: {total}")

for st in ("guideline", "new_food", "expert_media"):
    c = get_chunk_count(source_type=st)
    print(f"  - {st}: {c} 条")

print("\n关键词检索测试:")
results = keyword_search("肥胖饮食", top_k=3)
for i, r in enumerate(results):
    print(f"  [{i}] score={r['score']:.3f} | source={r['metadata'].get('source_type')} | guideline={r['metadata'].get('guideline_title','N/A')}")
    print(f"      content={r['content'][:80]}...")