"""
统一数据入库脚本：指南PDF + 新食品PDF + 专家媒体视频 + 大文件

双写存储：
  1. ChromaDB → 向量（语义检索）
  2. PostgreSQL document_chunks → 全文（BM25关键词检索）

用法:
    python scripts/unified_reindex.py                              # 全部入库
    python scripts/unified_reindex.py --target new_food             # 仅新食品
    python scripts/unified_reindex.py --target expert               # 仅专家媒体
    python scripts/unified_reindex.py --reset                       # 清空后全量
    python scripts/unified_reindex.py --target expert --test        # 专家媒体仅1个视频
"""
import os
import sys
import hashlib
import argparse
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from src.config import settings
from src.services.document_processor import DocumentProcessor
from src.repositories.vector_store import VectorStore
from src.repositories.chunk_repository import (
    create_chunks, ensure_table_exists, get_chunk_count,
    delete_by_source_type
)
from src.core.embedding import get_embedding_function
from src.utils.logger import setup_logger
from src.utils.semantic_chunker import SemanticChunker

logger = setup_logger(name="UnifiedReindex")


def _ensure_collection(vs, embedding_fn):
    """确保 ChromaDB collection 已初始化"""
    if vs.collection is None:
        vs.get_or_create_collection(embedding_function=embedding_fn)


def _reset_all(vs, embedding_fn):
    """完全重置：ChromaDB + PostgreSQL"""
    try:
        vs.client.delete_collection(vs.collection_name)
        logger.info(f"已删除 ChromaDB collection: {vs.collection_name}")
    except Exception as e:
        logger.warning(f"删除 ChromaDB collection 失败: {e}")
    vs.get_or_create_collection(embedding_function=embedding_fn)
    logger.info(f"已重建 ChromaDB collection: {vs.collection_name}")

    for st in ("guideline", "new_food", "expert_media", "gi_data"):
        deleted = delete_by_source_type(st)
        if deleted:
            logger.info(f"已清空 PostgreSQL source_type={st}: {deleted} 条")


def _build_chunk_payload(chunks, file_path, id_prefix):
    """为双写准备分块数据"""
    payload = []
    for i, c in enumerate(chunks):
        chunk_id = DocumentProcessor.generate_chunk_id(file_path, i, id_prefix)
        c["chunk_id"] = chunk_id
        c["metadata"]["chunk_id"] = chunk_id
        payload.append(c)
    return payload


def _ingest_file_both(vs, chunks, file_path, id_prefix, embedding_fn):
    """双写入库：ChromaDB + PostgreSQL"""
    if not chunks:
        return 0

    payload = _build_chunk_payload(chunks, file_path, id_prefix)
    chunk_ids = [c["chunk_id"] for c in payload]
    documents = [c["content"] for c in payload]
    metadatas = [c["metadata"] for c in payload]

    try:
        vs.add_documents(
            ids=chunk_ids,
            documents=documents,
            metadatas=metadatas,
            embedding_function=embedding_fn,
        )
    except Exception as e:
        logger.error(f"ChromaDB 写入失败 {os.path.basename(file_path)}: {e}")
        return 0

    try:
        create_chunks(payload)
    except Exception as e:
        logger.error(f"PostgreSQL 写入失败 {os.path.basename(file_path)}: {e}")

    return len(payload)


def _ingest_gi_data(vs, embedding_fn):
    """
    GI（升糖指数）数据双写入库：ChromaDB + PostgreSQL

    从 glycemic_index_facts 表读取已有数据，按食物大类聚合为语义分块，
    写入 ChromaDB（向量）和 document_chunks（全文），支持混合检索。
    """
    from src.models.db_models import GlycemicIndex
    from src.repositories.session import SessionLocal

    db = SessionLocal()
    try:
        gi_records = db.query(GlycemicIndex).order_by(GlycemicIndex.food_group, GlycemicIndex.food_name).all()
        if not gi_records:
            logger.warning("GI 数据表为空，请先运行 scripts/import_gi.py")
            return 0
    finally:
        db.close()

    # 按食物大类聚合
    groups = {}
    for rec in gi_records:
        fg = rec.food_group or "其他"
        groups.setdefault(fg, []).append(rec)

    chunker = SemanticChunker(target_size=500, min_size=200, max_size=700)
    total = 0

    for group_name, records in sorted(groups.items()):
        # 构建该大类的可读文本
        lines = [f"## {group_name} — 升糖指数（GI）数据\n"]
        for rec in records:
            gi_val = float(rec.gi_value)
            if gi_val <= 55:
                level = "低GI"
            elif gi_val <= 70:
                level = "中GI"
            else:
                level = "高GI"
            lines.append(f"- {rec.food_name}：GI值 {gi_val:.0f}（{level}）")
        text = "\n".join(lines)

        # 语义分块
        meta = {
            "source_type": "gi_data",
            "file_name": f"GI_{group_name}",
            "file_path": "gi_data",
            "guideline_title": f"{group_name}升糖指数",
            "doc_type": "gi_reference",
            "age_group": "all",
            "region": "全国",
        }
        chunks = chunker.chunk_with_headers(text, metadata=meta)
        if not chunks:
            continue

        payload = _build_chunk_payload(chunks, f"gi://{group_name}", "gi")
        chunk_ids = [c["chunk_id"] for c in payload]
        documents = [c["content"] for c in payload]
        metadatas = [c["metadata"] for c in payload]

        try:
            vs.add_documents(
                ids=chunk_ids,
                documents=documents,
                metadatas=metadatas,
                embedding_function=embedding_fn,
            )
        except Exception as e:
            logger.error(f"ChromaDB 写入 GI 数据失败 ({group_name}): {e}")
            continue

        try:
            create_chunks(payload)
        except Exception as e:
            logger.error(f"PostgreSQL 写入 GI 数据失败 ({group_name}): {e}")

        total += len(payload)
        logger.info(f"  ✅ GI/{group_name}: {len(records)} 条记录 → {len(payload)} chunks")

    logger.info(f"[gi_data] {len(groups)} 个食物大类 → {total} chunks")
    return total


