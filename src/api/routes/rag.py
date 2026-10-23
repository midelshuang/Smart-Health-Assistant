"""
RAG 检索增强生成 API 路由

对外暴露 RAGPipeline 的 HTTP 接口，提供：
1. POST /rag/query        —— 执行 RAG 查询（同步）
2. POST /rag/query/stream —— 执行 RAG 查询（流式）
3. POST /rag/index        —— 将文档目录入库到 ChromaDB
4. GET  /rag/status       —— 查询管道运行状态（LLM / ChromaDB 连通性）
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from fastapi.responses import StreamingResponse

from src.services.rag_service import RAGPipeline
from src.services.auth_service import get_current_user_optional, is_guest
from src.api.streaming import stream_with_heartbeat, get_sse_headers
from src.utils.logger import setup_logger

logger = setup_logger(name="RAG_Router")

router = APIRouter(prefix="/rag", tags=["RAG 检索增强"])

# ============================================================
# 数据模型定义 (Schemas)
# ============================================================

class RAGQueryRequest(BaseModel):
    """RAG 查询请求体"""
    query: str = Field(..., description="用户的自然语言问题", min_length=1, max_length=2000)
    conversation_context: Optional[List[Dict[str, str]]] = Field(
        default=None,
        description="历史对话上下文，格式 [{role: 'user'|'assistant', content: '...'}]",
    )
    stream: bool = Field(default=False, description="是否以流式方式返回结果")
    # 🆕 前端可显式传 plan_duration（覆盖意图识别器的关键词判断，更可靠）
    plan_duration: Optional[str] = Field(
        default=None,
        description="计划时长: 1_day | 7_days | 30_days（前端升级按钮直接传，避免 LLM 意图识别漏判）",
    )


class RAGQueryResponse(BaseModel):
    """RAG 查询响应体"""
    answer: str = Field(..., description="生成的答案文本")
    sources: List[Dict[str, Any]] = Field(default_factory=list, description="引用来源列表")
    answer_source: str = Field(default="knowledge_base", description="答案来源: knowledge_base | llm_generated")
    intent: str = Field(..., description="识别出的意图类型")
    success: bool = Field(default=True, description="请求是否成功")
    error: Optional[str] = Field(default=None, description="错误信息（仅在失败时返回")
    sql_results: Optional[List[Dict[str, Any]]] = Field(default=None, description="SQL 检索到的结构化数据")
    vector_results: Optional[List[Dict[str, Any]]] = Field(default=None, description="向量检索到的文档片段")
    rewrite_count: int = Field(default=0, description="Query 改写次数")
    eval_passed: Optional[bool] = Field(default=None, description="检索评估是否通过")
    plan_config: Optional[Dict[str, Any]] = Field(default=None, description="生成的饮食计划配置")
    plan_id: Optional[int] = Field(default=None, description="计划ID")
    goal_type: Optional[str] = Field(default=None, description="目标类型")
    message_uuid: Optional[str] = Field(default=None, description="消息UUID（用于反馈系统）")


class RAGIndexRequest(BaseModel):
    """文档入库请求体"""
    dir_path: Optional[str] = Field(default=None, description="文档目录路径，默认使用配置中的 Authoritative_file_path")
    source_type: str = Field(default="guideline", description="文档来源类型: guideline / literature / new_food / expert_media")


class RAGIndexResponse(BaseModel):
    """文档入库响应体"""
    success: bool = Field(..., description="入库是否成功")
    chunks_processed: int = Field(default=0, description="处理的文档块数量")
    collection_count: int = Field(default=0, description="ChromaDB Collection 中的文档总数")
    error: Optional[str] = Field(default=None, description="错误信息")


class RAGStatusResponse(BaseModel):
    """管道状态响应体"""
    graph_compiled: bool = Field(..., description="LangGraph 是否已编译")
    chromadb: bool = Field(..., description="ChromaDB 连接是否正常")
    llm: bool = Field(..., description="LLM 配置是否完整")
    config: Dict[str, Any] = Field(default_factory=dict, description="当前 RAG 配置参数")


# ============================================================
# 管道单例（全局共享，避免重复编译 LangGraph）
# ============================================================
_pipeline: Optional[RAGPipeline] = None


def get_pipeline() -> RAGPipeline:
    """获取全局 RAG 管道单例（延迟初始化）"""
    global _pipeline
    if _pipeline is None:
        logger.info("初始化 RAGPipeline 单例")
        _pipeline = RAGPipeline()
    return _pipeline


# ============================================================
# 接口实现
# ============================================================

@router.post("/query", response_model=RAGQueryResponse)
def execute_rag_query(
    body: RAGQueryRequest,
    current_user=Depends(get_current_user_optional),
):
    """
    执行 RAG 查询（同步模式）

    游客可问答（无法使用计划生成），登录用户可使用完整功能。
    """
    import uuid

    pipeline = get_pipeline()
    user_id = current_user.external_id if not is_guest(current_user) else "guest"

    try:
        result = pipeline.query(
            user_query=body.query,
            user_id=user_id,
            conversation_context=body.conversation_context,
            stream=False,
            plan_duration=body.plan_duration,  # 🆕 前端显式时长
        )

        if not result.get("success", False):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "RAG 管道执行失败"),
            )

        # 生成 message_uuid 用于反馈系统
        result["message_uuid"] = str(uuid.uuid4())

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"RAG 查询异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="服务内部错误，请稍后重试或联系管理员")


@router.post("/query/stream")
async def execute_rag_query_stream(
    body: RAGQueryRequest,
    request: Request,
    current_user=Depends(get_current_user_optional),
):
    pipeline = get_pipeline()
    user_id = current_user.external_id if not is_guest(current_user) else "guest"

    def sync_gen():
        try:
            for chunk in pipeline.query(
                user_query=body.query,
                user_id=user_id,
                conversation_context=body.conversation_context,
                stream=True,
            ):
                yield chunk
        except Exception as e:
            logger.error(f"流式 RAG 查询异常: {e}", exc_info=True)
            yield {"error": "服务内部错误，请稍后重试或联系管理员"}

    async def async_gen():
        async for sse_str in stream_with_heartbeat(
            sync_gen(), request, endpoint="rag/query/stream"
        ):
            yield sse_str

    return StreamingResponse(
        async_gen(),
        media_type="text/event-stream",
        headers=get_sse_headers(),
    )


@router.post("/chat")
async def rag_chat_stream(
    body: RAGQueryRequest,
    request: Request,
    current_user=Depends(get_current_user_optional),
):
    pipeline = get_pipeline()
    user_id = current_user.external_id if not is_guest(current_user) else "guest"

    def sync_gen():
        for event in pipeline.stream_answer(
            user_query=body.query,
            user_id=user_id,
            conversation_context=body.conversation_context,
        ):
            yield event

    async def async_gen():
        async for sse_str in stream_with_heartbeat(
            sync_gen(), request, endpoint="rag/chat"
        ):
            yield sse_str

    return StreamingResponse(
        async_gen(),
        media_type="text/event-stream",
        headers=get_sse_headers(),
    )


@router.post("/index", response_model=RAGIndexResponse)
def index_documents(
    body: RAGIndexRequest,
    current_user=Depends(get_current_user_optional),
):
    if is_guest(current_user):
        raise HTTPException(status_code=401, detail="请先登录后使用此功能")

    try:
        result = get_pipeline().index_documents(
            dir_path=body.dir_path,
            source_type=body.source_type,
        )

        if not result.get("success", False):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "文档入库失败"),
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"文档入库异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="文档入库失败，请稍后重试或联系管理员")


@router.get("/status", response_model=RAGStatusResponse)
def get_rag_status():
    """
    查询 RAG 管道运行状态

    检查项：
    - LangGraph 是否已编译
    - ChromaDB 是否可连接
    - LLM 配置是否完整
    - 当前 RAG 相关配置参数
    """
    pipeline = get_pipeline()

    try:
        status = pipeline.get_status()
        return status
    except Exception as e:
        logger.error(f"获取 RAG 状态异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="获取状态异常，请稍后重试或联系管理员")


# ============================================================
# 文档溯源：根据 chunk_id 获取原文档片段
# ============================================================

class ChunkDetailResponse(BaseModel):
    """文档片段详情响应体"""
    chunk_id: str = Field(..., description="分块唯一ID")
    content: str = Field(..., description="分块全文内容")
    file_name: str = Field(default="", description="原始文件名")
    file_path: str = Field(default="", description="原始文件路径")
    source_type: str = Field(default="", description="来源类型")
    guideline_title: str = Field(default="", description="指南标题")
    chunk_index: int = Field(default=-1, description="在原文件中的分块序号")
    char_count: int = Field(default=0, description="字符数")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="完整元数据")


@router.get("/chunk/{chunk_id}", response_model=ChunkDetailResponse)
def get_chunk_detail(chunk_id: str):
    """
    根据 chunk_id 获取原文档片段详情

    用于文档溯源：用户点击引用来源时，调用此接口查看完整的原文档片段。
    同时查询 ChromaDB 和 PostgreSQL，优先返回内容更完整的来源。
    """
    # 1. 先查 PostgreSQL（content 字段不截断）
    try:
        from src.repositories.session import SessionLocal
        from src.models.db_models import DocumentChunk

        db = SessionLocal()
        try:
            chunk = db.query(DocumentChunk).filter(
                DocumentChunk.chunk_id == chunk_id
            ).first()

            if chunk:
                return ChunkDetailResponse(
                    chunk_id=chunk.chunk_id,
                    content=chunk.content,
                    file_name=chunk.file_name or "",
                    file_path=chunk.file_path or "",
                    source_type=chunk.source_type or "",
                    guideline_title=chunk.guideline_title or "",
                    chunk_index=chunk.chunk_index if chunk.chunk_index is not None else -1,
                    char_count=chunk.char_count if chunk.char_count is not None else len(chunk.content),
                    metadata={
                        "audience": chunk.audience or "",
                        "disease_tags": chunk.disease_tags or "",
                        "region": chunk.region or "",
                        "age_group": chunk.age_group or "",
                        "doc_type": chunk.doc_type or "",
                    } if chunk.source_type == "guideline" else {},
                )
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"PostgreSQL 查询 chunk 失败: {e}")

    # 2. 回退到 ChromaDB
    try:
        from src.repositories.vector_store import VectorStore
        from src.core.embedding import get_embedding_function

        vs = VectorStore()
        vs.connect()
        embedding_fn = get_embedding_function()
        collection = vs.get_or_create_collection(embedding_function=embedding_fn)

        result = collection.get(ids=[chunk_id], include=["documents", "metadatas"])
        if result and result["ids"]:
            content = result["documents"][0] or ""
            meta = result["metadatas"][0] or {}
            return ChunkDetailResponse(
                chunk_id=chunk_id,
                content=content,
                file_name=meta.get("file_name", ""),
                file_path=meta.get("file_path", ""),
                source_type=meta.get("source_type", ""),
                guideline_title=meta.get("guideline_title", ""),
                chunk_index=meta.get("chunk_index", -1),
                char_count=len(content),
                metadata=meta,
            )
    except Exception as e:
        logger.error(f"ChromaDB 查询 chunk 失败: {e}")

    raise HTTPException(status_code=404, detail=f"未找到 chunk_id={chunk_id} 的文档片段")