# 运维手册

> 面向运维人员的日常运维、监控、故障排查指南。

## 1. 日常运维

### 1.1 服务状态检查

```bash
# 全部服务状态
docker compose ps

# 健康检查（关键指标）
curl http://localhost:8000/api/health
# 预期：{"status": "healthy", "database": "connected", "redis": "connected", "chromadb": "connected"}

# RAG 管道状态
curl http://localhost:8000/api/v1/rag/status
# 预期：{"graph_compiled": true, "chromadb": true, "llm": true}
```

### 1.2 服务启停

```bash
# 启动全部
docker compose up -d

# 停止全部
docker compose down

# 重启单个服务
docker compose restart app
docker compose restart prometheus

# 仅重启应用（不影响数据层）
docker compose restart app
```

### 1.3 日志查看

```bash
# 实时查看应用日志
docker compose logs -f app

# 查看最近 100 行
docker compose logs --tail 100 app

# 查看特定时间段
docker compose logs --since 30m app
docker compose logs --since 2026-08-03T10:00:00 --until 2026-08-03T12:00:00 app

# 过滤错误日志
docker compose logs app | grep -E "ERROR|CRITICAL"
```

应用日志文件位于 `logs/` 目录：
- `app.log`：应用主日志
- `rag.log`：RAG 管道日志
- `safety.log`：安全护栏命中日志
- `access.log`：HTTP 访问日志

### 1.4 资源监控

```bash
# 实时资源占用
docker stats

# 磁盘占用
docker system df

# 清理悬挂镜像
docker image prune -f
```

## 2. 监控告警

### 2.1 Prometheus 指标

访问 Prometheus UI：http://localhost:9090

核心指标：

| 指标 | 含义 | 告警阈值 |
|------|------|---------|
| `http_requests_total` | HTTP 请求总数 | - |
| `http_request_duration_seconds` | 请求延迟分布 | P99 > 5s |
| `http_requests_failed_total` | 失败请求总数 | 5xx 错误率 > 1% |
| `rag_query_duration_seconds` | RAG 查询延迟 | P99 > 8s |
| `safety_blocked_total` | 安全拦截次数 | 突增 > 50% |
| `clarification_triggered_total` | 追问触发次数 | 突增 > 30% |
| `db_connection_pool_active` | DB 连接池使用 | > 80% |
| `redis_memory_used_bytes` | Redis 内存使用 | > 80% maxmemory |

### 2.2 Grafana 仪表盘

访问 Grafana：http://localhost:3000（admin/admin）

预置仪表盘：
- **HealthAI Overview**：API QPS、延迟、错误率
- **RAG Pipeline**：意图分布、检索耗时、改写次数、追问触发率
- **Safety Guard**：拦截率、分类分布、热更新事件
- **Infrastructure**：CPU/内存/磁盘/网络

### 2.3 告警规则

配置文件：`prometheus/alerts.yml`

关键告警规则：

```yaml
groups:
- name: healthai_critical
  rules:
  # API 5xx 错误率告警
  - alert: HighErrorRate
    expr: |
      rate(http_requests_failed_total{status=~"5.."}[5m])
      / rate(http_requests_total[5m]) > 0.01
    for: 5m
    labels:
      severity: critical
    annotations:
      summary: "API 5xx 错误率 > 1%"
      description: "当前错误率 {{ $value | humanizePercentage }}"

  # RAG P99 延迟告警
  - alert: HighRAGLatency
    expr: |
      histogram_quantile(0.99,
        rate(rag_query_duration_seconds_bucket[5m])) > 8
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "RAG 查询 P99 > 8s"

  # 服务宕机告警
  - alert: ServiceDown
    expr: up == 0
    for: 1m
    labels:
      severity: critical
    annotations:
      summary: "{{ $labels.instance }} 服务不可用"
```

### 2.4 告警通知

