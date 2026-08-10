"""
ChromaDB 向量存储封装

职责：
- 连接 ChromaDB 服务
- 创建/获取 Collection（支持 metadata 过滤）
- 文档写入（含 embedding 和 metadata）
- 相似度检索（带阈值和数量限制）
"""

from typing import List, Dict, Any, Optional, Callable
import chromadb
from chromadb.config import Settings

# Monkey-patch: 绕过 chromadb 1.5.x HttpClient 的 auth identity 检查
# 当 ChromaDB 服务器未启用认证时，get_user_identity 会抛 ValueError
try:
    from chromadb.api.client import Client as _Client, UserIdentity as _UserIdentity
    _orig_get_user_identity = _Client.get_user_identity

    def _safe_get_user_identity(self):
        try:
            return _orig_get_user_identity(self)
        except Exception:
            return _UserIdentity(subject=None, tenants=[], databases=[])

    _Client.get_user_identity = _safe_get_user_identity
except Exception:
    pass

from src.config import settings
from src.utils.logger import setup_logger

logger = setup_logger(name="VectorStore")


class VectorStore:
    def __init__(
        self,
        host: str = None,
        port: int = None,
        collection_name: str = None,
    ):
        self.host = host or settings.CHROMA_HOST
        self.port = port or settings.CHROMA_PORT
        self.collection_name = collection_name or settings.CHROMA_COLLECTION_NAME
        self.client = None
        self.collection = None

    def connect(self) -> None:
        try:
            self.client = chromadb.HttpClient(
                host=self.host,
                port=self.port,
                settings=Settings(anonymized_telemetry=False),
            )
            heartbeat = self.client.heartbeat()
            logger.info(f"ChromaDB 连接成功: {heartbeat}")
        except Exception as e:
            logger.error(f"ChromaDB 连接失败: {e}")
            raise

    def get_or_create_collection(
        self,
        embedding_function: Optional[Callable] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        if self.client is None:
            self.connect()

        try:
            self.collection = self.client.get_collection(
                name=self.collection_name,
                embedding_function=embedding_function,
            )
            logger.info(f"获取已存在的 Collection: {self.collection_name}")
        except Exception:
            logger.info(f"Collection {self.collection_name} 不存在，正在创建...")
            try:
                self.collection = self.client.create_collection(
                    name=self.collection_name,
                    embedding_function=embedding_function,
                    metadata=metadata or {"hnsw:space": "cosine"},
                )
                logger.info(f"Collection {self.collection_name} 创建成功")
            except Exception as create_err:
                if "already exists" in str(create_err).lower():
                    logger.info(f"Collection {self.collection_name} 已被其他线程创建，直接获取")
                    self.collection = self.client.get_collection(
                        name=self.collection_name,
                        embedding_function=embedding_function,
                    )
                else:
                    raise

        return self.collection

    def add_documents(
        self,
        ids: List[str],
        documents: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        embeddings: Optional[List[List[float]]] = None,
        embedding_function=None,
    ) -> None:
        if self.collection is None:
            raise RuntimeError("Collection 未初始化，请先调用 get_or_create_collection()")

        if len(ids) != len(documents):
            raise ValueError("ids 和 documents 长度必须一致")

        try:
            kwargs = {
                "ids": ids,
                "documents": documents,
            }
            if metadatas:
                kwargs["metadatas"] = metadatas

            if embeddings:
                kwargs["embeddings"] = embeddings
            else:
                if embedding_function is None:
                    from src.core.embedding import get_embedding_function
                    embedding_function = get_embedding_function()
                kwargs["embeddings"] = embedding_function.embed_documents(documents)

            self.collection.add(**kwargs)
            logger.info(f"成功写入 {len(ids)} 条文档到 Collection {self.collection_name}")
        except Exception as e:
            logger.error(f"文档写入失败: {e}")
            raise

    def similarity_search(
        self,
        query_embedding: Optional[List[float]] = None,
        query_text: Optional[str] = None,
        top_k: int = None,
        threshold: Optional[float] = None,
        where: Optional[Dict[str, Any]] = None,
        where_document: Optional[Dict[str, Any]] = None,
        embedding_function=None,
    ) -> List[Dict[str, Any]]:
        if self.collection is None:
            raise RuntimeError("Collection 未初始化")

        top_k = top_k or settings.RAG_TOP_K

        try:
            kwargs: Dict[str, Any] = {"n_results": top_k}

            if query_embedding:
                kwargs["query_embeddings"] = [query_embedding]
            elif query_text:
                if embedding_function is None:
                    from src.core.embedding import get_embedding_function
                    embedding_function = get_embedding_function()
                query_emb = embedding_function.embed_query(query_text)
                kwargs["query_embeddings"] = [query_emb]
            else:
                raise ValueError("必须提供 query_embedding 或 query_text 之一")

            if where:
                kwargs["where"] = where
            if where_document:
                kwargs["where_document"] = where_document

            results = self.collection.query(**kwargs)

            documents = results.get("documents", [[]])[0]
            distances = results.get("distances", [[]])[0]
            ids = results.get("ids", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]

            retrieved = []
            for i, doc in enumerate(documents):
                distance = distances[i] if i < len(distances) else None
                similarity = 1.0 - distance if distance is not None else None

                if threshold is not None and similarity is not None:
                    if similarity < threshold:
                        continue

                retrieved.append({
                    "id": ids[i] if i < len(ids) else "",
                    "content": doc,
                    "score": round(similarity, 4) if similarity is not None else 0.0,
                    "metadata": metadatas[i] if i < len(metadatas) else {},
                })

            logger.info(f"向量检索完成: 返回 {len(retrieved)} 条结果 (top_k={top_k})")
            return retrieved

        except Exception as e:
            logger.error(f"向量检索失败: {e}")
            return []

    def delete_documents(self, ids: List[str]) -> None:
        if self.collection is None:
            raise RuntimeError("Collection 未初始化")

        try:
            self.collection.delete(ids=ids)
            logger.info(f"成功删除 {len(ids)} 条文档")
        except Exception as e:
            logger.error(f"文档删除失败: {e}")
            raise

    def get_collection_stats(self) -> Dict[str, Any]:
        if self.collection is None:
            return {"exists": False, "count": 0}

        try:
            count = self.collection.count()
            return {
                "exists": True,
                "name": self.collection_name,
                "count": count,
            }
        except Exception as e:
            logger.error(f"获取 Collection 统计失败: {e}")
            return {"exists": True, "count": -1, "error": str(e)}