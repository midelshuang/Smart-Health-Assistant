"""
PDF 文档加载器

职责：从 PDF 文件中提取文本内容
"""

import os
from typing import Optional, List
from src.utils.logger import setup_logger

logger = setup_logger(name="PDFLoader")


class PDFTextLoader:
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def load(self, file_path: str) -> Optional[str]:
        if not os.path.exists(file_path):
            logger.error(f"PDF 文件不存在: {file_path}")
            return None

        try:
            import fitz
            doc = fitz.open(file_path)
            page_count = len(doc)
            text_parts = []
            for page_num in range(page_count):
                page = doc[page_num]
                text_parts.append(page.get_text())
            doc.close()
            full_text = "\n".join(text_parts)
            logger.info(f"PDF 加载成功: {file_path} ({page_count} 页, {len(full_text)} 字符)")
            return full_text
        except ImportError:
            logger.error("PyMuPDF (fitz) 未安装，请运行: pip install PyMuPDF")
            return None
        except Exception as e:
            logger.error(f"PDF 加载失败 {file_path}: {e}")
            return None

    def load_and_chunk(
        self,
        file_path: str,
        metadata: Optional[dict] = None,
    ) -> List[dict]:
        text = self.load(file_path)
        if not text:
            return []

        return self._chunk(text, metadata or {})

    def _chunk(self, text: str, metadata: dict) -> List[dict]:
        chunks = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = min(start + self.chunk_size, text_len)
            chunk_text = text[start:end]

            if chunk_text.strip():
                chunk_meta = {**metadata}
                chunk_meta["char_start"] = start
                chunk_meta["char_end"] = end
                chunks.append({
                    "content": chunk_text.strip(),
                    "metadata": chunk_meta,
                })

            start += self.chunk_size - self.chunk_overlap

        logger.info(f"文本切分完成: {len(chunks)} 个块 (chunk_size={self.chunk_size}, overlap={self.chunk_overlap})")
        return chunks