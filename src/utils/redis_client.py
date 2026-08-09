"""
Redis 客户端管理模块

提供：
1. 单例 Redis 连接（连接池复用）
2. 健康检查
3. 优雅降级（Redis 不可用时自动降级，不阻塞主流程）
4. 统一的 Key 前缀管理，避免与其它服务冲突
"""

import os
import json
import threading
from typing import Any, Optional, Dict, List
from src.utils.logger import setup_logger

logger = setup_logger(name="RedisClient")

# 单例初始化锁（防止并发创建多个 RedisClient 实例）
_init_lock = threading.Lock()


# ============================================================
# Redis 配置
# ============================================================

def _load_redis_config() -> Dict[str, Any]:
    """从环境变量加载 Redis 配置"""
    return {
        "host": os.getenv("REDIS_HOST", "localhost"),
        "port": int(os.getenv("REDIS_PORT", "6379")),
        "password": os.getenv("REDIS_PASSWORD", ""),
        "db": int(os.getenv("REDIS_DB", "0")),
        "socket_timeout": float(os.getenv("REDIS_SOCKET_TIMEOUT", "2.0")),
        "socket_connect_timeout": float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "2.0")),
        "max_connections": int(os.getenv("REDIS_MAX_CONNECTIONS", "10")),
    }


# ============================================================
# Key 前缀定义（统一命名空间管理）
# ============================================================

# 查询缓存：完整 RAG 查询结果（24小时）
KEY_PREFIX_QUERY = "rag:query"
KEY_TTL_QUERY = 24 * 3600

# 嵌入缓存：文本 → 向量（7天，嵌入不变）
KEY_PREFIX_EMBEDDING = "rag:embed"
KEY_TTL_EMBEDDING = 7 * 24 * 3600

# 食物检索缓存：SQL 查询结果（1小时）
KEY_PREFIX_FOOD_SEARCH = "rag:food"
KEY_TTL_FOOD_SEARCH = 3600

# TDEE 计算结果缓存（用户档案不变时结果相同，7天）
KEY_PREFIX_TDEE = "rag:tdee"
KEY_TTL_TDEE = 7 * 24 * 3600

# 饮食计划缓存（用户档案+目标不变时结果相同，6小时）
KEY_PREFIX_PLAN = "rag:plan"
KEY_TTL_PLAN = 6 * 3600

# 安全护栏 LLM 判断结果缓存（同查询复用，24小时）
KEY_PREFIX_SAFETY = "rag:safety"
KEY_TTL_SAFETY = 24 * 3600


def build_key(prefix: str, *parts: str) -> str:
    """
    构造带命名空间的 Redis Key

    Args:
        prefix: Key 前缀（如 KEY_PREFIX_QUERY）
        parts: Key 组成部分

    Returns:
        完整的 Redis Key 字符串
    """
    safe_parts = [str(p) for p in parts if p is not None]
    return ":".join([prefix] + safe_parts)


# ============================================================
# Redis 客户端单例
# ============================================================

