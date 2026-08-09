"""
LLM 客户端封装

提供统一的 LLM 调用接口，支持多种 LLM 提供商的切换。
当前实现：基于配置的 HTTP API 调用（兼容 OpenAI / 通义千问 / 本地模型等）。

特性：
1. 线程安全单例：双重检查锁定，避免多线程并发创建
2. 指数退避重试：使用 tenacity 对 429/5xx/网络错误自动重试
3. 超时保护：所有 HTTP 请求设置 60s 超时，避免无限阻塞
"""

from typing import List, Dict, Optional, Any
import json
import threading
import urllib.request
import urllib.error
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_result,
    before_sleep_log,
)

from src.config import settings
from src.utils.logger import setup_logger

logger = setup_logger(name="LLM_Client")

# 单例初始化锁
_llm_init_lock = threading.Lock()
# 重试策略：最多 4 次（1 + 3 次重试），指数退避 1s/2s/4s
_MAX_RETRIES = 3
_RETRY_MIN_WAIT = 1
_RETRY_MAX_WAIT = 8


def _is_empty_response(resp: Any) -> bool:
    """判断 LLM 返回是否为空（需要重试的情形）"""
    if resp is None:
        return True
    if isinstance(resp, str):
        return resp.strip() == ""
    if isinstance(resp, dict):
        content = resp.get("content", "")
        finish_reason = resp.get("finish_reason", "")
        return not content and finish_reason not in ("stop", "length")
    return False


