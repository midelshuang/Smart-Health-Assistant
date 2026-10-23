"""
文档分块仓储层 — PostgreSQL 存储（与 ChromaDB 双写）

职责:
1. 存储文档分块全文（用于 BM25 关键词检索）
2. 支持按 source_type/guideline_title/age_group 等条件过滤
3. 提供基于 PostgreSQL tsvector + ts_rank_cd 的真正 BM25 检索接口
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from sqlalchemy import text
from typing import List, Dict, Any, Optional

from src.repositories.session import SessionLocal
from src.models.db_models import DocumentChunk
from src.utils.logger import setup_logger

logger = setup_logger(name="ChunkRepository")

# PostgreSQL 中文分词配置：使用 `zhparser`（若已安装），否则回退 `simple`
# 在建库脚本中执行 CREATE EXTENSION IF NOT EXISTS zhparser;
# 并创建自定义文本搜索配置：
#   CREATE TEXT SEARCH CONFIGURATION chinese_zhcfg (PARSER = zhparser);
#   ALTER TEXT SEARCH CONFIGURATION chinese_zhcfg ADD MAPPING FOR n,v,a,i,e,l WITH simple;
_TS_CONFIG = os.getenv("PG_TS_CONFIG", "simple")


def create_chunks(chunks: List[Dict[str, Any]]) -> int:
    """批量写入分块到 PostgreSQL（双写）"""
    if not chunks:
        return 0

    db = SessionLocal()
    try:
        count = 0
        for c in chunks:
            chunk_id = c.get("chunk_id")
            if not chunk_id:
                continue
            content = c.get("content", "")
            meta = c.get("metadata", {})

            existing = db.query(DocumentChunk).filter_by(chunk_id=chunk_id).first()
            if existing:
                continue

            doc_chunk = DocumentChunk(
                chunk_id=chunk_id,
                source_type=meta.get("source_type", ""),
                file_name=meta.get("file_name", ""),
                file_path=meta.get("file_path", ""),
                chunk_index=c.get("chunk_index", 0),
                content=content,
                content_summary=content[:200] if content else "",
                char_count=len(content),
                guideline_title=meta.get("guideline_title", ""),
                audience=meta.get("audience", ""),
                disease_tags=meta.get("disease_tags", ""),
                region=meta.get("region", ""),
                age_group=meta.get("age_group", ""),
                doc_type=meta.get("doc_type", ""),
                expert_name=meta.get("expert_name", ""),
                expert_category=meta.get("expert_category", ""),
                metadata_json={k: v for k, v in meta.items()
                              if k not in ("source_type", "file_name", "file_path",
                                          "guideline_title", "audience", "disease_tags",
                                          "region", "age_group", "doc_type",
                                          "expert_name", "expert_category")},
                # content_tsvector 通过触发器自动生成（见 ensure_table_exists），此处留空
                content_tsvector=None,
            )
            db.add(doc_chunk)
            count += 1

        db.commit()
        if count > 0:
            logger.info(f"写入 {count} 个分块到 PostgreSQL")
        return count
    except Exception as e:
        db.rollback()
        logger.error(f"PostgreSQL 分块写入失败: {e}")
        raise
    finally:
        db.close()


def _parse_where_filter(where_filter: Optional[Dict[str, Any]], params: Dict[str, Any]) -> List[str]:
    """将 ChromaDB 风格的 where_filter 转换为 SQL WHERE 子句片段"""
    if not where_filter:
        return []

    or_clauses = []

    def _parse_and(and_list: List[Dict[str, Any]]) -> str:
        parts = []
        for cond in and_list:
            for key, val in cond.items():
                if isinstance(val, dict):
                    for op, op_val in val.items():
                        if op == "$in":
                            ph = []
                            for j, v in enumerate(op_val):
                                pn = f"{key}_in_{len(params)}_{j}"
                                params[pn] = v
                                ph.append(f":{pn}")
                            parts.append(f'"{key}" IN ({", ".join(ph)})')
                        elif op == "$eq":
                            pn = f"{key}_eq_{len(params)}"
                            params[pn] = op_val
                            parts.append(f'"{key}" = :{pn}')
                else:
                    pn = f"{key}_val_{len(params)}"
                    params[pn] = val
                    parts.append(f'"{key}" = :{pn}')
        return " AND ".join(parts) if parts else "TRUE"

    for item in where_filter.get("$or", []):
        if "$and" in item:
            clause = _parse_and(item["$and"])
            or_clauses.append(clause)
        else:
            for key, val in item.items():
                if isinstance(val, dict) and "$in" in val:
                    ph = []
                    for j, v in enumerate(val["$in"]):
                        pn = f"{key}_top_{j}"
                        params[pn] = v
                        ph.append(f":{pn}")
                    or_clauses.append(f'"{key}" IN ({", ".join(ph)})')
                else:
                    pn = f"{key}_top_{len(params)}"
                    params[pn] = val
                    or_clauses.append(f'"{key}" = :{pn}')

    return ["(" + " OR ".join(or_clauses) + ")"] if or_clauses else []


def keyword_search(
    query: str,
    top_k: int = 20,
    where_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    基于 PostgreSQL tsvector + ts_rank_cd 的真正 BM25 关键词检索

    实现思路：
    1. 使用 to_tsvector() 将 content 转为倒排索引向量
    2. 使用 plainto_tsquery() 将用户查询转为 tsquery（自动处理 AND/OR）
    3. 使用 ts_rank_cd() 计算 BM25 风格的相关性得分（覆盖密度 + 词频）
    4. 若无 tsvector 列或查询无有效分词，自动降级为 ILIKE 模糊匹配兜底
    5. 结合 _parse_where_filter 实现元数据过滤
    """
    if not query or not query.strip():
        return []

    db = SessionLocal()
    try:
        params: Dict[str, Any] = {}
        conditions = []

        sql_conditions = _parse_where_filter(where_filter, params)
        conditions.extend(sql_conditions)

        # ---- 方案 A：优先使用 tsvector + ts_rank_cd 真正 BM25 ----
        # 尝试判断是否有 content_tsvector 列（由 ensure_table_exists 创建）
        # 若列不存在则降级为 ILIKE
        has_tsv_col = _check_column_exists(db, "document_chunks", "content_tsvector")

        params["query_text"] = query.strip()
        params["top_k"] = top_k

        if has_tsv_col:
            # 走 BM25 路径：content_tsvector @@ plainto_tsquery
            # 中文预分词：在每个汉字后加空格（与触发器一致）
            # 让 simple 配置能正确分词（simple 按空白分词，对连写中文无效）
            conditions.append(
                f"content_tsvector @@ plainto_tsquery('{_TS_CONFIG}', "
                f"regexp_replace(:query_text, '([\\u4e00-\\u9fff])', '\\1 ', 'g'))"
            )

            where_clause = " AND ".join(conditions) if conditions else "TRUE"

            sql = text(f"""
                SELECT id, chunk_id, content, guideline_title, source_type,
                       file_name, age_group, region, disease_tags,
                       char_count,
                       ts_rank_cd(content_tsvector,
                                  plainto_tsquery('{_TS_CONFIG}',
                                      regexp_replace(:query_text, '([\\u4e00-\\u9fff])', '\\1 ', 'g')),
                                  32) AS score
                FROM document_chunks
                WHERE {where_clause}
                ORDER BY score DESC, char_count DESC
                LIMIT :top_k
            """)
        else:
            # ---- 方案 B：降级为 ILIKE 模糊匹配 + 关键词加权 ----
            keywords = _extract_keywords(query)
            if keywords:
                kw_clauses = []
                for i, kw in enumerate(keywords):
                    pname = f"kw_{i}"
                    params[pname] = f"%{kw}%"
                    kw_clauses.append(f'content ILIKE :{pname}')
                conditions.append("(" + " OR ".join(kw_clauses) + ")")

            params["exact"] = f"%{query}%"

            score_parts = [f"CASE WHEN content ILIKE :exact THEN 100 ELSE 0 END"]
            for i in range(len(keywords)):
                score_parts.append(f"CASE WHEN content ILIKE :kw_{i} THEN 10 ELSE 0 END")

            where_clause = " AND ".join(conditions) if conditions else "TRUE"

            sql = text(f"""
                SELECT id, chunk_id, content, guideline_title, source_type,
                       file_name, age_group, region, disease_tags,
                       char_count,
                       {" + ".join(score_parts)} AS score
                FROM document_chunks
                WHERE {where_clause}
                ORDER BY score DESC, char_count DESC
                LIMIT :top_k
            """)

        result = db.execute(sql, params)
        rows = result.fetchall()

        results = []
        for row in rows:
            raw_score = float(row.score)
            # 归一化分数到 [0.01, 1.0] 区间，便于与向量路余弦相似度做 RRF 融合
            if has_tsv_col:
                # ts_rank_cd 范围 [0, 1+]（通常 < 1），直接 clip
                normalized = max(0.01, min(1.0, raw_score))
            else:
                normalized = raw_score / 100.0 if raw_score > 0 else 0.01
            results.append({
                "id": row.chunk_id,
                "content": row.content,
                "score": normalized,
                "metadata": {
                    "source_type": row.source_type,
                    "guideline_title": row.guideline_title or "",
                    "file_name": row.file_name or "",
                    "age_group": row.age_group or "",
                    "region": row.region or "",
                    "disease_tags": row.disease_tags or "",
                },
            })

        mode_label = "tsvector-BM25" if has_tsv_col else "ILIKE-fallback"
        logger.info(
            f"PostgreSQL 关键词检索[{mode_label}]: query='{query[:30]}', 命中 {len(results)} 条"
        )
        return results
    except Exception as e:
        logger.error(f"PostgreSQL 关键词检索失败: {e}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        db.close()


def _check_column_exists(db, table_name: str, column_name: str) -> bool:
    """检查 PostgreSQL 表是否存在某列（用于 tsvector 列兼容检测）"""
    try:
        sql = text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = :tbl AND column_name = :col
            )
        """)
        row = db.execute(sql, {"tbl": table_name, "col": column_name}).fetchone()
        return bool(row[0]) if row else False
    except Exception:
        return False


def _extract_keywords(query: str) -> List[str]:
    """从查询中提取关键词（中文双字切分 + 英文按词）"""
    import re
    keywords = set()
    query_lower = query.lower().strip()

    for token in re.split(r'\s+', query_lower):
        if len(token) >= 2:
            keywords.add(token)
        if len(token) >= 2:
            for i in range(len(token) - 1):
                bi = token[i:i + 2]
                if any('\u4e00' <= c <= '\u9fff' for c in bi):
                    keywords.add(bi)

    return sorted(keywords, key=len, reverse=True)[:20]


def get_chunk_count(source_type: Optional[str] = None) -> int:
    """获取分块总数"""
    db = SessionLocal()
    try:
        q = db.query(DocumentChunk)
        if source_type:
            q = q.filter_by(source_type=source_type)
        return q.count()
    finally:
        db.close()


def delete_by_source_type(source_type: str) -> int:
    """按 source_type 删除分块"""
    db = SessionLocal()
    try:
        count = db.query(DocumentChunk).filter_by(source_type=source_type).delete()
        db.commit()
        logger.info(f"删除 source_type={source_type} 的 {count} 个分块")
        return count
    except Exception as e:
        db.rollback()
        logger.error(f"删除失败: {e}")
        raise
    finally:
        db.close()


def ensure_table_exists() -> bool:
    """确保 document_chunks 表存在（含 BM25 所需的 tsvector 列、GIN 索引和触发器）"""
    db = SessionLocal()
    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS document_chunks (
                id SERIAL PRIMARY KEY,
                chunk_id VARCHAR(128) UNIQUE NOT NULL,
                source_type VARCHAR(32) NOT NULL,
                file_name VARCHAR(500),
                file_path VARCHAR(1000),
                chunk_index INTEGER,
                content TEXT NOT NULL,
                content_summary TEXT,
                token_count INTEGER,
                char_count INTEGER,
                guideline_title VARCHAR(500),
                audience VARCHAR(200),
                disease_tags VARCHAR(500),
                region VARCHAR(50),
                age_group VARCHAR(200),
                doc_type VARCHAR(50),
                expert_name VARCHAR(100),
                expert_category VARCHAR(200),
                metadata_json JSONB,
                content_tsvector TSVECTOR,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """))

        # ---- BM25 加速：为 content_tsvector 创建 GIN 倒排索引 ----
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_document_chunks_content_tsv
                ON document_chunks USING GIN(content_tsvector);
        """))

        # ---- 元数据过滤索引 ----
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_document_chunks_source_type
                ON document_chunks(source_type);
            CREATE INDEX IF NOT EXISTS ix_document_chunks_guideline_title
                ON document_chunks(guideline_title);
            CREATE INDEX IF NOT EXISTS ix_document_chunks_source_type_guideline_title
                ON document_chunks(source_type, guideline_title);
        """))

        # ---- 触发器：INSERT/UPDATE 时自动更新 content_tsvector ----
        # 使用动态配置名（simple / chinese_zhcfg 等），避免依赖 zhparser 扩展
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

            DROP TRIGGER IF EXISTS trg_document_chunks_tsv ON document_chunks;
            CREATE TRIGGER trg_document_chunks_tsv
                BEFORE INSERT OR UPDATE OF content, guideline_title, expert_name
                ON document_chunks
                FOR EACH ROW EXECUTE FUNCTION document_chunks_tsv_trigger();
        """))

        # ---- 存量数据补齐 tsvector（首次建表后可能为空） ----
        db.execute(text(f"""
            UPDATE document_chunks
            SET content_tsvector =
                setweight(to_tsvector('{_TS_CONFIG}', COALESCE(content, '')), 'A') ||
                setweight(to_tsvector('{_TS_CONFIG}', COALESCE(guideline_title, '')), 'B') ||
                setweight(to_tsvector('{_TS_CONFIG}', COALESCE(expert_name, '')), 'B')
            WHERE content_tsvector IS NULL;
        """))

        db.commit()
        logger.info(f"document_chunks 表已就绪（BM25 ts_config={_TS_CONFIG}）")
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"建表失败: {e}")
        return False
    finally:
        db.close()