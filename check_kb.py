"""检查知识库数据状态 - 修正版"""
import os, sys
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()

DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "healthai_db")
DB_USER = os.getenv("POSTGRES_USER", "postgres")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "postgres")

print(f"DB: {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

# 1. PostgreSQL
print("\n" + "=" * 60)
print("1. PostgreSQL 数据检查")
print("=" * 60)
try:
    import psycopg2
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
    tables = [r[0] for r in cur.fetchall()]
    print(f"Tables ({len(tables)}): {tables}")
    for t in ['documents', 'food_nutrition', 'health_guidelines', 'video_knowledge', 'users', 'user_profiles', 'diet_plans']:
        if t in tables:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            count = cur.fetchone()[0]
            print(f"\n  {t}: {count} rows")
            if count > 0:
                if t == 'documents':
                    cur.execute(f"SELECT id, title, doc_type, source_type FROM {t} LIMIT 10")
                    for r in cur.fetchall(): print(f"    {r}")
                elif t == 'food_nutrition':
                    cur.execute(f"SELECT food_name, energy_kcal, protein_g FROM {t} LIMIT 5")
                    for r in cur.fetchall(): print(f"    {r}")
                elif t == 'health_guidelines':
                    cur.execute(f"SELECT id, title, category FROM {t} LIMIT 5")
                    for r in cur.fetchall(): print(f"    {r}")
                elif t == 'video_knowledge':
                    cur.execute(f"SELECT id, title, source FROM {t} LIMIT 5")
                    for r in cur.fetchall(): print(f"    {r}")
    conn.close()
except Exception as e:
    print(f"  PostgreSQL Error: {e}")

# 2. ChromaDB
print("\n" + "=" * 60)
print("2. ChromaDB 向量库检查")
print("=" * 60)
try:
    import chromadb
    chroma_host = os.getenv("CHROMA_HOST", "localhost")
    chroma_port = os.getenv("CHROMA_PORT", "8000")
    print(f"ChromaDB: {chroma_host}:{chroma_port}")
    # Try HTTP client first
    try:
        client = chromadb.HttpClient(host=chroma_host, port=int(chroma_port))
        cols = client.list_collections()
        print(f"Collections (HTTP): {[c.name for c in cols]}")
        for c in cols:
            count = c.count()
            print(f"  {c.name}: {count} docs")
    except:
        # Fallback to persistent
        client = chromadb.PersistentClient(path='./chroma_db')
        cols = client.list_collections()
        print(f"Collections (Persistent): {[c.name for c in cols]}")
        for c in cols:
            count = c.count()
            print(f"  {c.name}: {count} docs")
except Exception as e:
    print(f"  ChromaDB Error: {e}")

# 3. Check for data/seed scripts
print("\n" + "=" * 60)
print("3. 数据源文件检查")
print("=" * 60)
import glob
for pattern in ['data/**/*.json', 'data/**/*.csv', 'data/**/*.yaml', 'knowledge_base/**/*', 'seed*.*', 'scripts/*seed*', 'scripts/*import*', 'scripts/*load*']:
    files = glob.glob(pattern, recursive=True)
    if files:
        print(f"\n  {pattern}:")
        for f in files[:10]:
            print(f"    {f} ({os.path.getsize(f)} bytes)")

# Check SQL init
print("\n  SQL files:")
for f in glob.glob('database/*.sql'):
    print(f"    {f} ({os.path.getsize(f)} bytes)")

print("\n检查完成")
