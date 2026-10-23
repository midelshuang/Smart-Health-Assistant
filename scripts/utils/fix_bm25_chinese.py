"""
迁移脚本 Part 2: 修 BM25 中文分词（不装 zhparser）

思路: 用 PostgreSQL regexp_replace 把连写中文在每个汉字后加空格，
     这样 simple 配置就能按单字分词，搜索端也做同样处理。

原理:
  "肥胖食养饮食管理" → "肥 胖 食 养 饮 食 管 理"
  simple 分词后: 8 个单字 token
  搜索端 "肥胖饮食" → "肥 胖 饮 食" → plainto_tsquery 也分 4 个 token
  两边对齐后就能用 BM25 了

用法:
    python scripts/utils/fix_bm25_chinese.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import text
from src.repositories.session import SessionLocal
from src.utils.logger import setup_logger

logger = setup_logger(name="BM25Migrate")

# 用 simple 配置 + 中文预分词（regexp_replace 每字后加空格）
_TS_CONFIG = os.getenv("PG_TS_CONFIG", "simple")

# 中文预分词函数: 在每个 CJK 汉字后加空格
_CN_TOKENIZE_SQL = r"""
    regexp_replace(%s, '([\u4e00-\u9fff])', '\1 ', 'g')
"""


def run():
    db = SessionLocal()
    try:
        # ---- 1. 重写触发器函数 ----
        logger.info("重写触发器（加中文预分词）...")
        db.execute(text(f"""
            CREATE OR REPLACE FUNCTION document_chunks_tsv_trigger() RETURNS trigger AS $$
            BEGIN
                NEW.content_tsvector :=
                    setweight(to_tsvector('{_TS_CONFIG}',
                        regexp_replace(COALESCE(NEW.content, ''), '([\\u4e00-\\u9fff])', '\\1 ', 'g')), 'A') ||
                    setweight(to_tsvector('{_TS_CONFIG}',
                        regexp_replace(COALESCE(NEW.guideline_title, ''), '([\\u4e00-\\u9fff])', '\\1 ', 'g')), 'B') ||
                    setweight(to_tsvector('{_TS_CONFIG}',
                        regexp_replace(COALESCE(NEW.expert_name, ''), '([\\u4e00-\\u9fff])', '\\1 ', 'g')), 'B');
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """))
        db.commit()
        logger.info("  ✅ 触发器已更新")

        # ---- 2. 删掉旧触发器重建（trigger 会自动生效）----
        logger.info("重建触发器 ...")
        db.execute(text("""
            DROP TRIGGER IF EXISTS trg_document_chunks_tsv ON document_chunks;
            CREATE TRIGGER trg_document_chunks_tsv
                BEFORE INSERT OR UPDATE OF content, guideline_title, expert_name
                ON document_chunks
                FOR EACH ROW EXECUTE FUNCTION document_chunks_tsv_trigger();
        """))
        db.commit()
        logger.info("  ✅ 触发器就绪")

        # ---- 3. 存量数据重新生成 tsvector ----
        cnt = db.execute(text("SELECT COUNT(*) FROM document_chunks")).fetchone()[0]
        logger.info(f"重新生成 {cnt} 行 tsvector（加中文预分词）...")
        db.execute(text(f"""
            UPDATE document_chunks
            SET content_tsvector =
                setweight(to_tsvector('{_TS_CONFIG}',
                    regexp_replace(COALESCE(content, ''), '([\\u4e00-\\u9fff])', '\\1 ', 'g')), 'A') ||
                setweight(to_tsvector('{_TS_CONFIG}',
                    regexp_replace(COALESCE(guideline_title, ''), '([\\u4e00-\\u9fff])', '\\1 ', 'g')), 'B') ||
                setweight(to_tsvector('{_TS_CONFIG}',
                    regexp_replace(COALESCE(expert_name, ''), '([\\u4e00-\\u9fff])', '\\1 ', 'g')), 'B')
            WHERE TRUE;
        """))
        db.commit()
        logger.info(f"  ✅ {cnt} 行全部重新生成")

        # ---- 4. 实测 ----
        logger.info("\n=== BM25 实测 ===")
        test = db.execute(text(f"""
            SELECT chunk_id, guideline_title,
                   ts_rank_cd(content_tsvector,
                              plainto_tsquery('{_TS_CONFIG}', regexp_replace(:q, '([\\u4e00-\\u9fff])', '\\1 ', 'g')),
                              32) AS score
            FROM document_chunks
            WHERE content_tsvector @@ plainto_tsquery('{_TS_CONFIG}', regexp_replace(:q, '([\\u4e00-\\u9fff])', '\\1 ', 'g'))
            ORDER BY score DESC
            LIMIT 5
        """), {"q": "肥胖食养"}).fetchall()
        if test:
            logger.info(f"✅ BM25 能工作！命中 {len(test)} 条")
            for t in test[:5]:
                logger.info(f"  score={round(float(t[2]), 4):.4f} | {t[1] or '(无指南标题)'}")
        else:
            logger.error("❌ BM25 仍无命中")

        test2 = db.execute(text(f"""
            SELECT chunk_id, guideline_title,
                   ts_rank_cd(content_tsvector,
                              plainto_tsquery('{_TS_CONFIG}', regexp_replace(:q, '([\\u4e00-\\u9fff])', '\\1 ', 'g')),
                              32) AS score
            FROM document_chunks
            WHERE content_tsvector @@ plainto_tsquery('{_TS_CONFIG}', regexp_replace(:q, '([\\u4e00-\\u9fff])', '\\1 ', 'g'))
            ORDER BY score DESC
            LIMIT 3
        """), {"q": "饮食管理"}).fetchall()
        if test2:
            logger.info(f"✅ 第二个 query 也能命中 {len(test2)} 条")
        else:
            logger.error("❌ 第二个 query 无命中")

    except Exception as e:
        db.rollback()
        logger.error(f"迁移失败: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run()
