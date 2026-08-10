"""
语义分块器 — 基于段落/句子/章节的智能分块

策略:
1. 按段落切分（尊重原文结构）
2. 段落按语义聚合为块（目标 400-600 字符）
3. 超长段落按句子二次切分
4. 保持标题-内容关联（将章节标题归入后续块）
5. 支持重叠窗口（相邻块共享尾部/头部内容）
"""
import re
from typing import List, Dict, Any, Optional
from src.utils.logger import setup_logger

logger = setup_logger(name="SemanticChunker")


class SemanticChunker:
    """语义分块器：基于自然边界的智能文本切分"""

    MIN_CHUNK_SIZE = 300
    MAX_CHUNK_SIZE = 700
    TARGET_CHUNK_SIZE = 500
    OVERLAP_SIZE = 80

    HEADER_PATTERN = re.compile(
        r'^(第[一二三四五六七八九十百千]+[章节部分篇]|[0-9]+[\.、)])\s',
        re.MULTILINE
    )
    SENTENCE_END = re.compile(
        r'(?<=[。！？.!?；;])\s*',
        re.DOTALL
    )

    def __init__(self, target_size: int = 500, min_size: int = 300, max_size: int = 700):
        self.TARGET_CHUNK_SIZE = target_size
        self.MIN_CHUNK_SIZE = min_size
        self.MAX_CHUNK_SIZE = max_size

    def chunk(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if not text or not text.strip():
            return []

        paragraphs = self._split_paragraphs(text)
        chunks = self._merge_paragraphs_into_chunks(paragraphs)

        result = []
        for i, chunk_text in enumerate(chunks):
            if not chunk_text.strip():
                continue
            chunk_meta = dict(metadata or {})
            chunk_meta["chunk_index"] = i
            chunk_meta["char_count"] = len(chunk_text)
            result.append({
                "content": chunk_text.strip(),
                "metadata": chunk_meta,
            })

        logger.info(f"语义分块完成: {len(text)} 字符 → {len(result)} 块")
        return result

    def _split_paragraphs(self, text: str) -> List[str]:
        text = text.replace('\r\n', '\n').replace('\r', '\n')

        raw_paragraphs = re.split(r'\n\s*\n', text)
        paragraphs = []
        for p in raw_paragraphs:
            p = p.strip()
            if not p:
                continue
            if len(p) > self.MAX_CHUNK_SIZE * 2:
                sentences = self._split_by_sentences(p)
                paragraphs.extend(sentences)
            else:
                paragraphs.append(p)

        return paragraphs

    def _split_by_sentences(self, text: str) -> List[str]:
        sentences = self.SENTENCE_END.split(text)
        result = []
        current = ""
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if len(current) + len(s) <= self.TARGET_CHUNK_SIZE:
                current = (current + " " + s).strip() if current else s
            else:
                if current:
                    result.append(current)
                if len(s) > self.MAX_CHUNK_SIZE:
                    for i in range(0, len(s), self.TARGET_CHUNK_SIZE):
                        result.append(s[i:i + self.TARGET_CHUNK_SIZE])
                else:
                    current = s
        if current:
            result.append(current)
        return result

    def _merge_paragraphs_into_chunks(self, paragraphs: List[str]) -> List[str]:
        chunks = []
        current_chunk = ""
        current_size = 0

        for para in paragraphs:
            para_size = len(para)

            if current_size == 0:
                current_chunk = para
                current_size = para_size
                continue

            if current_size + para_size <= self.TARGET_CHUNK_SIZE:
                current_chunk = current_chunk + "\n\n" + para
                current_size += para_size + 2
            elif current_size >= self.MIN_CHUNK_SIZE:
                chunks.append(current_chunk)
                current_chunk = para
                current_size = para_size
            else:
                current_chunk = current_chunk + "\n\n" + para
                current_size += para_size + 2

        if current_chunk:
            chunks.append(current_chunk)

        chunks = self._apply_overlap(chunks)
        return chunks

    def _apply_overlap(self, chunks: List[str]) -> List[str]:
        if len(chunks) <= 1 or self.OVERLAP_SIZE <= 0:
            return chunks

        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            curr = chunks[i]

            tail = prev[-self.OVERLAP_SIZE:] if len(prev) > self.OVERLAP_SIZE else prev
            if tail and len(curr) > self.OVERLAP_SIZE:
                overlap_text = f"{tail}\n{curr}"
                overlapped.append(overlap_text)
            else:
                overlapped.append(curr)

        return overlapped

    def chunk_with_headers(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """带标题感知的分块：章节标题会附加到后续块中"""
        if not text or not text.strip():
            return []

        lines = text.split('\n')
        current_header = ""
        structured_paragraphs = []

        for line in lines:
            line = line.strip()
            if not line:
                continue
            if self.HEADER_PATTERN.match(line) or (len(line) < 30 and line.endswith(('：', ':', '。')) and not line.endswith('。。')):
                current_header = line
                structured_paragraphs.append({"type": "header", "text": line})
            else:
                structured_paragraphs.append({
                    "type": "content",
                    "text": line,
                    "header": current_header,
                })

        paragraphs = []
        i = 0
        while i < len(structured_paragraphs):
            item = structured_paragraphs[i]
            if item["type"] == "header":
                header_text = item["text"]
                j = i + 1
                content_lines = []
                while j < len(structured_paragraphs) and structured_paragraphs[j]["type"] == "content":
                    content_lines.append(structured_paragraphs[j]["text"])
                    j += 1
                if content_lines:
                    para_text = header_text + "\n" + "\n".join(content_lines)
                else:
                    para_text = header_text
                paragraphs.append(para_text)
                i = j
            else:
                paragraphs.append(item["text"])
                i += 1

        chunks_text = self._merge_paragraphs_into_chunks(paragraphs)

        result = []
        for i, chunk_text in enumerate(chunks_text):
            if not chunk_text.strip():
                continue
            chunk_meta = dict(metadata or {})
            chunk_meta["chunk_index"] = i
            chunk_meta["char_count"] = len(chunk_text)
            chunk_meta["chunking_strategy"] = "semantic"
            result.append({
                "content": chunk_text.strip(),
                "metadata": chunk_meta,
            })

        logger.info(f"语义分块(带标题): {len(text)} 字符 → {len(result)} 块")
        return result