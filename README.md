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

## 这是什么？

**HealthAI 是一个基于权威指南的 AI 饮食健康助手。**

它面向需要管理饮食的普通人——减肥减脂、三高、痛风、肾病等人群，也适合营养师等健康从业者作为辅助工具。

它解决的问题很简单：**网上饮食建议鱼龙混杂，AI 生成的答案又常常是编的。** HealthAI 的每个回答都检索自国家卫健委发布的权威食养指南和标准食物成分数据，并附上来源引用——你可以点开溯源，自己验证。

```text
你问："痛风缓解期能吃豆腐吗？"
它答：基于《成人高尿酸血症与痛风食养指南》给出建议，
      并标注来源章节——不是 AI 拍脑袋，是有出处的。

你说："帮我减脂，26 岁，身高 158，体重 65kg"
它做：生成 7 天 / 30 天个性化食谱，按你的身高体重疾病定制，
      每天食材不重样，附换菜方案和营养说明。
```

---

## ✨ 它能做什么？

| 功能 | 说明 |
| ---- | ---- |
| 💬 **智能饮食问答** | 问"痛风能吃豆腐吗""减肥能吃多少米饭"，回答基于权威指南并**附来源引用**，可点击溯源到原文档 |
| 🍱 **个性化食谱生成** | 输入身高体重目标和健康状况，生成 **7 天 / 30 天**食谱计划：每天三餐+加餐不重样，附营养分析和替换方案 |
| 🍚 **食物数据查询** | 259 条 GI 升糖指数、中国食物成分表、常见食物嘌呤含量，支持自然语言查询（"西瓜 GI 高吗"） |
| 🤔 **多轮主动追问** | 信息不够时它会**主动问你**（"您的身高体重是多少？"），而不是瞎猜着生成——像真人营养师一样澄清需求 |
| 🛡️ **安全护栏** | 自动拦截处方药、诊断、极端饮食等越界请求；5+2 轻断食这类**科学减脂咨询正常放行** |
| ✅ **打卡跟踪** | 按计划每日打卡，跟踪执行情况 |
| 🎬 **多模态知识库** | 不止 PDF 指南，还入库了专家视频（含字幕转写）、GI 数据表，回答可引用视频来源 |

---

## 💬 示例对话

```text
👤 26岁男生，想减肥，帮我制定个饮食计划

🤖  为了给你定制食谱，还需要确认几个信息：
    你的身高和体重大概是多少？平时有体检指标异常吗？

👤  158cm，65kg，尿酸偏高

🤖  已为你匹配《成人高尿酸血症与痛风食养指南》+《成人肥胖食养指南》。
    生成了 7 天食谱（表格如下），每天不重样：
    - 周一：燕麦粥 + 清蒸鲈鱼 + 蒜蓉油麦菜 …
    - 周二：全麦面包 + 虾仁冬瓜汤 + 凉拌菠菜 …
    每道菜都可在「换菜池」中替换同类食材。
    📚 来源：成人高尿酸血症与痛风食养指南、中国居民膳食指南（2022）

👤  那我能喝豆浆吗？

🤖  缓解期可以适量饮用。根据指南，大豆制品属于低嘌呤食物…
    📚 来源：成人高尿酸血症与痛风食养指南（第X节）
    [点击可查看原文档对应段落]
```

---

## 🎯 核心技术亮点

- 🧠 **LangGraph 状态机编排**：意图识别 → Query 改写 → 混合检索 → 评估 → 追问/生成，复杂多轮对话流转
- 🔍 **混合检索架构（V3.0）**：向量语义检索 + PostgreSQL BM25 关键词检索双路召回，RRF 融合优化排序
- 📐 **语义分块（V3.0）**：SemanticChunker 基于段落/章节/句子边界智能切分，重叠窗口保持上下文连贯
- 💾 **双写存储（V3.0）**：ChromaDB（向量）+ PostgreSQL document_chunks（全文），支持多模态检索
- 📋 **指南注册表（V3.0）**：GuidelineRegistry 按年龄/疾病/BMI 两阶段路由匹配，精准定位目标指南
- 🛡️ **双重安全护栏**：正则规则（<5ms 快速拦截）+ LLM 语义判断（深层识别），规则外置 YAML 热更新
- 📊 **企业级可观测性**：Prometheus 指标 + Grafana 仪表盘 + 结构化日志 + OpenTelemetry 追踪
- ⚙️ **持续集成**：GitHub Actions 自动跑 lint / 依赖漏洞扫描 / 全量测试（覆盖率门槛 40%）

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

# 6. 入库数据（首次运行，导入指南/食物数据/GI 数据）
python scripts/ingest/unified_reindex.py --target all

# 7. 启动应用
python run.py all            # 一键启动 API(8000) + 前端(8501)
# 或仅启动后端：python web_api.py
```

启动后：

- **Web 界面**：http://localhost:8000 （对话问答、食谱表格、来源溯源）
- **API 文档**：http://localhost:8000/docs （Swagger 自动生成）

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
                                             │ └──────────────┘ │
                                             │ ┌──────────────┐ │
                                             │ │  ChromaDB   │ │
                                             │ └──────────────┘ │
                                             │ ┌──────────────┐ │
                                             │ │   Redis     │ │
                                             │ └──────────────┘ │
                                             └──────────────────┘
```

**一次提问的完整旅程**：用户提问 → 安全护栏校验 → 意图识别 → Query 改写 → 三路并行检索（向量/关键词/SQL）→ RRF 融合排序 → 质量评估（不达标则改写重检索或追问澄清）→ LLM 生成带引用的回答。

**详细架构设计** → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

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

**设计原则**：拦截的是"医疗越界"（开药、诊断、治疗方案），放行的是"健康生活咨询"（减脂计划、科学轻断食、饮食科普）。

**可拦截类别**：

- `prescription_drug` — 处方药请求
- `diagnosis_request` — 诊断请求
- `extreme_diet` — 极端饮食（连续多日绝食、催吐等）
- `treatment_plan` — 治疗方案请求
- `special_population_intervention` — 特殊人群干预

**规则热更新**：

```bash
# 编辑 config/safety_rules.yaml 后自动生效（5 秒内），无需重启
curl -X POST http://localhost:8000/api/v1/admin/safety-rules/reload
```

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
│   ├── etl/                      # 数据清洗脚本
│   ├── verify/                   # 验证脚本
│   └── utils/                    # 工具脚本
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
├── .github/workflows/ci.yml      # GitHub Actions 持续集成
├── docs/                         # 文档
├── grafana/                      # Grafana 配置
├── docker-compose.yml            # Docker Compose
├── Dockerfile                    # 应用镜像
├── requirements.txt              # Python 依赖
└── README.md
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

### 持续集成

项目配置了 GitHub Actions（[.github/workflows/ci.yml](.github/workflows/ci.yml)）：每次 push / PR 自动在干净的 Ubuntu 环境中运行 ruff 风格检查、pip-audit 依赖漏洞扫描、pytest 全量测试（PostgreSQL/Redis 由服务容器提供），覆盖率低于 40% 判失败。

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
