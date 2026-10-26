"""
RAG 管道主入口

对外提供统一的 query() 接口，内部通过 LangGraph 状态机执行完整的 RAG 流程：
用户提问 → 意图识别 → Query改写 → 检索（SQL/向量）→ 评估 → 答案生成

使用示例：
    from src.services.rag_service import RAGPipeline
    
    pipeline = RAGPipeline()
    result = pipeline.query("鸡蛋的热量是多少？")
    print(result["answer"])
"""

from typing import Dict, Any, List, Optional, Generator
from src.config import settings
from src.utils.logger import setup_logger

logger = setup_logger(name="RAG_Pipeline")


class RAGPipeline:
    def __init__(self):
        self._graph = None

    def _ensure_graph(self):
        """延迟初始化图（首次调用时才编译）"""
        if self._graph is None:
            from src.core.graph_builder import get_compiled_graph
            self._graph = get_compiled_graph()
            logger.info("RAG 图已编译完成")

    def query(
        self,
        user_query: str,
        user_id: str = "",
        conversation_context: Optional[List[Dict[str, str]]] = None,
        stream: bool = False,
        plan_duration: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        执行一次完整的 RAG 查询

        Args:
            user_query: 用户原始问题
            user_id: 用户标识（用于日志追踪）
            conversation_context: 历史对话上下文
            stream: 是否以流式方式返回（生成器）
            plan_duration: 🆕 前端可显式指定计划时长（1_day/7_days/30_days），
                           覆盖意图识别器的关键词判断（更可靠）

        Returns:
            包含 answer, sources, intent 等字段的结果字典
        """
        self._ensure_graph()

        initial_state = {
            "original_query": user_query,
            "user_id": user_id,
            "conversation_context": conversation_context or [],
            "rewrite_count": 0,
            "max_rewrite_count": settings.RAG_MAX_REWRITE,
            "intent": "unknown",
        }

        # 🆕 前端显式传的 plan_duration 放进 state，让意图识别器跳过关键词识别
        if plan_duration and plan_duration in ("1_day", "7_days", "30_days"):
            initial_state["plan_duration"] = plan_duration
            initial_state["intent"] = "plan_generation"
            logger.info(f"前端显式指定 plan_duration={plan_duration}，跳过意图识别关键词判断")

        # 加载用户反馈偏好（注入风格指令）
        if user_id and user_id != "guest":
            try:
                from src.services.feedback_service import get_style_directive_for_answer, get_style_directive_for_plan
                from src.repositories.session import SessionLocal
                db = SessionLocal()
                try:
                    initial_state["feedback_style_directive"] = get_style_directive_for_answer(
                        db, user_id)
                    initial_state["plan_style_directive"] = get_style_directive_for_plan(
                        db, user_id)
                finally:
                    db.close()
            except Exception as e:
                logger.warning(f"加载反馈偏好失败（降级为无调整）: {e}")
                initial_state["feedback_style_directive"] = ""
                initial_state["plan_style_directive"] = ""
        else:
            initial_state["feedback_style_directive"] = ""
            initial_state["plan_style_directive"] = ""

        logger.info(f"开始 RAG 查询: query='{user_query}', user_id={user_id}")

        try:
            if stream:
                return self._stream_query(initial_state)
            else:
                return self._invoke_query(initial_state)
        except Exception as e:
            logger.error(f"RAG 查询异常: {e}")
            return {
                "answer": f"抱歉，系统处理您的问题时出现错误：{e}",
                "sources": [],
                "intent": "unknown",
                "success": False,
                "error": str(e),
            }

    def _invoke_query(self, initial_state: Dict[str, Any]) -> Dict[str, Any]:
        """同步调用：一次性返回完整结果"""
        user_query = initial_state.get("original_query", "")
        user_id = initial_state.get("user_id", "")
        conversation_context = initial_state.get(
            "conversation_context", []) or []
        has_context = len(conversation_context) > 0

        # 🆕 多轮对话（有上下文）时跳过缓存，因为相同查询文本在不同上下文中含义不同
        if not has_context:
            try:
                from src.services.cache_service import get_cached_query, set_cached_query
                cached = get_cached_query(user_query, user_id)
                if cached:
                    logger.info(f"查询缓存命中，跳过 RAG 流程: query='{user_query[:30]}'")
                    return cached
            except Exception as e:
                logger.warning(f"读取查询缓存异常（降级为无缓存）: {e}")

        # 缓存未命中（或有上下文跳过缓存），执行完整 RAG 流程
        result = self._graph.invoke(initial_state)

        response = {
            "answer": result.get("generated_answer", ""),
            "sources": result.get("sources", []),
            "answer_source": result.get("answer_source", "knowledge_base"),
            "intent": result.get("intent", "unknown"),
            "sql_results": result.get("sql_results", []),
            "vector_results": result.get("vector_results", []),
            "rewrite_count": result.get("rewrite_count", 0),
            "eval_passed": result.get("eval_passed", False),
            "eval_feedback": result.get("eval_feedback", ""),
            "plan_config": result.get("plan_config") or None,
            "plan_id": result.get("plan_id"),
            "goal_type": result.get("goal_type"),
            "plan_duration": result.get("plan_duration"),
            "success": True,
            "error": result.get("error", ""),
        }

        # 🆕 仅在无上下文时写入缓存（多轮对话结果依赖上下文，不宜缓存）
        # 计划生成结果不缓存（plan_id 依赖用户登录状态，缓存会导致 plan_id 丢失）
        # R02：安全拦截结果不缓存（intent=blocked），否则规则热更新后
        # 旧的拦截/放行结果仍会绕过安全节点直接返回
        if (not has_context and not response.get("plan_config")
                and response.get("intent") != "blocked"):
            try:
                from src.services.cache_service import set_cached_query
                set_cached_query(user_query, response, user_id)
            except Exception as e:
                logger.warning(f"写入查询缓存异常: {e}")

        return response

    def stream_answer(
        self,
        user_query: str,
        user_id: str = "",
        conversation_context: Optional[List[Dict[str, str]]] = None,
    ) -> Generator:
        """
        流式 RAG 查询：先同步完成意图识别+检索，再逐 token 流式生成答案

        Yields:
            dict: 分为三类事件
              - {"type": "sources", "data": [...]}     检索到的来源
              - {"type": "content", "data": "片段"}    答案文本片段
              - {"type": "done", "data": {...}}       完整结果（含完整答案）
        """
        self._ensure_graph()

        initial_state = {
            "original_query": user_query,
            "user_id": user_id,
            "conversation_context": conversation_context or [],
            "rewrite_count": 0,
            "max_rewrite_count": settings.RAG_MAX_REWRITE,
            "intent": "unknown",
            "stream_mode": True,
        }

        # 加载用户反馈偏好
        if user_id and user_id != "guest":
            try:
                from src.services.feedback_service import get_style_directive_for_answer, get_style_directive_for_plan
                from src.repositories.session import SessionLocal
                db = SessionLocal()
                try:
                    initial_state["feedback_style_directive"] = get_style_directive_for_answer(
                        db, user_id)
                    initial_state["plan_style_directive"] = get_style_directive_for_plan(
                        db, user_id)
                finally:
                    db.close()
            except Exception:
                initial_state["feedback_style_directive"] = ""
                initial_state["plan_style_directive"] = ""
        else:
            initial_state["feedback_style_directive"] = ""
            initial_state["plan_style_directive"] = ""

        logger.info(f"开始流式 RAG 查询: query='{user_query}', user_id={user_id}")

        try:
            # 1. 同步执行 RAG 流程（stream_mode=True 时 generator_node 跳过 LLM 调用）
            from src.core.nodes import SYSTEM_PROMPT_KB
            result = self._graph.invoke(initial_state)

            # 🆕 R03 安全终态优先：被安全护栏拦截的查询直接返回拒绝文案，
            #    绝不进入流式模型生成（否则流式路径会绕过非流式路径的安全检查）
            if result.get("safety_blocked", False):
                refusal = result.get("generated_answer") or (
                    "抱歉，您的提问涉及医疗安全风险，我无法提供回答。"
                    "如有健康问题请咨询医生或临床营养师。"
                )
                logger.warning(
                    "流式路径命中安全拦截，直接返回拒绝文案: category=%s",
                    result.get("safety_category", "unknown"),
                )
                yield {"type": "sources", "data": result.get("sources", [])}
                yield {"type": "content", "data": refusal}
                yield {
                    "type": "done",
                    "data": {
                        "answer": refusal,
                        "sources": [],
                        "answer_source": "safety_guard",
                        "intent": "blocked",
                        "safety_blocked": True,
                        "success": True,
                    },
                }
                return

            sources = result.get("sources", [])
            prompt = result.get("_stream_prompt", "")
            system_prompt = result.get(
                "_stream_system_prompt", SYSTEM_PROMPT_KB)
            answer_source = result.get("answer_source", "knowledge_base")

            yield {"type": "sources", "data": sources}
            yield {"type": "answer_source", "data": answer_source}

            # 2. 流式生成答案
            from src.core.llm_client import get_llm_client
            llm_client = get_llm_client()

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]

            full_answer = ""
            for token in llm_client.stream_chat(
                messages, temperature=0.7, max_tokens=settings.LLM_MAX_TOKENS
            ):
                full_answer += token
                yield {"type": "content", "data": token}

            logger.info(
                f"流式答案生成完成: 来源={answer_source}, 长度={len(full_answer)} 字符")

            yield {
                "type": "done",
                "data": {
                    "answer": full_answer,
                    "sources": sources,
                    "answer_source": answer_source,
                    "intent": result.get("intent", "unknown"),
                    "success": True,
                },
            }

        except Exception as e:
            logger.error(f"流式 RAG 查询异常: {e}")
            yield {
                "type": "error",
                "data": {"error": str(e)},
            }

    def _stream_query(self, initial_state: Dict[str, Any]) -> Generator:
        """流式调用：逐步产出各节点的执行结果"""
        for event in self._graph.stream(initial_state):
            for node_name, node_output in event.items():
                logger.debug(f"[Stream] 节点 {node_name} 完成")
                yield {
                    "node": node_name,
                    "output": node_output,
                }

    def index_documents(
        self,
        dir_path: str = None,
        source_type: str = "guideline",
    ) -> Dict[str, Any]:
        """
        将文档目录入库到 ChromaDB（向量化 + 存储）

        流程：
        1. 扫描目录 → 按文件类型加载（PDF/TXT/QA/音频/视频）
        2. 文本清洗 → 智能切分
        3. 调用 ZhipuAI Embedding API 向量化
        4. 写入 ChromaDB（含 metadata 标签）

        Args:
            dir_path: 文档目录路径（默认使用配置中的 Authoritative_file_path）
            source_type: 文档来源类型（guideline / literature / new_food / expert_media）

        Returns:
            入库结果统计
        """
        from src.services.document_processor import DocumentProcessor
        from src.repositories.vector_store import VectorStore
        from src.core.embedding import get_embedding_function

        target_dir = dir_path or settings.Authoritative_file_path
        if not target_dir:
            return {"success": False, "error": "未指定文档目录"}

        logger.info(f"开始文档入库: dir={target_dir}, source_type={source_type}")

        # 1. 处理文档：加载 + 清洗 + 切分
        processor = DocumentProcessor()
        chunks = processor.process_directory(
            target_dir, source_type=source_type)

        if not chunks:
            return {"success": False, "error": "未处理到任何文档块"}

        # 2. 获取 ZhipuAI Embedding 函数（供 ChromaDB 自动向量化）
        embedding_fn = get_embedding_function()

        # 3. 连接 ChromaDB，创建/获取 Collection
        vector_store = VectorStore()
        vector_store.connect()
        vector_store.get_or_create_collection(embedding_function=embedding_fn)

        # 4. 写入文档（ChromaDB 会自动调用 embedding_fn 生成向量）
        ids = []
        documents = []
        metadatas = []

        for i, chunk in enumerate(chunks):
            chunk_id = f"{source_type}_{i}"
            ids.append(chunk_id)
            documents.append(chunk["content"])
            chunk_meta = chunk.get("metadata", {})
            chunk_meta["source_type"] = source_type
            metadatas.append(chunk_meta)

        vector_store.add_documents(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embedding_function=embedding_fn,
        )

        stats = vector_store.get_collection_stats()
        logger.info(
            f"文档入库完成: {len(chunks)} 个块, Collection 总数: {stats.get('count', 0)}")

        return {
            "success": True,
            "chunks_processed": len(chunks),
            "collection_count": stats.get("count", 0),
        }

    def get_status(self) -> Dict[str, Any]:
        """
        获取管道状态（检查依赖服务是否可用）

        检查项：
        - LangGraph 是否已编译
        - ChromaDB 是否可连接
        - LLM 配置是否完整
        - Embedding 配置是否完整
        - Whisper 是否可用
        - Redis 缓存是否可用

        Returns:
            状态信息字典
        """
        status = {
            "graph_compiled": self._graph is not None,
            "chromadb": self._check_chromadb(),
            "llm": self._check_llm(),
            "embedding": self._check_embedding(),
            "whisper": self._check_whisper(),
            "redis": self._check_redis(),
            "config": {
                "llm_model": settings.LLM_MODEL,
                "embedding_model": settings.EMBEDDING_MODEL,
                "embedding_dimension": settings.EMBEDDING_DIMENSION,
                "chroma_host": settings.CHROMA_HOST,
                "chroma_port": settings.CHROMA_PORT,
                "rag_top_k": settings.RAG_TOP_K,
                "whisper_model": settings.WHISPER_MODEL,
                "redis_host": settings.REDIS_HOST,
                "redis_port": settings.REDIS_PORT,
            },
        }
        return status

    def _check_redis(self) -> bool:
        """检查 Redis 是否可用"""
        try:
            from src.utils.redis_client import get_redis_client
            return get_redis_client().is_available()
        except Exception:
            return False

    def _check_chromadb(self) -> bool:
        try:
            from src.repositories.vector_store import VectorStore
            vs = VectorStore()
            vs.connect()
            return True
        except Exception:
            return False

    def _check_llm(self) -> bool:
        if not settings.LLM_API_KEY or not settings.LLM_BASE_URL:
            return False
        try:
            from src.core.llm_client import get_llm_client
            client = get_llm_client()
            return bool(client.api_key and client.base_url and client.model)
        except Exception:
            return False

    def _check_embedding(self) -> bool:
        """检查 Embedding API 配置是否完整"""
        return bool(
            settings.EMBEDDING_API_KEY
            and settings.EMBEDDING_BASE_URL
            and settings.EMBEDDING_MODEL
        )

    def _check_whisper(self) -> bool:
        """检查 Whisper 是否可用（检测是否为 OpenAI Whisper 而非 Graphite 同名包）"""
        try:
            import whisper
            if hasattr(whisper, "load_model"):
                return True
            return False
        except ImportError:
            return False


if __name__ == "__main__":

    pipeline = RAGPipeline()
    result = pipeline.query("鸡蛋的热量是多少？")
    print(result["answer"])
