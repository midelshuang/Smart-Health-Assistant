import sys
sys.path.insert(0, '.')
from sqlalchemy import text
from src.repositories.session import SessionLocal

db = SessionLocal()
try:
    count = db.execute(text("SELECT COUNT(*) FROM document_chunks")).scalar()
    print(f"Total chunks: {count}")

    sample = db.execute(text("SELECT chunk_id, source_type, LEFT(content, 100) as content_preview FROM document_chunks LIMIT 5"))
    for row in sample:
        print(f"  {row.chunk_id} | {row.source_type} | {row.content_preview[:80]}...")

    keywords = ['肥胖', '胖饮', '饮食', '肥胖饮食', '胖']
    conditions = []
    params = {}
    for i, kw in enumerate(keywords):
        pname = f"kw_{i}"
        params[pname] = f"%{kw}%"
        conditions.append(f'content ILIKE :{pname}')
    
    sql = text(f"""
        SELECT chunk_id, source_type, LEFT(content, 100) as preview,
        CASE 
            WHEN content ILIKE :exact THEN 100 
            ELSE 0 
        END + {" + ".join([f"CASE WHEN content ILIKE :kw_{i} THEN 10 ELSE 0 END" for i in range(len(keywords))])} AS score
        FROM document_chunks
        WHERE ({' OR '.join(conditions)})
        ORDER BY score DESC
        LIMIT 5
    """)
    params["exact"] = "%肥胖饮食%"
    
    result = db.execute(sql, params)
    rows = result.fetchall()
    print(f"\nKeyword search '肥胖饮食': {len(rows)} results")
    for row in rows:
        print(f"  score={row.score} | {row.chunk_id} | {row.source_type} | {row.preview[:80]}...")

    simple_sql = text("""
        SELECT chunk_id, source_type, LEFT(content, 100) as preview
        FROM document_chunks
        WHERE content ILIKE '%肥胖%'
        LIMIT 5
    """)
    result2 = db.execute(simple_sql)
    rows2 = result2.fetchall()
    print(f"\nSimple search '肥胖': {len(rows2)} results")
    for row in rows2:
        print(f"  {row.chunk_id} | {row.source_type} | {row.preview[:80]}...")
        
finally:
    db.close()