"""
专家视频批量入库脚本

将 raw_data/raw/academic/ 下的所有音频/视频文件转写并入库到 ChromaDB。

特点：
- 断点续传：已入库的文件跳过（通过文件名查询 ChromaDB）
- 进度保存：失败列表保存到 JSON，方便后续重试
- 中文日志：实时输出进度

用法：
    python ingest_expert_media.py
"""

import sys
import os
import time
import json

# 设置 UTF-8 输出，避免 Windows GBK 编码错误
sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = __import__("io").TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.services.document_processor import DocumentProcessor
from src.repositories.vector_store import VectorStore
from src.core.embedding import get_embedding_function
from src.config import settings
from src.utils.logger import setup_logger

logger = setup_logger(name="IngestExpert")

# 专家视频目录
EXPERT_DIR = os.path.join(settings.Authoritative_file_path, "academic")
# 断点续传状态文件
PROGRESS_FILE = os.path.join(os.path.dirname(__file__), "ingest_expert_progress.json")


def load_progress() -> dict:
    """加载进度文件"""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"completed": [], "failed": []}


def save_progress(progress: dict) -> None:
    """保存进度文件"""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def list_media_files(root_dir: str) -> list:
    """递归列出所有音视频文件"""
    from src.core.loaders.audio_loader import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
    media_exts = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
    files = []
    for dirpath, _, filenames in os.walk(root_dir):
        for name in sorted(filenames):
            ext = os.path.splitext(name)[1].lower()
            if ext in media_exts:
                files.append(os.path.join(dirpath, name))
    return files


def main():
    if not os.path.isdir(EXPERT_DIR):
        logger.error(f"专家视频目录不存在: {EXPERT_DIR}")
        return

    files = list_media_files(EXPERT_DIR)
    logger.info(f"扫描到 {len(files)} 个音视频文件")

    progress = load_progress()
    completed_set = set(progress["completed"])
    logger.info(f"已完成: {len(completed_set)} 个，失败: {len(progress['failed'])} 个")

    # 连接 ChromaDB
    embedding_fn = get_embedding_function()
    vector_store = VectorStore()
    vector_store.connect()
    vector_store.get_or_create_collection(embedding_function=embedding_fn)
    collection = vector_store.collection

    processor = DocumentProcessor()

    total = len(files)
    pending = [f for f in files if f not in completed_set]
    logger.info(f"待处理: {len(pending)} 个 (跳过已完成 {total - len(pending)} 个)")

    if not pending:
        logger.info("全部已完成，无需处理")
        return

    t_start = time.time()

    for idx, file_path in enumerate(pending, 1):
        file_name = os.path.basename(file_path)
        rel_path = os.path.relpath(file_path, EXPERT_DIR)
        logger.info(f"\n[{idx}/{len(pending)}] {rel_path}")

        t0 = time.time()
        try:
            # 1. 转写 + 切分
            chunks = processor.process_file(
                file_path,
                source_type="expert_media",
                extra_metadata={
                    "expert_name": "陈伟",  # 专家姓名（与 DocumentChunk.expert_name 字段对齐）
                    "expert_category": "医学减重专家陈伟（抖音）",
                    "relative_path": rel_path,
                },
            )

            if not chunks:
                logger.warning(f"  转写结果为空，跳过")
                progress["failed"].append({"file": file_path, "reason": "empty_transcript"})
                save_progress(progress)
                continue

            # 2. 入库（避免 id 冲突，用文件名 hash + 序号）
            import hashlib
            file_hash = hashlib.md5(file_path.encode("utf-8")).hexdigest()[:8]

            ids = [f"expert_{file_hash}_{i}" for i in range(len(chunks))]
            documents = [c["content"] for c in chunks]
            metadatas = [c["metadata"] for c in chunks]

            collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )

            elapsed = time.time() - t0
            total_elapsed = time.time() - t_start
            logger.info(
                f"  完成: {len(chunks)} 块, 耗时 {elapsed:.1f}s, "
                f"累计 {total_elapsed:.0f}s, 进度 {idx}/{len(pending)}"
            )

            progress["completed"].append(file_path)
            save_progress(progress)

        except Exception as e:
            logger.error(f"  失败: {e}")
            progress["failed"].append({"file": file_path, "reason": str(e)})
            save_progress(progress)
            continue

    total_time = time.time() - t_start
    logger.info(f"\n=== 入库完成 ===")
    logger.info(f"成功: {len(progress['completed'])} 个")
    logger.info(f"失败: {len(progress['failed'])} 个")
    logger.info(f"总耗时: {total_time:.0f}s ({total_time / 60:.1f} 分钟)")

    if progress["failed"]:
        logger.info(f"\n失败列表:")
        for item in progress["failed"]:
            logger.info(f"  - {item['file']}: {item['reason']}")


if __name__ == "__main__":
    main()
