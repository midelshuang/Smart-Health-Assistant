import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.repositories.session import SessionLocal
from src.models.db_models import AuthoritativeSource, NutritionFact
from sqlalchemy import func

db = SessionLocal()

print("=" * 60)
print("PostgreSQL 知识库统计")
print("=" * 60)

# authoritative_sources
sources = db.query(AuthoritativeSource).all()
print(f"\nauthoritative_sources 表: {len(sources)} 条")
for s in sources:
    print(f"  [{s.id}] {s.title} ({s.source_type}, {s.media_type})")

# nutrition_facts
facts = db.query(NutritionFact).all()
categories = db.query(NutritionFact.category, func.count(NutritionFact.id)).group_by(NutritionFact.category).all()
print(f"\nnutrition_facts 表: {len(facts)} 条")
for cat, cnt in categories:
    print(f"  {cat}: {cnt} 条")

db.close()

print("\n" + "=" * 60)
print("ChromaDB 向量库统计")
print("=" * 60)

from src.repositories.vector_store import VectorStore
vs = VectorStore()
vs.connect()
embedding_fn = __import__('src.core.embedding', fromlist=['get_embedding_function']).get_embedding_function()
col = vs.get_or_create_collection(embedding_function=embedding_fn)

count = col.count()
print(f"\nCollection 'authoritative_sources': {count} 条向量")

# 尝试检索儿童青少年肥胖
results = col.query(
    query_texts=["儿童青少年肥胖饮食"],
    n_results=5,
)
print("\n检索 '儿童青少年肥胖饮食' 前5条:")
for i, (doc, meta, dist) in enumerate(zip(results['documents'][0], results['metadatas'][0], results['distances'][0])):
    print(f"  [{i+1}] dist={dist:.4f} | {meta.get('title', 'N/A')} ({meta.get('source_type', 'N/A')})")
    print(f"      {doc[:100]}...")

# 检索成人肥胖
results2 = col.query(
    query_texts=["成人肥胖食养指南"],
    n_results=3,
)
print("\n检索 '成人肥胖食养指南' 前3条:")
for i, (doc, meta, dist) in enumerate(zip(results2['documents'][0], results2['metadatas'][0], results2['distances'][0])):
    print(f"  [{i+1}] dist={dist:.4f} | {meta.get('title', 'N/A')} ({meta.get('source_type', 'N/A')})")
