"""
重建 ChromaDB：清空 collection → 重新入库 guidelines PDF（含注册表元数据）
+ 写入 guide_catalog 摘要 chunk + （可选）重新入库 seed 的 expert_media 摘要。

用法:
    python scripts/reindex_guidelines.py [--keep-seed-expert]

前置条件：
- ChromaDB 服务已启动（CHROMA_HOST/CHROMA_PORT）
- ZhipuAI Embedding API 已配置（EMBEDDING_API_KEY 等）
- raw_data/raw/guidelines/ 下存在食养指南 PDF

注意：74MB 的《中国居民膳食指南2022》解析较慢，单文件失败会被 try/except 隔离，不中断其余入库。
入库后请清空 Redis 查询缓存（cache_service 会缓存旧结构结果）。
"""
import os
import sys
import hashlib
import argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from src.config import settings
from src.services.document_processor import DocumentProcessor
from src.repositories.vector_store import VectorStore
from src.core.embedding import get_embedding_function
from src.core.guideline_registry import build_guide_catalog_chunks
from src.utils.logger import setup_logger

logger = setup_logger(name="ReindexGuidelines")


def _reset_collection(vs: VectorStore, embedding_fn) -> None:
    """删除并重建 collection（彻底清除旧 source_type/元数据残留）。"""
    try:
        vs.client.delete_collection(vs.collection_name)
        logger.info(f"已删除旧 collection: {vs.collection_name}")
    except Exception as e:
        logger.warning(f"删除旧 collection 失败（可能不存在，忽略）: {e}")
    vs.get_or_create_collection(embedding_function=embedding_fn)


def _ingest_dir(
    processor: DocumentProcessor,
    vs: VectorStore,
    dir_path: str,
    source_type: str,
    id_prefix: str,
    embedding_fn,
) -> int:
    """遍历目录入库所有文件，返回成功写入的 chunk 数。单文件失败被隔离。"""
    if not os.path.isdir(dir_path):
        logger.warning(f"目录不存在，跳过: {dir_path}")
        return 0

    total = 0
    for root, _, files in os.walk(dir_path):
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            try:
                chunks = processor.process_file(fp, source_type=source_type)
                if not chunks:
                    logger.info(f"  跳过（无内容）: {fn}")
                    continue
                fh = hashlib.md5(fp.encode("utf-8")).hexdigest()[:8]
                ids = [f"{id_prefix}_{fh}_{i}" for i in range(len(chunks))]
                vs.add_documents(
                    ids=ids,
                    documents=[c["content"] for c in chunks],
                    metadatas=[c["metadata"] for c in chunks],
                    embedding_function=embedding_fn,
                )
                total += len(chunks)
                logger.info(f"  ✅ {fn} → {len(chunks)} chunks")
            except Exception as e:
                # 隔离单文件失败（如 74MB 大 PDF 解析/内存问题），不中断其余入库
                logger.error(f"  ❌ {fn} 入库失败（已隔离）: {e}")
    return total


def _ingest_seed_expert_media(vs: VectorStore, embedding_fn) -> int:
    """重新入库 seed_knowledge_base 中的 expert_media 摘要（专家媒体补充）。"""
    try:
        from scripts.seed_knowledge_base import SEED_DOCUMENTS, _infer_metadata
    except Exception as e:
        logger.warning(f"无法导入 seed_knowledge_base，跳过 expert_media: {e}")
        return 0

    total = 0
    for doc in SEED_DOCUMENTS:
        if doc.get("source_type") != "expert_media":
            continue
        title = doc["title"]
        content = doc["content"]
        inferred = _infer_metadata(title)
        # 按 ~500 字分段（与 seed 脚本一致）
        paragraphs = [p.strip() for p in content.split("\n") if p.strip()]
        chunks = []
        cur = ""
        for para in paragraphs:
            if len(cur) + len(para) > 500 and cur:
                chunks.append(cur)
                cur = para
            else:
                cur = cur + "\n" + para if cur else para
        if cur:
            chunks.append(cur)

        ids = [f"seed_expert_{hashlib.md5(title.encode()).hexdigest()[:8]}_{i}"
               for i in range(len(chunks))]
        metadatas = [{
            "file_name": title,
            "source_type": "expert_media",
            "media_type": doc.get("media_type", "text"),
            "chunk_index": i,
            "doc_type": "expert_media_summary",
            "age_group": inferred["age_group"],
            "region": inferred["region"],
        } for i in range(len(chunks))]

        vs.add_documents(ids=ids, documents=chunks, metadatas=metadatas,
                         embedding_function=embedding_fn)
        total += len(chunks)
        logger.info(f"  ✅ [expert_media] {title} → {len(chunks)} chunks")
    return total


def _ingest_catalog(vs: VectorStore, embedding_fn) -> int:
    """写入 guide_catalog 摘要 chunk（决策A 向量补摘要）。"""
    catalog = build_guide_catalog_chunks()
    ids = [f"guide_catalog_{hashlib.md5(c['metadata']['guideline_title'].encode()).hexdigest()[:8]}"
           for c in catalog]
    vs.add_documents(
        ids=ids,
        documents=[c["content"] for c in catalog],
        metadatas=[c["metadata"] for c in catalog],
        embedding_function=embedding_fn,
    )
    logger.info(f"  ✅ guide_catalog → {len(catalog)} chunks")
    return len(catalog)


def main(keep_seed_expert: bool = False) -> None:
    guide_dir = os.path.join(settings.Authoritative_file_path, "guidelines")
    # 兼容 raw_data/raw/guidelines 路径
    if not os.path.isdir(guide_dir):
        alt = os.path.join(settings.Authoritative_file_path, "raw", "guidelines")
        guide_dir = alt if os.path.isdir(alt) else guide_dir

    embedding_fn = get_embedding_function()
    vs = VectorStore()
    vs.connect()
    _reset_collection(vs, embedding_fn)
    processor = DocumentProcessor()

    logger.info("=" * 50)
    logger.info(f"开始重建入库: guide_dir={guide_dir}")
    logger.info("=" * 50)

    n_guide = _ingest_dir(processor, vs, guide_dir, "guideline", "guide", embedding_fn)
    n_catalog = _ingest_catalog(vs, embedding_fn)
    n_expert = _ingest_seed_expert_media(vs, embedding_fn) if keep_seed_expert else 0

    count = vs.get_collection_stats().get("count", -1)
    logger.info("=" * 50)
    logger.info(f"重建完成: guideline内容={n_guide}, catalog={n_catalog}, "
                f"expert_media={n_expert}, collection总数={count}")
    logger.info("请记得清空 Redis 查询缓存！")
    logger.info("=" * 50)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="重建 ChromaDB 指南入库")
    p.add_argument("--keep-seed-expert", action="store_true",
                   help="保留并重新入库 seed 的 expert_media 摘要")
    args = p.parse_args()
    main(keep_seed_expert=args.keep_seed_expert)