class RedisClient:
    """
    Redis 客户端单例（带优雅降级 + 线程安全初始化）

    设计要点：
    1. 线程安全：双重检查锁定（DCL）避免并发下多实例问题
    2. 懒加载：首次使用时才连接，避免无 Redis 时启动失败
    3. 健康检查：ping 失败时标记不可用，所有操作降级为 no-op
    4. 连接池复用：减少连接开销
    5. JSON 序列化：自动处理 dict/list
    """

    _instance: Optional["RedisClient"] = None

    def __new__(cls):
        # ---- 双重检查锁定（Double-Checked Locking）----
        # 外层无锁检查 → 命中锁 → 内层再检查 → 真正初始化
        # 既保证线程安全，又避免每次访问都加锁的性能损耗
        if cls._instance is None:
            with _init_lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._client = None
                    inst._available: Optional[bool] = None
                    inst._config = _load_redis_config()
                    cls._instance = inst
        return cls._instance

    def _get_client(self):
        """懒加载 Redis 连接"""
        if self._client is not None:
            return self._client

        try:
            import redis
        except ImportError:
            logger.error("redis 库未安装，请执行: pip install redis")
            self._available = False
            return None

        try:
            self._client = redis.Redis(
                host=self._config["host"],
                port=self._config["port"],
                password=self._config["password"] or None,
                db=self._config["db"],
                socket_timeout=self._config["socket_timeout"],
                socket_connect_timeout=self._config["socket_connect_timeout"],
                max_connections=self._config["max_connections"],
                decode_responses=True,
            )
            logger.info(
                f"Redis 客户端初始化: {self._config['host']}:{self._config['port']}/{self._config['db']}"
            )
            return self._client
        except Exception as e:
            logger.error(f"Redis 连接初始化失败: {e}")
            self._available = False
            return None

    def is_available(self) -> bool:
        """
        检查 Redis 是否可用

        首次调用执行 ping，结果缓存；失败后每 60 秒重试一次。
        """
        import time
        if self._available is True:
            return True
        # 失败后 60 秒内不重试
        now = time.time()
        if self._available is False:
            if now - getattr(self, "_last_check_ts", 0) < 60:
                return False

        client = self._get_client()
        if client is None:
            self._available = False
            self._last_check_ts = now
            return False

        try:
            client.ping()
            self._available = True
            logger.info("Redis 连接正常")
            return True
        except Exception as e:
            self._available = False
            self._last_check_ts = now
            logger.warning(f"Redis 不可用，降级为无缓存模式: {e}")
            return False

    # ============================================================
    # 基础操作（带降级）
    # ============================================================

    def get_json(self, key: str) -> Optional[Any]:
        """读取 JSON 数据，失败/未命中返回 None"""
        if not self.is_available():
            return None
        try:
            raw = self._client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Redis get_json 失败 key={key}: {e}")
            return None

    def set_json(self, key: str, value: Any, ttl: int) -> bool:
        """写入 JSON 数据，带 TTL"""
        if not self.is_available():
            return False
        try:
            raw = json.dumps(value, ensure_ascii=False, default=str)
            self._client.set(key, raw, ex=ttl)
            return True
        except Exception as e:
            logger.warning(f"Redis set_json 失败 key={key}: {e}")
            return False

    def delete(self, *keys: str) -> int:
        """删除键，返回删除数量"""
        if not self.is_available() or not keys:
            return 0
        try:
            return self._client.delete(*keys)
        except Exception as e:
            logger.warning(f"Redis delete 失败 keys={keys}: {e}")
            return 0

    def delete_pattern(self, pattern: str) -> int:
        """按 pattern 批量删除（如清空某用户的所有缓存）"""
        if not self.is_available():
            return 0
        try:
            # scan_iter 避免大 key 阻塞
            keys = list(self._client.scan_iter(pattern, count=100))
            if keys:
                return self._client.delete(*keys)
            return 0
        except Exception as e:
            logger.warning(f"Redis delete_pattern 失败 pattern={pattern}: {e}")
            return 0

    def exists(self, key: str) -> bool:
        """判断键是否存在"""
        if not self.is_available():
            return False
        try:
            return bool(self._client.exists(key))
        except Exception as e:
            logger.warning(f"Redis exists 失败 key={key}: {e}")
            return False

    def incr(self, key: str, ttl: Optional[int] = None) -> int:
        """自增计数器（可用于限流）"""
        if not self.is_available():
            return 0
        try:
            val = self._client.incr(key)
            if ttl and val == 1:
                self._client.expire(key, ttl)
            return val
        except Exception as e:
            logger.warning(f"Redis incr 失败 key={key}: {e}")
            return 0

    def setex(self, key: str, value: Any, ttl: int) -> bool:
        """带 TTL 的字符串写入"""
        if not self.is_available():
            return False
        try:
            self._client.set(key, value, ex=ttl)
            return True
        except Exception as e:
            logger.warning(f"Redis setex 失败 key={key}: {e}")
            return False

    def get(self, key: str) -> Optional[str]:
        """读取字符串值"""
        if not self.is_available():
            return None
        try:
            return self._client.get(key)
        except Exception as e:
            logger.warning(f"Redis get 失败 key={key}: {e}")
            return None

    def ttl(self, key: str) -> int:
        """获取剩余 TTL（秒），-2 不存在，-1 无过期"""
        if not self.is_available():
            return -2
        try:
            return int(self._client.ttl(key))
        except Exception as e:
            logger.warning(f"Redis ttl 失败 key={key}: {e}")
            return -2


# ============================================================
# 全局便捷函数
# ============================================================

def get_redis_client() -> RedisClient:
    """获取 Redis 客户端单例"""
    return RedisClient()


def clear_user_cache(user_id: str) -> int:
    """
    清空指定用户的所有缓存（用于用户档案更新时）

    根据 cache_service 中的 key 构造规则：
    - query:  rag:query:{hash}:{user_id}      → pattern: rag:query:*:{user_id}
    - TDEE:   rag:tdee:{user_id}:{hash}        → pattern: rag:tdee:{user_id}:*
    - plan:   rag:plan:{user_id}:{goal}:{cal}  → pattern: rag:plan:{user_id}:*
    - safety: rag:safety:{hash}（无 user_id，不在此清理）

    Args:
        user_id: 用户 ID

    Returns:
        删除的 Key 数量
    """
    client = get_redis_client()
    if not client.is_available():
        return 0

    cleared = 0
    patterns = [
        f"{KEY_PREFIX_QUERY}:*:{user_id}",  # 查询缓存（user_id 在末尾）
        f"{KEY_PREFIX_TDEE}:{user_id}:*",   # TDEE 缓存（user_id 在前）
        f"{KEY_PREFIX_PLAN}:{user_id}:*",   # 计划缓存（user_id 在前）
    ]
    for pattern in patterns:
        cleared += client.delete_pattern(pattern)

    logger.info(f"已清空用户 {user_id} 的缓存: {cleared} 条")
    return cleared


def get_cache_stats() -> Dict[str, Any]:
    """获取 Redis 缓存统计信息"""
    client = get_redis_client()
    if not client.is_available():
        return {"available": False, "message": "Redis 不可用"}

    try:
        info = client._client.info(section="keyspace")
        return {
            "available": True,
            "host": client._config["host"],
            "port": client._config["port"],
            "db": client._config["db"],
            "keyspace": info,
        }
    except Exception as e:
        return {"available": False, "error": str(e)}