支持的通知渠道：
- 邮件（SMTP 配置在 `.env`）
- 钉钉机器人（设置 `DINGTALK_WEBHOOK_URL`）
- 企业微信（设置 `WECOM_WEBHOOK_URL`）

## 3. 配置管理

### 3.1 配置文件清单

| 文件 | 用途 | 是否支持热更新 |
|------|------|---------------|
| `.env` | 环境变量（启动时读取） | 否，需重启 |
| `config/safety_rules.yaml` | 安全护栏规则 | ✅ 自动热更新 |
| `prometheus.yml` | 指标采集配置 | 通过 `/-/reload` API |
| `grafana/provisioning/` | Grafana 数据源/仪表盘 | 启动时加载 |

### 3.2 安全规则热更新

**修改规则**：
1. 编辑 `config/safety_rules.yaml`
2. 保存后自动生效（5 秒内 mtime 检测）
3. 验证生效：

```bash
# 查看当前生效规则
curl http://localhost:8000/api/v1/admin/safety-rules

# 强制重载（不等待 mtime 检测）
curl -X POST http://localhost:8000/api/v1/admin/safety-rules/reload \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**热更新机制**：
- 通过 `os.stat().st_mtime` 检测文件变更
- 5 秒间隔限频，避免高频 stat 开销
- 加载失败时保留上一次有效配置（fail-safe）
- 加载成功后记录版本号与时间戳

### 3.3 运行时参数调整

部分参数支持通过环境变量调整（需重启）：

```bash
# RAG 检索配置
RAG_TOP_K=5                    # 检索 top-K
RAG_SIMILARITY_THRESHOLD=0.5   # 相似度阈值
RAG_MAX_REWRITE=2              # 最大改写次数
RAG_CLARIFICATION_THRESHOLD=0.75  # 追问触发阈值

# LLM 配置
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=2048

# 限流配置
LOGIN_RATE_LIMIT_PER_IP=5/60s
RAG_RATE_LIMIT_PER_USER=30/60s
```

## 4. 故障排查

### 4.1 故障定位流程

```
用户报告问题
    ↓
检查 /api/health（服务存活？）
    ↓ ✅
检查 docker compose ps（依赖服务存活？）
    ↓ ✅
查看应用日志 docker compose logs --tail 200 app
    ↓
查看 Prometheus 指标（异常突增？）
    ↓
根据日志中的 ERROR/CRITICAL 定位模块
```

### 4.2 常见故障与解决

#### 故障 1：应用启动失败

**现象**：`docker compose up app` 后容器立即退出

**排查**：
```bash
docker compose logs app | tail -50
```

常见原因：
- `.env` 缺失必填项 → 补全配置
- DB 连接失败 → 检查 `DATABASE_URL` 与 `db` 服务状态
- LLM API key 无效 → 验证 `LLM_API_KEY`

#### 故障 2：RAG 查询超时

**现象**：`POST /rag/query` 返回 504 或耗时 > 10s

**排查步骤**：
1. 检查 LLM API 延迟：
   ```bash
   docker compose logs app | grep "LLM_API" | tail -10
   ```
2. 检查 ChromaDB 健康：
   ```bash
   curl http://localhost:8001/api/v2/heartbeat
   ```
3. 检查向量库是否为空：
   ```bash
   curl http://localhost:8000/api/v1/rag/status
   # 若 collection_count = 0，需重新入库
   ```

**临时降级**：在 `.env` 中设置 `RAG_MAX_REWRITE=0` 关闭改写循环。

#### 故障 3：ChromaDB 数据丢失

**现象**：向量检索返回空结果

**排查**：
```bash
# 检查 collection
docker compose exec chroma python -c "
import chromadb
c = chromadb.HttpClient(host='localhost', port=8000)
col = c.get_collection('authoritative_sources')
print('count:', col.count())
"

# 若 count=0，从备份恢复或重新入库
```

恢复流程：
```bash
# 1. 停止应用
docker compose stop app

