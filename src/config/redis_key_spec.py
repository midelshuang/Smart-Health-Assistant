"""
Redis Key 命名规范（V4 多模块共享）

所有模块共享 Redis 时必须遵循此命名规范，避免键冲突。
当前版本（V1/V2）的 RAG/auth/plan 等模块的键也应迁移到此规范。

## 命名格式

    healthai:{env}:{module}:{resource}:{key}

各部分说明：
- env:      环境标识（dev/staging/prod），通过 APP_ENV 推导
- module:   业务模块（rag/auth/plan/social/expert/content/admin）
- resource: 资源类型（cache/lock/session/queue/counter/ratelimit）
- key:      具体键（用户 ID / 查询哈希 / 业务标识）

## 示例

    healthai:prod:rag:cache:query:a3f5b8...           # RAG 查询结果缓存
    healthai:prod:auth:session:u123                   # 用户会话
    healthai:prod:auth:ratelimit:login:192.168.1.1    # 登录限流计数
    healthai:prod:plan:lock:generate:u123             # 计划生成分布式锁
    healthai:prod:social:counter:followers:u123        # 粉丝数（V4）
    healthai:prod:expert:cache:profile:e456           # 专家档案缓存（V4）

## TTL 建议

| 资源类型 | 建议 TTL | 说明 |
|---------|---------|------|
| cache   | 5min - 1h | 业务数据缓存，根据时效性调整 |
| session | 24h      | 用户会话，与 JWT 过期时间一致 |
| lock    | 30s      | 分布式锁，防止任务重复执行 |
| ratelimit | 60s    | 限流计数，按窗口大小 |
| counter | 永久     | 计数器（粉丝数等），由业务管理 |
| queue   | 24h      | 任务队列，超时自动清理 |
"""

import os

from src.utils.logger import setup_logger

logger = setup_logger(name="RedisKeySpec")


def _get_env_prefix() -> str:
    """获取环境前缀（dev/staging/prod）

    优先读取环境变量 APP_ENV，未设置时回退到 dev。
    """
    env = os.getenv("APP_ENV", "development").lower()
    if env in ("prod", "production"):
        return "prod"
    if env in ("staging", "stage"):
        return "staging"
    return "dev"


def build_key(module: str, resource: str, key: str) -> str:
    """
    构造标准 Redis Key

    Args:
        module: 业务模块名（rag/auth/plan/social/expert/content/admin）
        resource: 资源类型（cache/lock/session/queue/counter/ratelimit）
        key: 具体键

    Returns:
        标准化的 Redis Key，例: "healthai:prod:rag:cache:query:abc123"
    """
    if not module or not resource or not key:
        raise ValueError("module/resource/key 不能为空")

    env = _get_env_prefix()
    return f"healthai:{env}:{module}:{resource}:{key}"


# ============================================================
# 各模块 Key 构造便捷函数
# ============================================================

def rag_cache_key(query_hash: str) -> str:
    """RAG 查询结果缓存"""
    return build_key("rag", "cache", f"query:{query_hash}")


def rag_lock_key(user_id: str) -> str:
    """RAG 查询分布式锁（防止同一用户并发查询冲突）"""
    return build_key("rag", "lock", f"query:{user_id}")


def auth_session_key(user_id: str) -> str:
    """用户会话"""
    return build_key("auth", "session", user_id)


def auth_ratelimit_login_key(ip: str) -> str:
    """登录限流计数（按 IP）"""
    return build_key("auth", "ratelimit", f"login:{ip}")


def auth_ratelimit_query_key(user_id: str) -> str:
    """RAG 查询限流计数（按用户）"""
    return build_key("auth", "ratelimit", f"query:{user_id}")


def plan_lock_key(user_id: str) -> str:
    """计划生成分布式锁"""
    return build_key("plan", "lock", f"generate:{user_id}")


def plan_cache_key(plan_id: str) -> str:
    """计划缓存"""
    return build_key("plan", "cache", f"plan:{plan_id}")


# ============================================================
# V4 预留：社交/专家模块 Key
# ============================================================

def social_counter_followers_key(user_id: str) -> str:
    """粉丝数计数器（V4）"""
    return build_key("social", "counter", f"followers:{user_id}")


def social_counter_following_key(user_id: str) -> str:
    """关注数计数器（V4）"""
    return build_key("social", "counter", f"following:{user_id}")


def social_cache_feed_key(user_id: str) -> str:
    """用户动态流缓存（V4）"""
    return build_key("social", "cache", f"feed:{user_id}")


def expert_cache_profile_key(expert_id: str) -> str:
    """专家档案缓存（V4）"""
    return build_key("expert", "cache", f"profile:{expert_id}")


def expert_lock_consultation_key(consultation_id: str) -> str:
    """专家咨询分布式锁（V4）"""
    return build_key("expert", "lock", f"consultation:{consultation_id}")


# ============================================================
# 管理工具
# ============================================================

def get_namespace_pattern(module: str = "*", resource: str = "*") -> str:
    """
    获取匹配命名空间模式（用于 SCAN 查找一批 Key）

    Args:
        module: 模块名，* 匹配全部
        resource: 资源类型，* 匹配全部

    Returns:
        匹配模式，例: "healthai:prod:rag:*"
    """
    env = _get_env_prefix()
    return f"healthai:{env}:{module}:{resource}:*"


def is_valid_key(key: str) -> bool:
    """校验 Key 是否符合命名规范"""
    if not key or not key.startswith("healthai:"):
        return False
    parts = key.split(":")
    # 至少 5 段：healthai:env:module:resource:key
    return len(parts) >= 5
