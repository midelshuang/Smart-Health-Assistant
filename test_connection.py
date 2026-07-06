import psycopg2
import chromadb


def test_postgres():
    """测试 PostgreSQL 连接"""
    print("🔍 正在尝试连接 PostgreSQL...")
    try:
        conn = psycopg2.connect(
            host="localhost",
            port="5432",
            database="healthai_db",  # 默认数据库名，根据你的 docker-compose 配置可能不同
            user="postgres",  # 默认用户名
            password="postgres"  # 默认密码，请替换为你设置的密码
        )
        cur = conn.cursor()
        cur.execute("SELECT version();")
        db_version = cur.fetchone()
        print(f"✅ PostgreSQL 连接成功! 版本: {db_version[0]}")
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ PostgreSQL 连接失败: {e}")
        return False


def test_chroma():
    """测试 ChromaDB 连接"""
    print("\n🔍 正在尝试连接 ChromaDB...")
    try:
        # 使用 HttpClient 连接本地运行的 Docker 容器
        client = chromadb.HttpClient(host="localhost", port=8000)

        # 尝试获取集合列表来验证连接
        collections = client.list_collections()
        print(f"✅ ChromaDB 连接成功! 当前集合数量: {len(collections)}")
        return True
    except Exception as e:
        print(f"❌ ChromaDB 连接失败: {e}")
        return False


if __name__ == "__main__":
    print("--- 开始环境连通性测试 ---")
    pg_ok = test_postgres()
    ch_ok = test_chroma()

    print("\n--- 测试结果汇总 ---")
    if pg_ok and ch_ok:
        print("🎉 恭喜！所有服务连接正常，可以开始开发了！")
    else:
        print("⚠️ 部分服务连接异常，请检查上方报错信息。")