class LLMClient:
    """
    LLM 客户端（线程安全 + 指数退避重试）

    重试策略：
    - HTTPError(429/5xx)、URLError（网络抖动）→ 指数退避重试
    - 空响应（空字符串/空content）→ 重试（避免因 LLM 偶发空输出导致质量下降）
    - 共尝试 4 次（1 次首次 + 3 次重试），间隔 1s→2s→4s
    """

    _instance: Optional["LLMClient"] = None

    def __new__(cls, api_key=None, base_url=None, model=None):
        if cls._instance is None:
            with _llm_init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        api_key: str = None,
        base_url: str = None,
        model: str = None,
    ):
        # 单例模式：只在首次初始化时设置属性
        if not hasattr(self, "_initialized"):
            self.api_key = api_key or settings.LLM_API_KEY
            self.base_url = base_url or settings.LLM_BASE_URL
            self.model = model or settings.LLM_MODEL
            self._initialized = True

    def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """
        单轮生成（用于 Query 改写等场景）

        Args:
            prompt: 完整提示词
            temperature: 采样温度
            max_tokens: 最大生成 token 数

        Returns:
            生成的文本
        """
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages, temperature, max_tokens)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """
        多轮对话（用于答案生成等场景）— 带 tenacity 指数退避重试

        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}, ...]
            temperature: 采样温度
            max_tokens: 最大生成 token 数

        Returns:
            生成的文本
        """
        if not self.api_key or not self.base_url or not self.model:
            logger.warning("LLM 配置不完整，返回空响应。请在 .env 中设置 LLM_API_KEY, LLM_BASE_URL, LLM_MODEL")
            return ""

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # ---- tenacity 装饰器写法（函数级闭包，避免类方法装饰器问题）----
        @retry(
            stop=stop_after_attempt(_MAX_RETRIES + 1),  # 1 次初始 + 3 次重试 = 共 4 次
            wait=wait_exponential(multiplier=1, min=_RETRY_MIN_WAIT, max=_RETRY_MAX_WAIT),
            retry=(
                retry_if_exception_type((urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError))
                | retry_if_result(_is_empty_response)
            ),
            before_sleep=before_sleep_log(logger, "WARNING"),
            reraise=False,
        )
        def _chat_with_retry() -> str:
            response = self._http_request(
                endpoint="/chat/completions",
                payload=payload,
            )
            choices = response.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
            return ""

        try:
            return _chat_with_retry()
        except Exception as e:
            logger.error(f"LLM chat 调用最终失败（已重试 {_MAX_RETRIES} 次）: {e}")
            return ""

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ):
        """
        流式多轮对话（逐 token yield）

        Args:
            messages: 消息列表
            temperature: 采样温度
            max_tokens: 最大生成 token 数

        Yields:
            str: 每次产出一个文本片段
        """
        if not self.api_key or not self.base_url or not self.model:
            logger.warning("LLM 配置不完整，无法流式生成")
            return

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        url = f"{self.base_url.rstrip('/')}/chat/completions"
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        from src.config.tracing import inject_trace_headers
        headers = inject_trace_headers(headers)

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=60)
            buf = b""
            # 逐块读取（4KB），高效处理 SSE 流
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buf += chunk
                # SSE: 每条消息以 \n\n 分隔
                while b"\n\n" in buf:
                    raw, buf = buf.split(b"\n\n", 1)
                    for line in raw.decode("utf-8", errors="ignore").split("\n"):
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data_str = line[len("data:"):].strip()
                        if data_str == "[DONE]":
                            return
                        try:
                            obj = json.loads(data_str)
                            choices = obj.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                        except json.JSONDecodeError:
                            continue
            # 处理 buffer 中剩余的数据
            if buf:
                for line in buf.decode("utf-8", errors="ignore").split("\n"):
                    line = line.strip()
                    if line.startswith("data:"):
                        data_str = line[len("data:"):].strip()
                        if data_str and data_str != "[DONE]":
                            try:
                                obj = json.loads(data_str)
                                choices = obj.get("choices", [])
                                if choices:
                                    content = choices[0].get("delta", {}).get("content", "")
                                    if content:
                                        yield content
                            except json.JSONDecodeError:
                                pass
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            logger.error(f"LLM 流式 API HTTP 错误 {e.code}: {error_body}")
        except urllib.error.URLError as e:
            logger.error(f"LLM 流式 API 连接错误: {e}")

    def _http_request(self, endpoint: str, payload: dict) -> dict:
        """
        发送 HTTP POST 请求到 LLM API

        Args:
            endpoint: API 端点路径
            payload: 请求体

        Returns:
            响应 JSON 字典
        """
        url = f"{self.base_url.rstrip('/')}{endpoint}"

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        from src.config.tracing import inject_trace_headers
        headers = inject_trace_headers(headers)

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            logger.error(f"LLM API HTTP 错误 {e.code}: {error_body}")
            raise
        except urllib.error.URLError as e:
            logger.error(f"LLM API 连接错误: {e}")
            raise

    def chat_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        tool_choice: str = "auto",
    ) -> Dict[str, Any]:
        """
        带 Function Calling 的多轮对话 — 带 tenacity 指数退避重试

        Args:
            messages: 消息列表
            tools: 工具定义列表（OpenAI Function Calling 格式）
            temperature: 采样温度（解析场景建议低温度 0.3）
            max_tokens: 最大生成 token 数
            tool_choice: "auto" / "none" / {"type": "function", "function": {"name": "..."}}

        Returns:
            dict: {
                "content": str,                  # LLM 回复文本（无工具调用时）
                "tool_calls": list,              # 工具调用列表
                "finish_reason": str,            # 终止原因
                "raw": dict,                     # 原始响应
            }
        """
        if not self.api_key or not self.base_url or not self.model:
            logger.warning("LLM 配置不完整，无法调用 Function Calling")
            return {"content": "", "tool_calls": [], "finish_reason": "error"}

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": tools,
            "tool_choice": tool_choice,
        }

        @retry(
            stop=stop_after_attempt(_MAX_RETRIES + 1),
            wait=wait_exponential(multiplier=1, min=_RETRY_MIN_WAIT, max=_RETRY_MAX_WAIT),
            retry=(
                retry_if_exception_type((urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError))
            ),
            before_sleep=before_sleep_log(logger, "WARNING"),
            reraise=False,
        )
        def _tools_with_retry() -> Dict[str, Any]:
            response = self._http_request(endpoint="/chat/completions", payload=payload)
            choices = response.get("choices", [])
            if not choices:
                return {"content": "", "tool_calls": [], "finish_reason": "empty"}
            msg = choices[0].get("message", {})
            finish_reason = choices[0].get("finish_reason", "stop")
            return {
                "content": msg.get("content", "") or "",
                "tool_calls": msg.get("tool_calls", []) or [],
                "finish_reason": finish_reason,
                "raw": response,
            }

        try:
            return _tools_with_retry()
        except Exception as e:
            logger.error(f"Function Calling 最终失败（已重试 {_MAX_RETRIES} 次）: {e}")
            return {"content": "", "tool_calls": [], "finish_reason": "error", "error": str(e)}


def get_llm_client() -> LLMClient:
    """
    获取全局 LLM 客户端单例（线程安全，内部走 LLMClient.__new__ DCL）

    Returns:
        LLMClient 实例
    """
    return LLMClient()