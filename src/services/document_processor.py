"""
文档处理器

职责：
1. 自动识别文档类型（PDF / TXT / QA文件）
2. 调用对应加载器提取文本
3. 使用 TextCleaner 清洗文本
4. 语义分块（SemanticChunker，基于段落/句子/章节）
5. 为每个切片打上 metadata 标签（source_type 等）
6. 双写存储：ChromaDB（向量）+ PostgreSQL（全文，用于BM25）
"""

import os
import json
import hashlib
from typing import List, Dict, Any, Optional
from src.utils.logger import setup_logger
from src.utils.text_cleaner import TextCleaner
from src.utils.semantic_chunker import SemanticChunker
from src.config import settings
from src.core.loaders.pdf_loader import PDFTextLoader
from src.core.loaders.audio_loader import AudioLoader, AUDIO_EXTENSIONS, VIDEO_EXTENSIONS

logger = setup_logger(name="DocumentProcessor")


class DocumentProcessor:
    SOURCE_TYPE_GUIDE = "guideline"
    SOURCE_TYPE_NEW_FOOD = "new_food"
    SOURCE_TYPE_LITERATURE = "literature"
    SOURCE_TYPE_EXPERT = "expert_media"

    def __init__(self):
        self.pdf_loader = PDFTextLoader(
            chunk_size=settings.RAG_CHUNK_SIZE,
            chunk_overlap=settings.RAG_CHUNK_OVERLAP,
        )
        self.audio_loader = AudioLoader()
        self.chunker = SemanticChunker(
            target_size=settings.RAG_CHUNK_SIZE,
            min_size=300,
            max_size=700,
        )

    def process_file(
        self,
        file_path: str,
        source_type: str = SOURCE_TYPE_GUIDE,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        ext = self._get_extension(file_path)
        base_meta = {
            "source_type": source_type,
            "file_name": os.path.basename(file_path),
            "file_path": file_path,
        }
        if extra_metadata:
            base_meta.update(extra_metadata)

        # 注入指南注册表元数据（audience/disease_tags/age_group/region/guideline_title/doc_type）
        # 这些标签供两阶段检索的 where 过滤与重排序使用
        if source_type == "guideline":
            from src.core.guideline_registry import match_guideline
            entry = match_guideline(file_path)
            if entry:
                base_meta.update({
                    "guideline_title": entry["guideline_title"],
                    "audience": ",".join(entry["audience"]),
                    "disease_tags": ",".join(entry["disease_tags"]),
                    "region": entry["region"],
                    "age_group": ",".join(entry["age_groups"]),
                    "doc_type": "guideline_content",
                })
            else:
                # 未匹配到注册表，标记便于排查
                base_meta["doc_type"] = "guideline_content_unmapped"
                logger.warning(f"PDF 未匹配到指南注册表: {file_path}")

        logger.info(f"开始处理文件: {file_path} (类型: {ext}, 来源: {source_type})")

        if ext == ".qa" or self._is_qa_file(file_path):
            return self._process_qa_file(file_path, base_meta)
        elif ext == ".pdf":
            return self._process_pdf(file_path, base_meta)
        elif ext in (".txt", ".md", ".text"):
            return self._process_text_file(file_path, base_meta)
        elif ext in AUDIO_EXTENSIONS or ext in VIDEO_EXTENSIONS:
            # 音频文件（mp3/wav/m4a/flac...）和视频文件（mp4/avi/mov/mkv...）
            # AudioLoader 内部会自动判断类型，视频文件先提取音轨再转写
            return self._process_audio(file_path, base_meta)
        else:
            logger.warning(f"不支持的文件类型: {ext} ({file_path})")
            return []

    # 不同来源类型对应的子目录
    SOURCE_SUBDIR_MAP = {
        "guideline": "guidelines",
        "new_food": "new_foods",
        "literature": "literature",
        "expert_media": "academic",  # 专家媒体（音频/视频/文献）
    }

    def process_directory(
        self,
        dir_path: str,
        source_type: str = SOURCE_TYPE_GUIDE,
        recursive: bool = True,
    ) -> List[Dict[str, Any]]:
        if not os.path.isdir(dir_path):
            logger.error(f"目录不存在: {dir_path}")
            return []

        subdir = self.SOURCE_SUBDIR_MAP.get(source_type)
        if subdir:
            target_dir = os.path.join(dir_path, subdir)
            if not os.path.isdir(target_dir):
                logger.warning(f"子目录不存在: {target_dir}，将扫描根目录")
            else:
                dir_path = target_dir

        all_chunks = []
        logger.info(f"开始扫描目录: {dir_path} (source_type={source_type}, recursive={recursive})")

        if recursive:
            for root, dirs, files in os.walk(dir_path):
                for file_name in sorted(files):
                    file_path = os.path.join(root, file_name)
                    chunks = self.process_file(file_path, source_type)
                    all_chunks.extend(chunks)
        else:
            for file_name in sorted(os.listdir(dir_path)):
                file_path = os.path.join(dir_path, file_name)
                if os.path.isfile(file_path):
                    chunks = self.process_file(file_path, source_type)
                    all_chunks.extend(chunks)

        logger.info(f"目录处理完成: 共生成 {len(all_chunks)} 个文本块")
        return all_chunks

    def _process_pdf(self, file_path: str, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw_text = self.pdf_loader.load(file_path)
        if not raw_text:
            return []

        cleaned_text = TextCleaner.clean(raw_text)
        chunks = self._chunk_text(cleaned_text, metadata)
        return chunks

    def _process_text_file(self, file_path: str, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except Exception as e:
            logger.error(f"读取文本文件失败 {file_path}: {e}")
            return []

        cleaned_text = TextCleaner.clean(raw_text)
        chunks = self._chunk_text(cleaned_text, metadata)
        return chunks

    def _process_audio(self, file_path: str, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        处理音频/视频文件

        AudioLoader 内部会：
        1. 判断文件类型（音频直接转写，视频先提取音轨）
        2. 调用 Whisper 进行语音转文字
        3. 将转写文本切分为指定大小的块
        """
        return self.audio_loader.load_and_chunk(
            file_path,
            metadata=metadata,
            chunk_size=settings.RAG_CHUNK_SIZE,
        )

    def _process_qa_file(
        self,
        file_path: str,
        metadata: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.error(f"读取 QA 文件失败 {file_path}: {e}")
            return []

        qa_pairs = self._parse_qa_content(content)
        chunks = []
        for i, qa in enumerate(qa_pairs):
            chunk_meta = {**metadata}
            chunk_meta["qa_index"] = i
            chunk_meta["qa_type"] = "qa_pair"
            chunks.append({
                "content": qa["question"],
                "metadata": {**chunk_meta, "role": "question"},
            })
            chunks.append({
                "content": qa["answer"],
                "metadata": {**chunk_meta, "role": "answer"},
            })

        logger.info(f"QA 文件处理完成: {len(qa_pairs)} 对问答")
        return chunks

    def _chunk_text(
        self,
        text: str,
        metadata: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        return self.chunker.chunk_with_headers(text, metadata)

    @staticmethod
    def generate_chunk_id(file_path: str, index: int, prefix: str = "chunk") -> str:
        """生成分块唯一ID（用于 ChromaDB 和 PostgreSQL 双写关联）"""
        fp_hash = hashlib.md5(file_path.encode("utf-8")).hexdigest()[:12]
        return f"{prefix}_{fp_hash}_{index}"

    def _parse_qa_content(self, content: str) -> List[Dict[str, str]]:
        qa_pairs = []
        lines = content.strip().split("\n")
        current_q = None
        current_a_lines = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            if stripped.startswith("Q:") or stripped.startswith("Question:"):
                if current_q is not None:
                    qa_pairs.append({
                        "question": current_q,
                        "answer": " ".join(current_a_lines).strip(),
                    })
                current_q = stripped.split(":", 1)[-1].strip()
                current_a_lines = []
            elif stripped.startswith("A:") or stripped.startswith("Answer:"):
                current_a_lines.append(stripped.split(":", 1)[-1].strip())
            elif current_q is not None:
                current_a_lines.append(stripped)

        if current_q is not None:
            qa_pairs.append({
                "question": current_q,
                "answer": " ".join(current_a_lines).strip(),
            })

        return qa_pairs

    @staticmethod
    def _get_extension(file_path: str) -> str:
        _, ext = os.path.splitext(file_path)
        return ext.lower()

    @staticmethod
    def _is_qa_file(file_path: str) -> bool:
        ext = DocumentProcessor._get_extension(file_path)
        if ext == ".qa":
            return True
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                first_lines = [next(f) for _ in range(3)]
            content = "".join(first_lines)
            if "Q:" in content or "Question:" in content:
                return True
        except Exception:
            pass
        return False