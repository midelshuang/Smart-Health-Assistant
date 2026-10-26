# ============================================================
# HealthAI Platform - 应用镜像 Dockerfile
# 多阶段构建：依赖安装 → 运行时镜像
# ============================================================

# ---------- 阶段 1：依赖安装 ----------
FROM python:3.11-slim AS builder

# 设置 pip 镜像加速（可选，按需启用）
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# 系统依赖（psycopg2 编译所需）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖清单，利用 Docker 缓存层
COPY requirements.txt .

# 安装依赖到独立目录（便于多阶段复制）
RUN pip install --prefix=/install -r requirements.txt


# ---------- 阶段 2：运行时镜像 ----------
FROM python:3.11-slim AS runtime

# 元数据
LABEL maintainer="HealthAI Platform" \
      version="1.4.0" \
      description="HealthAI Platform - 健康AI对话与个性化饮食推荐"

# 环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    APP_ENV=production

# 安装运行时所需的系统库
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户（安全最佳实践）
RUN groupadd -r healthai && useradd -r -g healthai -d /app healthai

WORKDIR /app

# 复制依赖（从 builder 阶段）
# R06 修复：pip --prefix 的安装布局是 lib/pythonX.Y/site-packages，
# 原来直接 COPY /install 会导致 site-packages 路径错位、依赖全部丢失；
# uvicorn 等控制台脚本在 /install/bin，必须一并复制否则 CMD 启动失败
COPY --from=builder /install/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /install/bin /usr/local/bin

# 复制应用代码
COPY --chown=healthai:healthai . /app/

# 切换到非 root 用户
USER healthai

# 暴露端口
EXPOSE 8000

# 健康检查（应用自身的 /api/health 接口）
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

# 启动命令
CMD ["uvicorn", "web_api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