def _ingest_directory(processor, vs, dir_path, source_type, id_prefix, embedding_fn):
    """遍历目录双写入库"""
    if not os.path.isdir(dir_path):
        logger.warning(f"目录不存在: {dir_path}")
        return 0

    total = 0
    file_count = 0
    for fn in sorted(os.listdir(dir_path)):
        fp = os.path.join(dir_path, fn)
        if not os.path.isfile(fp):
            continue

        ext = os.path.splitext(fn)[1].lower()
        if ext not in (".pdf", ".mp4", ".mp3", ".wav", ".m4a", ".flac", ".avi", ".mov", ".mkv", ".txt", ".md"):
            continue

        try:
            t0 = time.time()
            chunks = processor.process_file(fp, source_type=source_type)
            elapsed = time.time() - t0

            if not chunks:
                logger.info(f"  跳过（无内容）: {fn}")
                continue

            n = _ingest_file_both(vs, chunks, fp, id_prefix, embedding_fn)
            total += n
            file_count += 1
            logger.info(f"  ✅ {fn} → {n} chunks ({elapsed:.1f}s)")

        except Exception as e:
            logger.error(f"  ❌ {fn} 入库失败: {e}")

    logger.info(f"[{source_type}] {file_count} 文件 → {total} chunks")
    return total