# 2. 恢复 ChromaDB 数据卷
docker run --rm -v healthai-platform_chroma_data:/data \
  -v $(pwd):/backup alpine \
  tar xzf /backup/chroma_YYYYMMDD.tar.gz -C /data

# 3. 启动应用
docker compose start app

# 4. 验证
curl http://localhost:8000/api/v1/rag/status
```

#### 故障 4：安全规则加载失败

**现象**：日志出现 `安全规则加载失败`

排查：
```bash
# 查看 loader 日志
docker compose logs app | grep SafetyRuleLoader

# 验证 YAML 语法
python -c "import yaml; yaml.safe_load(open('config/safety_rules.yaml'))"

# 强制重载
curl -X POST http://localhost:8000/api/v1/admin/safety-rules/reload
```

若 YAML 损坏，loader 会保留上一次有效配置，服务不会中断。

### 4.3 性能调优

#### 高延迟场景

| 现象 | 调优方向 |
|------|---------|
| P50 > 2s | LLM API 延迟高，考虑换更快的模型或本地部署 |
| P99 > 8s | 检查改写循环是否过多，设置 `RAG_MAX_REWRITE=1` |
| 并发上不去 | 增加 uvicorn workers：`APP_WORKERS=4` |
| 内存持续增长 | 设置 `APP_MAX_REQUESTS=10000` 定期重启 worker |

#### 数据库慢查询

```sql
-- 查看慢查询
SELECT query, mean_time, calls
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 10;

-- 检查索引使用率
SELECT relname, indexrelname, idx_scan
FROM pg_stat_user_indexes
WHERE idx_scan = 0;
```

## 5. 备份与恢复

详见《部署文档》§5 数据备份与恢复。

### 5.1 备份策略

| 数据 | 频率 | 保留时长 | 存储 |
|------|------|---------|------|
| PostgreSQL | 每日 03:00 | 30 天 | 本地 + 远程 |
| ChromaDB | 每周日 03:00 | 4 周 | 本地 |
| Redis RDB | 每日 04:00 | 7 天 | 本地 |
| 配置文件 | 每次变更 | 永久 | Git 版本控制 |

### 5.2 灾难恢复演练

每季度执行一次完整灾难恢复演练：
1. 在隔离环境部署备份
2. 验证数据完整性
3. 模拟主库故障切换
4. 记录 RTO/RPO 指标

## 6. 安全运维

### 6.1 密钥轮换

| 密钥 | 轮换周期 | 轮换方式 |
|------|---------|---------|
| JWT_SECRET_KEY | 90 天 | 修改 `.env` 后重启应用（旧 token 失效） |
| POSTGRES_PASSWORD | 180 天 | 修改数据库 + `.env`，重启 |
| REDIS_PASSWORD | 180 天 | 修改 Redis 配置 + `.env`，重启 |
| LLM_API_KEY | 按需 | 在 LLM 平台轮换后更新 `.env` |

### 6.2 安全审计

定期审计项：
- 每周：检查 `safety_blocked_total` 指标，确认拦截规则有效
- 每月：审查 Grafana 访问日志，确认无未授权访问
- 每季度：依赖安全扫描 `pip-audit` / `safety check`
- 每年：渗透测试与合规审计

### 6.3 日志保护

- 应用日志保留 90 天
- 安全拦截日志保留 1 年（合规要求）
- 访问日志保留 30 天
- 敏感字段（密码、token）已在日志中脱敏

## 7. 版本升级

详见《部署文档》§6 升级流程。

升级前必做：
1. 备份所有数据
2. 阅读版本 release notes
3. 在 staging 环境验证
4. 准备回滚方案

升级后验证清单：
- [ ] `/api/health` 返回 healthy
- [ ] 执行冒烟测试（注册/登录/RAG 查询/计划生成）
- [ ] Prometheus 指标正常采集
- [ ] Grafana 仪表盘数据更新
- [ ] 日志无 ERROR 级别条目（持续 30 分钟）
