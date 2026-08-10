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


class RAGQueryResponse(BaseModel):
    """RAG 查询响应体"""
    answer: str = Field(..., description="生成的答案文本")
    sources: List[Dict[str, str]] = Field(default_factory=list, description="引用来源列表")
    answer_source: str = Field(default="knowledge_base", description="答案来源: knowledge_base | llm_generated")
    intent: str = Field(..., description="识别出的意图类型")
    success: bool = Field(default=True, description="请求是否成功")
    error: Optional[str] = Field(default=None, description="错误信息（仅在失败时返回")
    sql_results: Optional[List[Dict[str, Any]]] = Field(default=None, description="SQL 检索到的结构化数据")
    vector_results: Optional[List[Dict[str, Any]]] = Field(default=None, description="向量检索到的文档片段")
    rewrite_count: int = Field(default=0, description="Query 改写次数")
    eval_passed: Optional[bool] = Field(default=None, description="检索评估是否通过")


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
    pipeline = get_pipeline()
    user_id = current_user.external_id if not is_guest(current_user) else "guest"

    try:
        result = pipeline.query(
            user_query=body.query,
            user_id=user_id,
            conversation_context=body.conversation_context,
            stream=False,
        )

        if not result.get("success", False):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "RAG 管道执行失败"),
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"RAG 查询异常: {e}")
        raise HTTPException(status_code=500, detail=f"RAG 管道执行异常: {e}")


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
            logger.error(f"流式 RAG 查询异常: {e}")
            yield {"error": str(e)}

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
        logger.error(f"文档入库异常: {e}")
        raise HTTPException(status_code=500, detail=f"文档入库异常: {e}")


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
        logger.error(f"获取 RAG 状态异常: {e}")
        raise HTTPException(status_code=500, detail=f"获取状态异常: {e}")