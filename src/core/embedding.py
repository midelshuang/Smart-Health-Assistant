"""
ZhipuAI Embedding 函数

职责：
- 调用 ZhipuAI 的 Embedding API 将文本转为向量
- 兼容 ChromaDB 的 EmbeddingFunction 协议（__call__ 接口）
- 支持批量嵌入，自动分批处理以避免 API 限制
- 线程安全单例 + 零向量告警（不污染向量库）

ZhipuAI embedding-2 模型参数：
- 维度: 1024
- 最大输入: 512 tokens/条
- 最大批量: 64 条/请求（安全值）
"""

from typing import List, Optional
import json
import logging
import threading
import urllib.request
import urllib.error
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
from src.config import settings
from src.utils.logger import setup_logger

logger = setup_logger(name="EmbeddingFunction")

# 每批次最大文本数
_BATCH_SIZE = 16
# 单例初始化锁
_emb_init_lock = threading.Lock()
# 重试次数（1 + 2 = 共 3 次）
_EMB_RETRIES = 2


def _is_zero_vector(vec: List[float]) -> bool:
    """判断向量是否为全零（无效向量，不应入库）"""
    return all(v == 0.0 for v in vec)


class ZhipuAIEmbeddingFunction:
    """
    ZhipuAI Embedding 函数（兼容 ChromaDB EmbeddingFunction 协议）

    特性：
    1. 线程安全单例：DCL 双重检查锁定
    2. tenacity 指数退避重试：429/5xx/网络错误自动重试 3 次
    3. 零向量不静默：API 失败时记录 ERROR 告警并抛出 RuntimeError，
       避免零向量被写入 ChromaDB 导致"全库查询都命中同一向量"的严重事故

    使用方式：
        # 1. 作为 ChromaDB Collection 的 embedding_function
        embedding_fn = ZhipuAIEmbeddingFunction()
        collection = client.get_or_create_collection(
            name="my_collection",
            embedding_function=embedding_fn,
        )

        # 2. 直接调用获取单条文本的向量
        embedding = embedding_fn.embed_query("鸡蛋的热量是多少？")
    """

    _instance: Optional["ZhipuAIEmbeddingFunction"] = None

    def __new__(cls, api_key=None, base_url=None, model=None):
        # DCL 双重检查锁定
        if cls._instance is None:
            with _emb_init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def name(self) -> str:
        return f"ZhipuAIEmbeddingFunction_{self.model}"

    def __init__(
        self,
        api_key: str = None,
        base_url: str = None,
        model: str = None,
    ):
        if not hasattr(self, "_initialized"):
            self.api_key = api_key or settings.EMBEDDING_API_KEY
            self.base_url = base_url or settings.EMBEDDING_BASE_URL
            self.model = model or settings.EMBEDDING_MODEL
            self._initialized = True

    def __call__(self, input: List[str]) -> List[List[float]]:
        """
        ChromaDB 调用接口：接收文本列表，返回向量列表

        Args:
            input: 文本列表

        Returns:
            向量列表，每个向量是 float 列表
        """
        return self.embed_documents(input)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        批量嵌入文档文本（带 tenacity 重试 + 零向量告警 + 超长截断防御）

        自动截断：SiliconFlow bge-large-zh 单条上限约 800 中文字符（或 512 token），
        超长会返回 400 "The parameter is invalid"。这里统一截断到 700 字符防御。

        自动去空：空字符串会被 API 拒绝。

        Args:
            texts: 待嵌入的文本列表

        Returns:
            向量列表，顺序与输入一致
        """
        if not texts:
            return []

        # ---- 防御性预处理：截断超长 + 去空 ----
        MAX_CHARS_PER_TEXT = 700  # SiliconFlow bge 单条上限约 800 字，留余量
        processed_texts = []
        _truncated_count = 0
        _empty_count = 0
        for t in texts:
            if not t or not t.strip():
                _empty_count += 1
                processed_texts.append(" ")  # 用一个空格占位，保持索引对齐
                continue
            if len(t) > MAX_CHARS_PER_TEXT:
                _truncated_count += 1
                processed_texts.append(t[:MAX_CHARS_PER_TEXT])
            else:
                processed_texts.append(t)
        if _truncated_count > 0 or _empty_count > 0:
            logger.info(
                f"Embedding 预处理: 截断 {_truncated_count} 条超长文本(>{MAX_CHARS_PER_TEXT}字), "
                f"占位 {_empty_count} 条空文本"
            )

        if not self._is_configured():
            # ---- [坑点修复] 原实现静默返回零向量，会导致"所有文档向量相同 → 检索完全乱序"的灾难 ----
            # 现在改为直接抛异常，让调用方在入库阶段就感知到配置缺失，避免脏数据
            msg = (
                "Embedding API 未配置（缺失 EMBEDDING_API_KEY/BASE_URL/MODEL），"
                "无法向量化文档。请检查 .env 配置后重试。"
            )
            logger.error(msg)
            raise RuntimeError(msg)

        all_embeddings: List[List[float]] = []

        # 分批处理，避免超出 API 单次请求限制（用处理后的版本）
        for i in range(0, len(processed_texts), _BATCH_SIZE):
            batch = processed_texts[i : i + _BATCH_SIZE]
            batch_embeddings = self._request_embedding(batch)
            all_embeddings.extend(batch_embeddings)

        # ---- 零向量二次校验：避免 API 返回异常结构导致的隐性脏数据 ----
        zero_count = sum(1 for v in all_embeddings if _is_zero_vector(v))
        if zero_count > 0:
            logger.error(
                f"[严重] 检测到 {zero_count}/{len(all_embeddings)} 个零向量！"
                "这些向量不应入库，已抛异常阻断入库流程。"
            )
            raise RuntimeError(
                f"Embedding 产出 {zero_count} 个零向量，可能为 API 返回异常，"
                "已阻断入库以避免向量库污染。"
            )

        logger.info(f"Embedding 完成: {len(texts)} 条文本 → {len(all_embeddings)} 个向量")
        return all_embeddings

    def embed_query(self, text: str = None, input: str = None, **kwargs) -> List[float]:
        """
        嵌入单条查询文本（查询场景允许降级为零向量，但记录告警）

        与 embed_documents 不同：查询时若 Embedding 失败，允许返回零向量让
        流程继续（大不了本次检索质量差），但记录 ERROR 告警便于排查。
        """
        query_text = text or input or kwargs.get("input", "")
        try:
            results = self.embed_documents([query_text])
            return results[0] if results else [0.0] * settings.EMBEDDING_DIMENSION
        except RuntimeError as e:
            logger.error(
                f"查询向量化失败（降级返回零向量，本次检索可能不准）: {e}"
            )
            return [0.0] * settings.EMBEDDING_DIMENSION

    def _request_embedding(self, texts: List[str]) -> List[List[float]]:
        """
        调用 ZhipuAI Embedding API — 带 tenacity 指数退避重试

        请求格式：
            POST /embeddings
            {
                "model": "embedding-2",
                "input": ["文本1", "文本2", ...]
            }
        """
        @retry(
            stop=stop_after_attempt(_EMB_RETRIES + 1),  # 共 3 次
            wait=wait_exponential(multiplier=1, min=1, max=4),
            retry=retry_if_exception_type((
                urllib.error.HTTPError,
                urllib.error.URLError,
                json.JSONDecodeError,
                KeyError,
            )),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,  # 重试全部失败后抛出，由上层决定零向量降级
        )
        def _do_request() -> List[List[float]]:
            url = f"{self.base_url.rstrip('/')}/embeddings"
            payload = {"model": self.model, "input": texts}
            data = json.dumps(payload).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            from src.config.tracing import inject_trace_headers
            headers = inject_trace_headers(headers)
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")

            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                result = json.loads(body)

            data_list = result.get("data", [])
            if len(data_list) != len(texts):
                raise KeyError(
                    f"Embedding API 返回数量不匹配：期望 {len(texts)}，实际 {len(data_list)}"
                )
            data_list.sort(key=lambda x: x.get("index", 0))
            return [item["embedding"] for item in data_list]

        try:
            return _do_request()
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            logger.error(
                f"Embedding API HTTP 错误（已重试 {_EMB_RETRIES} 次） {e.code}: {error_body}"
            )
            raise
        except urllib.error.URLError as e:
            logger.error(f"Embedding API 连接错误（已重试 {_EMB_RETRIES} 次）: {e}")
            raise
        except Exception as e:
            logger.error(f"Embedding 请求异常（已重试 {_EMB_RETRIES} 次）: {e}")
            raise

    def _is_configured(self) -> bool:
        """检查 Embedding API 是否已正确配置"""
        return bool(self.api_key and self.base_url and self.model)


def get_embedding_function() -> ZhipuAIEmbeddingFunction:
    """
    获取全局 Embedding 函数单例（线程安全，内部走 DCL）

    Returns:
        ZhipuAIEmbeddingFunction 实例
    """
    inst = ZhipuAIEmbeddingFunction()
    if hasattr(inst, "_initialized"):
        logger.debug(
            f"Embedding 函数已就绪: model={settings.EMBEDDING_MODEL}, "
            f"dim={settings.EMBEDDING_DIMENSION}"
        )
    return inst