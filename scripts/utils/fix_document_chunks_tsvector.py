"""
迁移脚本：补齐 document_chunks 表缺失的 BM25 组件

缺的东西：
  1. content_tsvector 列（TSVECTOR 类型）
  2. ix_document_chunks_content_tsv GIN 倒排索引
  3. document_chunks_tsv_trigger() 触发器函数
  4. trg_document_chunks_tsv 触发器（INSERT/UPDATE 时自动更新 tsvector）
  5. 存量数据补齐 tsvector

用法:
    python scripts/utils/fix_document_chunks_tsvector.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import text
from src.repositories.session import SessionLocal
from src.utils.logger import setup_logger

logger = setup_logger(name="MigrateTSV")

_TS_CONFIG = os.getenv("PG_TS_CONFIG", "simple")


def run_migration():
    db = SessionLocal()
    try:
        # ---- Step 1: 加 content_tsvector 列 ----
        col_exists = db.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'document_chunks' AND column_name = 'content_tsvector'
            )
        """)).fetchone()[0]

        if not col_exists:
            logger.info("Adding column content_tsvector ...")
            db.execute(text("ALTER TABLE document_chunks ADD COLUMN content_tsvector TSVECTOR"))
            db.commit()
            logger.info("  ✅ content_tsvector added")
        else:
            logger.info("  ✅ content_tsvector already exists")

        # ---- Step 2: 创建 GIN 索引 ----
        logger.info("Creating GIN index ...")
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_document_chunks_content_tsv
                ON document_chunks USING GIN(content_tsvector);
        """))
        db.commit()
        logger.info("  ✅ GIN index ready")

        # ---- Step 3: 创建触发器函数 ----
        logger.info("Creating trigger function ...")
        db.execute(text(f"""
            CREATE OR REPLACE FUNCTION document_chunks_tsv_trigger() RETURNS trigger AS $$
            BEGIN
                NEW.content_tsvector :=
                    setweight(to_tsvector('{_TS_CONFIG}', COALESCE(NEW.content, '')), 'A') ||
                    setweight(to_tsvector('{_TS_CONFIG}', COALESCE(NEW.guideline_title, '')), 'B') ||
                    setweight(to_tsvector('{_TS_CONFIG}', COALESCE(NEW.expert_name, '')), 'B');
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """))
        db.commit()
        logger.info("  ✅ trigger function ready")

        # ---- Step 4: 创建触发器 ----
        logger.info("Creating trigger ...")
        db.execute(text("""
            DROP TRIGGER IF EXISTS trg_document_chunks_tsv ON document_chunks;
            CREATE TRIGGER trg_document_chunks_tsv
                BEFORE INSERT OR UPDATE OF content, guideline_title, expert_name
                ON document_chunks
                FOR EACH ROW EXECUTE FUNCTION document_chunks_tsv_trigger();
        """))
        db.commit()
        logger.info("  ✅ trigger ready")

        # ---- Step 5: 存量数据补齐 tsvector ----
        cnt = db.execute(text("SELECT COUNT(*) FROM document_chunks WHERE content_tsvector IS NULL")).fetchone()[0]
        if cnt > 0:
            logger.info(f"Backfilling tsvector for {cnt} existing rows ...")
            db.execute(text(f"""
                UPDATE document_chunks
                SET content_tsvector =
                    setweight(to_tsvector('{_TS_CONFIG}', COALESCE(content, '')), 'A') ||
                    setweight(to_tsvector('{_TS_CONFIG}', COALESCE(guideline_title, '')), 'B') ||
                    setweight(to_tsvector('{_TS_CONFIG}', COALESCE(expert_name, '')), 'B')
                WHERE content_tsvector IS NULL;
            """))
            db.commit()
            logger.info(f"  ✅ backfilled {cnt} rows")
        else:
            logger.info("  (no existing rows to backfill)")

        # ---- 验证 ----
        total = db.execute(text("SELECT COUNT(*) FROM document_chunks")).fetchone()[0]
        tsv_ok = db.execute(text("SELECT COUNT(*) FROM document_chunks WHERE content_tsvector IS NOT NULL")).fetchone()[0]
        logger.info(f"\n迁移完成！总行数={total}, tsvector 就绪={tsv_ok}")
        logger.info(f"ts_config = '{_TS_CONFIG}'")

    except Exception as e:
        db.rollback()
        logger.error(f"迁移失败: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run_migration()
