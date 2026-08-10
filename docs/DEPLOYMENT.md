# 部署文档

> HealthAI-Platform 部署指南，覆盖开发、测试、生产三种环境。

## 1. 环境要求

| 组件 | 最低版本 | 推荐版本 | 备注 |
|------|---------|---------|------|
| Python | 3.10 | 3.11+ | LangGraph 依赖 |
| PostgreSQL | 13 | 15 | 关系型数据存储 |
| Redis | 6 | 7 | 缓存与限流 |
| ChromaDB | 0.4 | latest | 向量数据库 |
| Docker | 24.0 | latest | 容器化部署 |
| Docker Compose | v2 | v2.24+ | 多容器编排 |

硬件基线（生产环境）：
- CPU：4 核
- 内存：8 GB（含 ChromaDB 与 LLM 客户端）
- 磁盘：50 GB SSD（PostgreSQL + ChromaDB + 日志）
- 网络：100 Mbps（LLM API 出口带宽）

## 2. 一键 Docker 部署（推荐）

### 2.1 准备配置文件

```bash
cp .env.example .env
# 编辑 .env，填入实际的：
# - POSTGRES_PASSWORD（生产环境务必修改）
# - JWT_SECRET_KEY（生成随机 32+ 字符）
# - LLM_API_KEY / EMBEDDING_API_KEY
# - REDIS_PASSWORD（生产环境务必设置）
```

生成强随机密钥：
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 2.2 启动全栈服务

```bash
# 启动全部 7 个服务（DB/Chroma/Redis/App/Streamlit/Prometheus/Grafana）
docker compose --env-file .env up -d

# 查看启动状态
docker compose ps

# 等待所有 healthcheck 通过（约 60 秒）
docker compose logs -f app
```

### 2.3 健康检查验证

```bash
# FastAPI 应用健康检查
curl http://localhost:8000/api/health

# 预期响应
{"status": "healthy", "database": "connected", ...}
```

服务端口映射：
| 服务 | 端口 | 用途 |
|------|------|------|
| FastAPI | 8000 | 主 API 服务 |
| Streamlit | 8501 | 演示界面 |
| PostgreSQL | 5432 | 数据库 |
| ChromaDB | 8001 | 向量库 |
| Redis | 6379 | 缓存 |
| Prometheus | 9090 | 指标采集 |
| Grafana | 3000 | 仪表盘 |

### 2.4 初始化向量索引

首次部署需将权威指南文档入库到 ChromaDB：

```bash
# 通过 API 触发入库
curl -X POST http://localhost:8000/api/v1/rag/index \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_type": "guide"}'
```

或使用管理脚本：
```bash
python -m src.cli.init_vector_index --source raw_data/guides
```

## 3. 开发环境部署

### 3.1 创建虚拟环境

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/macOS
source venv/bin/activate

# 或使用 conda
conda create -n health_ai_env python=3.11
conda activate health_ai_env
```

### 3.2 安装依赖

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt  # 测试与开发工具
```

### 3.3 启动依赖服务（仅 DB/Redis/Chroma）

```bash
# 仅启动基础设施服务，应用本地运行便于调试
docker compose up -d db redis chroma
```

### 3.4 数据库迁移

```bash
# 应用 schema.sql
psql -h localhost -U postgres -d healthai_db -f database/init.sql
```

### 3.5 启动应用

```bash
# 开发模式（热重载）
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

# 或通过 main.py
python src/api/main.py
```

## 4. 生产环境部署

### 4.1 镜像构建优化

```dockerfile
# 多阶段构建（已在 Dockerfile 中实现）
# Stage 1: 安装依赖
# Stage 2: 运行时镜像（不包含构建工具）
```

构建生产镜像：
```bash
docker build -t healthai-platform:$(git rev-parse --short HEAD) .
docker tag healthai-platform:$(git rev-parse --short HEAD) healthai-platform:latest
```

### 4.2 生产环境配置要点

1. **必改项**：
   - `JWT_SECRET_KEY`：32+ 字符随机字符串
   - `POSTGRES_PASSWORD`：强密码
   - `REDIS_PASSWORD`：强密码
   - `LLM_API_KEY`：生产环境独立 key，设置用量上限

