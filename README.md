# HealthAI-Platform 🏥

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.x-FF6B6B.svg)](https://langchain-ai.github.io/langgraph/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791.svg)](https://www.postgresql.org/)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-1.x-4793E4.svg)](https://www.trychroma.com/)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D.svg)](https://redis.io/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg)](https://www.docker.com/)
[![Tests](https://img.shields.io/badge/tests-422%20passed-4CAF50.svg)](#测试)
[![Version](https://img.shields.io/badge/version-3.0.0-FF8C00.svg)](#版本历史)

> 基于 **LangGraph 状态机** 与 **RAG（检索增强生成）** 的智能健康饮食助手，
> 融合 **向量语义检索 + BM25 关键词检索** 混合召回，采用 **语义分块** 确保查询精度，
> 为用户提供可溯源、安全护栏加持的个性化健康建议。

---

## ✨ 核心特性

- 🧠 **LangGraph 状态机编排**：意图识别 → Query 改写 → 混合检索 → 评估 → 追问/生成，复杂多轮对话流转
- 🔍 **混合检索架构（V3.0 升级）**：向量语义检索 + PostgreSQL BM25 关键词检索双路召回，RRF 融合优化排序
- 📐 **语义分块（V3.0 新增）**：SemanticChunker 基于段落/章节/句子边界智能切分，重叠窗口保持上下文连贯
- 💾 **双写存储（V3.0 新增）**：ChromaDB（向量）+ PostgreSQL document_chunks（全文），支持多模态检索
- 📋 **指南注册表（V3.0 新增）**：GuidelineRegistry 按年龄/疾病/BMI 两阶段路由匹配，精准定位目标指南
- 🍚 **GI 升糖指数数据（V3.0 新增）**：259 条 GI 数据入库，支持低/中/高 GI 食物检索
- 🛡️ **双重安全护栏**：正则规则（快速拦截）+ LLM 语义判断（深层识别），拦截处方药/诊断请求等违规咨询
- 🔍 **ClarificationNode 追问节点**：检索质量差时自动追问用户补充信息，而非强行猜测
- 🍎 **食物安全校验**：独立 `food_validator` 模块，覆盖过敏原/疾病禁忌/药物交互/营养素边界
- 🔧 **合规规则热更新**：安全规则外置 YAML 配置，基于 mtime 检测自动热加载，无需重启
- 📊 **企业级可观测性**：Prometheus 指标采集 + Grafana 仪表盘 + 结构化日志 + OpenTelemetry 追踪
- 📚 **数据溯源**：大模型回复自动附带引用来源，拒绝 AI 幻觉
- 🐳 **一键 Docker 部署**：PostgreSQL + ChromaDB + Redis + FastAPI + Streamlit + Prometheus + Grafana

---

## 🏗️ 架构概览

```
┌──────────────┐  HTTPS  ┌──────────────┐
│   客户端     │ ───────▶│   Nginx      │
│ Web/Mobile   │         │  反向代理    │
└──────────────┘         └──────┬───────┘
                                 │
┌────────────────────────────────▼────────────────────────────────┐
│                      FastAPI 应用层                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │ api/     │  │ services/│  │ core/    │  │ repositories/│   │
│  │ 路由/中间│  │ 业务服务 │  │ LangGraph│  │ 数据访问    │   │
│  └──────────┘  └──────────┘  └────┬─────┘  └──────┬───────┘   │
│                                   │                │           │
│  ┌──────────┐  ┌────────────┐    │                │           │
│  │ utils/   │  │ guideline  │    │                │           │
│  │ 工具函数 │  │ _registry  │    │                │           │
│  └──────────┘  └────────────┘    │                │           │
└───────────────────────────────────┼────────────────┼───────────┘
                                    │                │
          ┌─────────────────────────┴──┐     ┌───────▼──────────┐
          │   外部 LLM / Embedding API  │     │   数据存储层     │
          └────────────────────────────┘     │ ┌──────────────┐ │
                                             │ │ PostgreSQL   │ │
                                             │ │ └──────────────┘ │
                                             │ ┌──────────────┐ │
                                             │ │  ChromaDB   │ │
                                             │ └──────────────┘ │
                                             │ ┌──────────────┐ │
                                             │ │   Redis     │ │
                                             │ └──────────────┘ │
                                             └──────────────────┘
```

**详细架构设计** → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## 🛠️ 技术栈

| 类别         | 技术                            | 版本             |
| ------------ | ------------------------------- | ---------------- |
| Web 框架     | FastAPI                         | 0.115            |
| AI 编排      | LangGraph + LangChain           | 1.2 / 1.3        |
| LLM          | GPT-4o-mini / 兼容 API          | -                |
| Embedding    | text-embedding-3-small (1024维) | -                |
| 关系型数据库 | PostgreSQL                      | 15               |
| 向量数据库   | ChromaDB                        | 1.x (1.5.9)      |
| 缓存         | Redis                           | 7                |
| 监控         | Prometheus + Grafana            | latest           |
| 容器化       | Docker Compose                  | v2               |
| 测试         | pytest + httpx + Locust         | 8.3 / 0.27 / 0.2 |

---

## 📁 项目结构

```
HealthAI-Platform/
├── src/                          # 应用源码（模块化分层架构）
│   ├── api/                      # 应用层
│   │   ├── routes/               # 路由定义
│   │   ├── middleware/           # 中间件（限流/鉴权/版本控制）
│   │   ├── schemas/              # 请求/响应 Schema
│   │   └── streaming.py          # SSE 流式响应工具
│   ├── core/                     # 核心编排层
│   │   ├── nodes/                # LangGraph 节点（7 个状态机节点）
│   │   ├── graph_builder.py      # 状态机编排
│   │   ├── state.py              # RAGState 状态定义
│   │   ├── safety_checker.py     # 安全护栏（正则 + LLM）
│   │   ├── rrf_fusion.py         # RRF 融合算法
│   │   ├── guideline_registry.py # 指南注册表（V3.0 新增）
│   │   ├── llm_client.py         # LLM 客户端
│   │   ├── embedding.py          # Embedding 服务
│   │   └── loaders/              # 多模态加载器
│   ├── services/                 # 业务服务层
│   │   ├── rag_service.py        # RAG 管道
│   │   ├── document_processor.py # 文档处理（语义分块）
│   │   ├── auth_service.py       # 认证业务
│   │   ├── plan_service.py       # 饮食计划
│   │   ├── food_parser.py        # 食物解析
│   │   └── ...                   # 其他服务
│   ├── repositories/             # 数据访问层
│   │   ├── vector_store.py       # ChromaDB 访问
│   │   ├── chunk_repository.py   # PostgreSQL BM25 检索（V3.0 新增）
│   │   ├── session.py            # PostgreSQL 会话
│   │   └── init_db.py            # 数据库初始化
│   ├── models/                   # 数据模型层
│   │   └── db_models.py          # SQLAlchemy ORM
│   ├── utils/                    # 工具函数层
│   │   ├── semantic_chunker.py   # 语义分块器（V3.0 新增）
│   │   ├── food_validator.py     # 食物安全校验
│   │   ├── nutrition_calc.py     # 营养计算
│   │   └── ...                   # 其他工具
│   ├── config/                   # 配置层
│   │   ├── settings.py           # 全局配置
│   │   ├── safety_rule_loader.py # 安全规则热加载
│   │   └── tracing.py            # OpenTelemetry 配置
│   └── modules/                  # V4 预留模块
├── scripts/                      # 运维脚本（按职责分类）
│   ├── ingest/                   # 入库脚本
│   │   ├── unified_reindex.py    # 统一入库（指南/食品/GI/专家）
│   │   ├── import_gi.py          # GI 升糖指数 ETL
│   │   ├── import_nutrition.py   # 营养成分 ETL
│   │   └── seed_knowledge_base.py # 种子数据导入
│   ├── etl/                      # 数据清洗脚本
│   │   ├── clean_nutrition_data.py # 营养数据清洗（步骤1）
│   │   ├── fix_abnormal_data.py  # 异常数据修正（步骤2）
│   │   └── fill_missing_calories.py # 缺失热量补充（步骤3）
│   ├── verify/                   # 验证脚本
│   │   ├── verify_ingestion.py   # 入库一致性验证
│   │   ├── check_kb_guides.py    # 指南检查
│   │   └── final_nutrition_report.py # 数据质量报告
│   └── utils/                    # 工具脚本
│       └── clear_cache.py        # Redis 缓存清理
├── tests/                        # 测试（分层组织）
│   ├── unit/                     # 单元测试
│   ├── integration/              # 集成测试
│   ├── e2e/                      # 端到端测试
│   └── perf/                     # 性能测试
├── raw_data/                     # 原始数据
│   ├── raw/                      # 指南/专家/学术数据
│   └── china-food-composition-data/ # 中国食物成分数据
├── config/                       # 外部配置
│   └── safety_rules.yaml         # 安全护栏规则
├── database/                     # 数据库脚本
├── docs/                         # 文档
├── grafana/                      # Grafana 配置
├── docker-compose.yml            # Docker Compose
├── Dockerfile                    # 应用镜像
├── requirements.txt              # Python 依赖
└── README.md
```

---

## 🚀 快速开始

### 环境要求

- Python 3.11+
- PostgreSQL 15+
- Redis 7+
- Docker 24+（可选，推荐用于生产）

### 方式一：Docker 一键部署（推荐）

```bash
# 1. 克隆项目
git clone https://github.com/midelshuang/Smart-Health-Assistant.git
cd HealthAI-Platform

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入：
#   - POSTGRES_PASSWORD（生产环境务必修改）
#   - JWT_SECRET_KEY（32+ 字符随机字符串）
#   - LLM_API_KEY / EMBEDDING_API_KEY

# 3. 一键启动所有服务
docker compose --env-file .env up -d

# 4. 验证健康检查
curl http://localhost:8000/api/health
# 预期: {"status":"healthy","database":"connected","redis":"connected"}
```

### 方式二：本地开发

```bash
# 1. 克隆并创建虚拟环境
git clone https://github.com/midelshuang/Smart-Health-Assistant.git
cd HealthAI-Platform
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，修改数据库/LLM 连接信息

# 4. 启动依赖服务
docker compose up -d db redis chroma

# 5. 初始化数据库
psql -U postgres -d healthai_db -f database/init.sql

# 6. 入库数据（首次运行）
python scripts/ingest/unified_reindex.py --target all

# 7. 启动应用
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 📖 API 速查

### 认证

```
POST /api/v1/auth/register     # 用户注册
POST /api/v1/auth/login        # 用户登录（返回 JWT）
POST /api/v1/auth/refresh      # 刷新 Token
```

### RAG 查询

```
POST /api/v1/rag/query         # RAG 查询（同步）
POST /api/v1/rag/query/stream  # RAG 查询（流式 SSE）
GET  /api/v1/rag/status        # 管道状态
```

**请求示例**：

```python
import httpx

# 登录获取 Token
login = httpx.post(
    "http://localhost:8000/api/v1/auth/login",
    json={"username": "test", "password": "test123"},
)
token = login.json()["access_token"]

# RAG 查询（自动识别意图 + 混合检索）
r = httpx.post(
    "http://localhost:8000/api/v1/rag/query",
    headers={"Authorization": f"Bearer {token}"},
    json={"query": "25岁男生减肥饮食建议"},
)
print(r.json()["answer"])
```

### 饮食计划

```
POST /api/v1/plan/generate     # 生成个性化饮食计划
GET  /api/v1/plan/{plan_id}    # 获取计划详情
PUT  /api/v1/plan/{plan_id}    # 调整计划
POST /api/v1/plan/{plan_id}/checkin  # 打卡
```

### 健康检查

```
GET  /api/health               # 服务健康状态
GET  /api/metrics              # Prometheus 指标
```

**完整 API 文档** → http://localhost:8000/docs

---

## 🧠 RAG 状态机流程

```
START
  │
  ▼
[ safety_check ] ──(拦截)──▶ END（拒绝话术）
  │(通过)
  ▼
[ intent_recognizer ]
  │
  ▼
[ query_rewriter ]
  │
  ├──(food_search)──▶ [sql_retriever] ──┐
  │                                     │
  ├──(guideline_query)──▶ [vector_retriever] + [keyword_search] ──┤
  │                                     │
  └──(hybrid/unknown)──▶ 三路并行 ───────┘
                                        │
                                        ▼
                                  [ RRF 融合 ]
                                        │
                                        ▼
                                  [ evaluator ]
                                        │
                          ┌──────────────┼──────────────┐
                          ▼              ▼              ▼
                      (通过)       (可改写)       (达上限)
                          │              │              │
                          ▼              ▼              ▼
                     [fusion]    [query_rewriter]  [clarification]
                          │                              │
                          ▼                              ▼
                     [generator]                   (追问)→ END
                          │
                          ▼
                         END
```

**详细节点说明** → [src/core/nodes/**init**.py](src/core/nodes/__init__.py)

---

## 🛡️ 安全护栏

系统采用**双重校验**机制拦截违规医疗咨询：

| 校验层 | 方式         | 延迟  | 用途                 |
| ------ | ------------ | ----- | -------------------- |
| 第一层 | 正则规则匹配 | < 5ms | 快速拦截明确违规内容 |
| 第二层 | LLM 语义判断 | < 1s  | 识别隐晦违规请求     |

**可拦截类别**：

- `prescription_drug` — 处方药请求
- `diagnosis_request` — 诊断请求
- `extreme_diet` — 极端饮食
- `treatment_plan` — 治疗方案请求
- `special_population_intervention` — 特殊人群干预

**规则热更新**：

```bash
# 编辑 config/safety_rules.yaml 后自动生效（5 秒内）
curl -X POST http://localhost:8000/api/v1/admin/safety-rules/reload
```

---

## 🧪 测试

### 运行全部测试

```bash
# 运行全部测试（单元 + 集成 + E2E）
pytest tests/ -v

# 分层运行
pytest tests/unit/ -v           # 仅单元测试（快速）
pytest tests/integration/ -v    # 集成测试（需 Redis/DB）
pytest tests/e2e/ -v            # 端到端测试
pytest tests/perf/ -v           # 性能测试
```

### 数据验证

```bash
# 验证入库数据一致性
python scripts/verify/verify_ingestion.py

# 检查指南数据
python scripts/verify/check_kb_guides.py

# 生成数据质量报告
python scripts/verify/final_nutrition_report.py
```

---

## 📚 文档

| 文档                                      | 说明                                   |
| ----------------------------------------- | -------------------------------------- |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md)   | 系统架构、混合检索、语义分块、双写存储 |
| [SCRIPTS_GUIDE.md](docs/SCRIPTS_GUIDE.md) | 运维脚本功能详解                       |
| [PROJECT_INTRO.md](docs/PROJECT_INTRO.md) | 项目介绍与宣传材料                     |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md)       | Docker 部署、数据备份                  |
| [OPERATIONS.md](docs/OPERATIONS.md)       | 监控告警、故障排查                     |
| [USER_MANUAL.md](docs/USER_MANUAL.md)     | API 使用、多轮对话                     |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md)     | 开发文档                               |

---

## 🔧 配置说明

### 核心环境变量

| 变量                           | 说明                         | 默认值         |
| ------------------------------ | ---------------------------- | -------------- |
| `APP_ENV`                      | 环境标识（dev/staging/prod） | development    |
| `JWT_SECRET_KEY`               | JWT 签名密钥                 | **必填**       |
| `DATABASE_URL`                 | PostgreSQL 连接串            | -              |
| `REDIS_HOST` / `REDIS_PORT`    | Redis 地址                   | localhost:6379 |
| `LLM_API_KEY` / `LLM_BASE_URL` | LLM API 配置                 | -              |
| `LLM_MODEL`                    | LLM 模型                     | gpt-4o-mini    |
| `EMBEDDING_API_KEY`            | Embedding API Key            | -              |
| `CHROMA_HOST` / `CHROMA_PORT`  | ChromaDB 地址                | localhost:8000 |
| `RAG_TOP_K`                    | 检索 Top-K                   | 5              |
| `RAG_MAX_REWRITE`              | Query 最大改写次数           | 2              |
| `RAG_CLARIFICATION_THRESHOLD`  | 追问触发阈值                 | 0.75           |

**完整配置** → [.env.example](.env.example)

---

## 🗺️ 路线图

### V1.4 ✅（基础能力）

- [x] LangGraph 状态机编排
- [x] SQL + ChromaDB 混合检索
- [x] RRF 融合算法
- [x] 安全护栏
- [x] ClarificationNode 追问节点
- [x] Docker Compose 部署

### V2.0 ✅（工程化）

- [x] 模块化分层重构（api/service/core/repositories/utils）
- [x] 脚本体系：ingest/etl/verify/utils 四层分类
- [x] OpenTelemetry 追踪
- [x] 流式响应优化
- [x] Prometheus + Grafana

### V3.0 ✅（智能升级）

- [x] **语义分块**：SemanticChunker 替代固定分块
- [x] **混合检索升级**：向量 + BM25 双路召回
- [x] **双写存储**：ChromaDB + PostgreSQL document_chunks
- [x] **指南注册表**：两阶段检索路由
- [x] **GI 数据入库**：升糖指数双写支持
- [x] **E2E 测试更新**：适配语义分块 + 双写存储的新检索链路

### V4.0 📋（业务扩展）

- [ ] 社交模块（关注/粉丝/动态）
- [ ] 专家服务（认证/付费咨询）
- [ ] UGC 内容管理
- [ ] 多租户隔离

---

## 🤝 贡献指南

1. Fork 本仓库
2. 创建功能分支：`git checkout -b feature/amazing-feature`
3. 提交更改：`git commit -m 'feat: add amazing feature'`
4. 推送分支：`git push origin feature/amazing-feature`
5. 发起 Pull Request

### 开发规范

- 遵循现有目录分层（api/core/services/repositories/models/utils/config）
- 新增功能必须附带单元测试
- 遵循 PEP 8 编码规范
- 敏感信息禁止提交到 Git

---

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。

---

## 🙏 致谢

- [LangChain](https://github.com/langchain-ai/langchain) / [LangGraph](https://github.com/langchain-ai/langgraph) — RAG 编排框架
- [ChromaDB](https://github.com/chroma-core/chroma) — 向量数据库
- [FastAPI](https://github.com/tiangolo/fastapi) — 现代 Python Web 框架
- [食物成分数据](https://www.fao.org/food-composition-databases/en/) — 中国食物成分表