def main():
    parser = argparse.ArgumentParser(description="统一数据入库脚本")
    parser.add_argument("--target", choices=["all", "guidelines", "new_food", "expert", "big_file", "gi_data"],
                        default="all", help="入库目标")
    parser.add_argument("--reset", action="store_true", help="清空 ChromaDB + PostgreSQL 后全量入库")
    parser.add_argument("--test", action="store_true", help="测试模式（专家媒体仅处理1个视频）")
    args = parser.parse_args()

    embedding_fn = get_embedding_function()
    vs = VectorStore()
    vs.connect()

    ensure_table_exists()
    _ensure_collection(vs, embedding_fn)

    if args.reset:
        _reset_all(vs, embedding_fn)

    processor = DocumentProcessor()

    logger.info(f"目标={args.target}, 重置={args.reset}, 测试={args.test}")

    results = {}

    # 0. GI 升糖指数数据（从 PostgreSQL 表读取）
    if args.target in ("all", "gi_data"):
        logger.info("=" * 50)
        logger.info("入库GI升糖指数数据（从 glycemic_index_facts 表读取）")
        results["gi_data"] = _ingest_gi_data(vs, embedding_fn)

    # 1. 指南 PDF
    if args.target in ("all", "guidelines"):
        guide_dir = os.path.join(settings.Authoritative_file_path, "guidelines")
        if not os.path.isdir(guide_dir):
            guide_dir = os.path.join(settings.Authoritative_file_path, "raw", "guidelines")
        logger.info("=" * 50)
        logger.info(f"入库指南PDF: {guide_dir}")
        results["guidelines"] = _ingest_directory(
            processor, vs, guide_dir, "guideline", "guide", embedding_fn
        )

    # 2. 新食品 PDF
    if args.target in ("all", "new_food"):
        new_food_dir = os.path.join(settings.Authoritative_file_path, "new_foods")
        logger.info("=" * 50)
        logger.info(f"入库新食品PDF: {new_food_dir}")
        results["new_food"] = _ingest_directory(
            processor, vs, new_food_dir, "new_food", "newfood", embedding_fn
        )

    # 3. 专家媒体视频
    if args.target in ("all", "expert"):
        academic_dir = os.path.join(settings.Authoritative_file_path, "academic")
        logger.info("=" * 50)
        logger.info(f"入库专家媒体视频: {academic_dir}")
        if args.test:
            logger.info("⚠️  测试模式：仅处理第一个视频")

        expert_total = 0
        video_count = 0
        for category in sorted(os.listdir(academic_dir)):
            cat_dir = os.path.join(academic_dir, category)
            if not os.path.isdir(cat_dir):
                continue
            logger.info(f"  分类: {category}")

            for fn in sorted(os.listdir(cat_dir)):
                fp = os.path.join(cat_dir, fn)
                if not os.path.isfile(fp):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext not in (".mp4", ".mp3", ".wav", ".m4a", ".flac", ".avi", ".mov", ".mkv"):
                    continue

                try:
                    t0 = time.time()
                    chunks = processor.process_file(
                        fp,
                        source_type="expert_media",
                        extra_metadata={
                            "expert_name": "陈伟",
                            "expert_category": category,
                            "media_type": "video" if ext in (".mp4", ".avi", ".mov", ".mkv") else "audio",
                        },
                    )
                    elapsed = time.time() - t0
                    if not chunks:
                        logger.info(f"  ⏭️ {fn} → 无内容 ({elapsed:.1f}s)")
                        continue
                    n = _ingest_file_both(vs, chunks, fp, "expert", embedding_fn)
                    expert_total += n
                    video_count += 1
                    logger.info(f"  ✅ {fn} → {n} chunks ({elapsed:.1f}s)")

                    if args.test and video_count >= 1:
                        logger.info("测试完成：仅处理了1个视频")
                        break
                except Exception as e:
                    logger.error(f"  ❌ {fn} 入库失败: {e}")

            if args.test and video_count >= 1:
                break

        results["expert_media"] = expert_total

    # 4. 大文件（中国居民膳食指南）
    if args.target in ("all", "big_file"):
        guide_dir = os.path.join(settings.Authoritative_file_path, "guidelines")
        if not os.path.isdir(guide_dir):
            guide_dir = os.path.join(settings.Authoritative_file_path, "raw", "guidelines")

        target = [f for f in os.listdir(guide_dir)
                  if "中国居民膳食指南" in f and f.endswith(".pdf")]
        if target:
            fp = os.path.join(guide_dir, target[0])
            size_mb = os.path.getsize(fp) / (1024 * 1024)
            logger.info("=" * 50)
            logger.info(f"入库大文件: {target[0]} ({size_mb:.1f}MB)")
            try:
                t0 = time.time()
                chunks = processor.process_file(fp, source_type="guideline")
                elapsed = time.time() - t0
                if chunks:
                    n = _ingest_file_both(vs, chunks, fp, "guide", embedding_fn)
                    results["big_file"] = n
                    logger.info(f"  ✅ {target[0]} → {n} chunks ({elapsed:.1f}s)")
                else:
                    logger.warning(f"  ⚠️ {target[0]} → 无内容")
                    results["big_file"] = 0
            except Exception as e:
                logger.error(f"  ❌ {target[0]} 入库失败: {e}")
                results["big_file"] = 0

    # 5. guide_catalog 摘要块
    if args.target in ("all", "guidelines"):
        try:
            from src.core.guideline_registry import build_guide_catalog_chunks
            catalog = build_guide_catalog_chunks()
            if catalog:
                cat_payload = []
                for i, c in enumerate(catalog):
                    cid = f"guide_catalog_{hashlib.md5(c['metadata']['guideline_title'].encode()).hexdigest()[:8]}"
                    c["chunk_id"] = cid
                    c["metadata"]["chunk_id"] = cid
                    cat_payload.append(c)

                vs.add_documents(
                    ids=[c["chunk_id"] for c in cat_payload],
                    documents=[c["content"] for c in cat_payload],
                    metadatas=[c["metadata"] for c in cat_payload],
                    embedding_function=embedding_fn,
                )
                try:
                    create_chunks(cat_payload)
                except Exception:
                    pass
                logger.info(f"guide_catalog → {len(catalog)} chunks")
        except Exception as e:
            logger.warning(f"guide_catalog 写入失败: {e}")

    chroma_count = vs.get_collection_stats().get("count", -1)
    pg_count = get_chunk_count()

    logger.info("=" * 50)
    logger.info(f"入库完成: {results}")
    logger.info(f"ChromaDB 总数: {chroma_count}")
    logger.info(f"PostgreSQL 总数: {pg_count}")
    logger.info("请记得清空 Redis 查询缓存！")
    logger.info("=" * 50)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()