2. **性能调优**：
   - `APP_WORKERS=4`（CPU 核数 × 1）
   - `APP_MAX_REQUESTS=10000`（防内存泄漏后重启）
   - `RAG_TOP_K=5`（按召回率调整）

3. **安全加固**：
   - 关闭 `APP_DEBUG=false`
   - 启用 HTTPS（通过 Nginx 反向代理）
   - 限制 CORS 来源

### 4.3 Nginx 反向代理示例

```nginx
upstream healthai_app {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 443 ssl http2;
    server_name api.healthai.example.com;

    ssl_certificate /etc/ssl/certs/healthai.pem;
    ssl_certificate_key /etc/ssl/private/healthai.key;

    # 限制请求体大小（RAG 查询不超过 10KB）
    client_max_body_size 10m;

    location /api/ {
        proxy_pass http://healthai_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 流式响应必须禁用缓冲
        proxy_buffering off;
        proxy_read_timeout 300s;
    }

    # 速率限制：每 IP 每秒 10 次
    limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;

    location /api/v1/rag/ {
        limit_req zone=api_limit burst=20 nodelay;
        proxy_pass http://healthai_app;
    }
}
```

### 4.4 滚动更新

```bash
# 拉取新镜像
docker compose pull app

# 滚动重启（保持其他服务不变）
docker compose up -d --no-deps app

# 验证新版本健康
curl http://localhost:8000/api/health
```

## 5. 数据备份与恢复

### 5.1 PostgreSQL 备份

```bash
# 全量备份
docker compose exec db pg_dump -U postgres healthai_db > backup_$(date +%Y%m%d).sql

# 恢复
docker compose exec -T db psql -U postgres -d healthai_db < backup_20260101.sql
```

### 5.2 ChromaDB 备份

```bash
# 备份 ChromaDB 数据卷
docker run --rm -v healthai-platform_chroma_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/chroma_$(date +%Y%m%d).tar.gz -C /data .

# 恢复
docker run --rm -v healthai-platform_chroma_data:/data -v $(pwd):/backup \
  alpine tar xzf /backup/chroma_20260101.tar.gz -C /data
```

### 5.3 Redis 备份

```bash
# 触发 RDB 快照
docker compose exec redis redis-cli BGSAVE

# 复制 dump.rdb
docker compose cp redis:/data/dump.rdb ./redis_backup_$(date +%Y%m%d).rdb
```

### 5.4 自动化定时备份

```bash
# 添加到 crontab（每日凌晨 3 点）
0 3 * * * cd /opt/healthai && ./scripts/backup.sh >> logs/backup.log 2>&1
```

## 6. 升级流程

### 6.1 标准升级流程

1. **备份**：执行 §5 数据备份
2. **拉取代码**：`git pull origin main`
3. **构建镜像**：`docker compose build app`
4. **数据库迁移**：如有 schema 变更，先执行迁移
5. **滚动重启**：`docker compose up -d --no-deps app`
6. **健康检查**：`curl http://localhost:8000/api/health`
7. **冒烟测试**：执行一组核心 API 测试

### 6.2 回滚

```bash
# 切换到上一个镜像版本
docker compose down app
docker tag healthai-platform:<previous-hash> healthai-platform:latest
docker compose up -d --no-deps app

# 如需回滚数据库（谨慎！）
psql -h localhost -U postgres -d healthai_db < backup_YYYYMMDD.sql
```

## 7. 常见部署问题

| 现象 | 可能原因 | 解决方案 |
|------|---------|---------|
| `app` 容器 healthcheck 失败 | DB/Redis 未就绪 | 检查 `depends_on` 与 `start_period` |
| RAG 查询超时 | LLM API 慢或网络问题 | 检查 LLM_BASE_URL 连通性 |
| 向量检索返回空 | ChromaDB 未入库 | 执行 §2.3 初始化索引 |
| 内存占用持续增长 | Python 进程未释放 | 设置 `APP_MAX_REQUESTS` 定期重启 worker |
| ChromaDB 端口冲突 | 8000 被占用 | 修改 `docker-compose.yml` 中映射端口 |
