# 开发文档：HealthAI-Platform

> **版本**：V2.0
> **最后更新**：2026-08-04
> **状态**：V2.0 全部完成，422 项测试通过
> **维护者**：HealthAI 团队

---

## 📋 目录

1. [项目概述](#1-项目概述)
2. [设计目标与原则](#2-设计目标与原则)
3. [系统架构设计](#3-系统架构设计)
4. [核心模块详解](#4-核心模块详解)
5. [数据模型设计](#5-数据模型设计)
6. [关键技术实现](#6-关键技术实现)
7. [测试策略](#7-测试策略)
8. [可观测性设计](#8-可观测性设计)
9. [部署架构](#9-部署架构)
10. [版本规划与路线图](#10-版本规划与路线图)
11. [开发流程与规范](#11-开发流程与规范)
12. [附录](#12-附录)

---

## 1. 项目概述

### 1.1 项目定位

HealthAI-Platform 是一个面向 C 端用户的智能健康饮食助手平台，通过 **LangGraph 状态机**编排的多轮对话，结合 **RAG（检索增强生成）** 技术，为用户提供可溯源、安全护栏加持的个性化健康饮食建议。

### 1.2 核心价值

| 价值点 | 实现方式 |
|--------|----------|
| **可溯源** | 大模型回复自动附带引用来源，杜绝 AI 幻觉 |
| **安全护栏** | 正则规则 + LLM 语义双重校验，拦截处方药/诊断等违规咨询 |
| **个性化** | 基于用户档案（年龄/体重/目标/疾病）生成针对性建议 |
| **高可用** | Docker 一键部署，Prometheus 全链路监控 |
| **可观测** | OpenTelemetry 分布式追踪 + 结构化日志 |

### 1.3 当前版本能力（V2.0）

- ✅ LangGraph 状态机编排（7 节点 + 条件边）
- ✅ SQL + ChromaDB 混合检索 + RRF 融合
- ✅ 双重安全护栏（正则 + LLM）
- ✅ ClarificationNode 追问节点
- ✅ food_validator 食物安全校验
- ✅ 安全规则 YAML 热更新
- ✅ OpenTelemetry 分布式追踪
- ✅ SSE 流式响应（心跳保活 + 断开检测）
- ✅ 模块化分层架构
- ✅ 分层测试体系（422 项通过）

---

## 2. 设计目标与原则

### 2.1 设计目标

| 维度 | 目标 | 当前指标 |
|------|------|----------|
| **性能** | RAG 查询 P99 < 3s | < 2.5s |
| **可用性** | 服务可用率 99.9% | 已具备 |
| **可观测** | 全链路追踪 + 指标 + 日志 | 三位一体 |
| **安全** | 100% 拦截违规查询 | 双重护栏 |
| **扩展性** | 模块化、可插拔 | 7 大模块解耦 |

### 2.2 设计原则

1. **分层解耦**：api → services → core → repositories → models，单向依赖
2. **配置外置**：安全规则、连接信息、特性开关全部外置
3. **渐进增强**：V1.4 核心 → V2.0 可观测 → V4.0 业务扩展
4. **安全第一**：所有用户输入必过安全护栏，所有 LLM 输出必带引用
5. **可测试性**：每个模块可独立测试，依赖可通过 fixture mock
6. **资源友好**：流式响应支持客户端断开检测，避免浪费 LLM token

---

## 3. 系统架构设计

### 3.1 整体架构

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
│  ┌──────────┐                     │                │           │
│  │ utils/   │                     │                │           │
│  │ 工具函数 │                     │                │           │
│  └──────────┘                     │                │           │
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
                                             │ ┌──────────────┐ │ │
                                             │ │   Redis     │ │
                                             │ └──────────────┘ │
                                             └──────────────────┘
```

### 3.2 请求处理流程

```
HTTP 请求
   │
   ▼
[中间件层]
   ├── APIVersionMiddleware  → /v1, /v2 版本路由
   ├── TracingMiddleware     → 开启 Span，注入 trace_id
   └── MonitoringMiddleware  → 记录 HTTP 指标
   │
   ▼
[路由层] api/routes/
   ├── auth.py    → JWT 认证
   ├── rag.py     → RAG 查询（流式/非流式）
   ├── plan.py    → 饮食计划 CRUD
   ├── diet.py    → 饮食日志 CRUD
   └── user.py    → 用户档案 CRUD
   │
   ▼
[服务层] services/
   ├── rag_service.py        → RAG 业务编排
   ├── food_parser.py        → LLM 解析食物文本
   ├── adjustment_service.py → 计划动态调整
   ├── auth_service.py       → 注册/登录/JWT
   ├── user_service.py       → 用户档案
   ├── plan_service.py       → 计划管理
   ├── diet_service.py       → 饮食日志
   ├── cache_service.py      → Redis 缓存
   └── email_service.py      → 邮件通知
   │
   ▼
[核心层] core/
   ├── graph_builder.py      → LangGraph 状态机构建
   ├── nodes/                → 7 个状态机节点
   ├── safety_checker.py     → 双重安全护栏
   ├── rrf_fusion.py         → RRF 融合算法
   ├── llm_client.py         → LLM 客户端封装
   ├── embedding.py          → Embedding 服务
   └── tracing_helper.py    → 追踪装饰器
   │
   ▼
[数据访问层] repositories/
   ├── session.py            → PostgreSQL 会话
   ├── vector_store.py       → ChromaDB 访问
   └── init_db.py            → 数据库初始化
```

### 3.3 RAG 状态机流程

```
[START]
   │
   ▼
[SafetyCheckNode]  ─── 拦截 ──▶ [END]
   │ 通过
   ▼
[IntentRecognitionNode]  → 意图：查询食物/食养建议/闲聊
   │
   ▼
[QueryRewriteNode]  → 改写为 SQL 查询 + 向量查询
   │
   ▼
[HybridRetrievalNode]  ─── SQL + ChromaDB 并行检索
   │
   ▼
[RRFFusionNode]  → 融合多路结果
   │
   ▼
[RetrievalEvaluatorNode]  → 评估检索质量
   │
   ├── 质量差 ─▶ [ClarificationNode]  ── 追问用户
   │                                      │
   │                                      ▼
   │                                  [END]
   │
   └── 质量好
       │
       ▼
[AnswerGenerationNode]  → LLM 生成答案 + 引用
   │
   ▼
[END]
```

---

## 4. 核心模块详解

### 4.1 模块分层架构

```
src/
├── api/                          # 应用层（HTTP 入口）
│   ├── routes/                   # 路由定义
│   ├── middleware/               # 中间件（追踪/监控/版本控制）
│   ├── schemas/                  # 请求/响应 Schema
│   └── streaming.py              # SSE 流式响应工具
│
├── core/                         # 核心层（LangGraph 编排）
│   ├── graph_builder.py          # 状态机构建
│   ├── nodes/                    # 状态机节点
│   │   └── __init__.py           # 7 个节点实现
│   ├── safety_checker.py         # 安全护栏
│   ├── rrf_fusion.py            # RRF 融合
│   ├── llm_client.py             # LLM 客户端
│   ├── embedding.py              # Embedding 服务
│   ├── tracing_helper.py         # 追踪装饰器
│   ├── loaders/                  # 文档加载器
│   └── state.py                  # 状态定义
│
├── services/                     # 业务服务层
│   ├── rag_service.py            # RAG 业务编排
│   ├── food_parser.py            # 食物解析
│   ├── adjustment_service.py     # 计划调整
│   ├── auth_service.py            # 认证
│   ├── user_service.py           # 用户档案
│   ├── plan_service.py           # 饮食计划
│   ├── diet_service.py           # 饮食日志
│   ├── cache_service.py          # 缓存
│   ├── email_service.py          # 邮件
│   ├── login_limiter.py          # 登录限流
│   ├── document_processor.py     # 文档处理
│   └── base_service.py           # 服务基类
│
├── repositories/                 # 数据访问层
│   ├── session.py                # PostgreSQL
│   ├── vector_store.py           # ChromaDB
│   └── init_db.py                # 初始化
│
├── models/                       # 数据模型
│   └── db_models.py              # SQLAlchemy ORM 模型
│
├── config/                       # 配置层
│   ├── settings.py               # 全局配置
│   ├── tracing.py                # OpenTelemetry 配置
│   ├── safety_rule_loader.py     # 安全规则热加载
│   └── redis_key_spec.py         # Redis Key 规范
│
├── utils/                        # 工具层
│   ├── metrics.py                # Prometheus 指标
│   ├── logger.py                 # 结构化日志
│   ├── redis_client.py           # Redis 客户端
│   ├── food_validator.py         # 食物安全校验
│   ├── nutrition_calc.py         # 营养计算
│   ├── password_policy.py        # 密码策略
│   └── text_cleaner.py           # 文本清洗
│
└── modules/                      # 业务模块占位（V4）
    ├── content/                  # UGC 内容
    ├── expert/                   # 专家服务
    └── social/                   # 社交模块
```

### 4.2 关键模块说明

#### 4.2.1 流式响应模块（`src/api/streaming.py`）

**职责**：将同步生成器包装为异步 SSE 流，提供心跳保活、断开检测、指标采集。

**核心函数**：

| 函数 | 作用 |
|------|------|
| `format_sse(data)` | 格式化 SSE data 事件 |
| `format_sse_done()` | 生成 `[DONE]` 结束标记 |
| `format_sse_heartbeat()` | 生成 `: keepalive` 心跳注释 |
| `get_sse_headers()` | 返回标准 SSE 响应头 |
| `stream_with_heartbeat()` | 同步生成器 → 异步 SSE 流 |

**设计要点**：
- **异步非阻塞**：同步生成器在 `run_in_executor` 线程池运行，通过 `asyncio.Queue` 与事件循环通信
- **心跳保活**：15 秒无数据自动发送 `: keepalive` 注释，防止 Nginx/网关超时
- **断开检测**：每 5 秒检查 `request.is_disconnected()`，断开后立即 `cancel()` 生成任务
- **指标采集**：自动记录 TTFB、token 数、总耗时、错误、断开、活跃连接

#### 4.2.2 安全护栏模块（`src/core/safety_checker.py`）

**职责**：双重校验拦截违规咨询。

**双层架构**：
1. **正则规则层**（快速）：YAML 配置热加载，毫秒级响应
2. **LLM 语义层**（深度）：处理正则无法覆盖的语义场景

**拦截类别**：
- `prescription_drug` — 处方药请求
- `diagnosis_request` — 诊断请求
- `extreme_diet` — 极端饮食（辟谷/催吐）
- `medical_advice` — 医疗建议

#### 4.2.3 食物安全校验（`src/utils/food_validator.py`）

**职责**：独立食物安全校验，覆盖多维度风险。

**校验维度**：
- 过敏原匹配（用户档案 vs 食物成分）
- 疾病禁忌（糖尿病/高血压/痛风等）
- 药物交互（华法林/降压药等）
- 营养素边界（维生素 A/铁/钠过量）

#### 4.2.4 分布式追踪（`src/config/tracing.py`）

**职责**：OpenTelemetry 全链路追踪配置。

**三层追踪**：
1. **HTTP 请求层**：`TracingMiddleware` 自动埋点
2. **RAG 流程层**：`tracing_helper.py` 装饰器
3. **节点执行层**：每个 LangGraph 节点独立 Span

**导出器**：支持 Jaeger（OTLP）/ Console 多导出器切换。

### 4.3 模块依赖关系

```
api/routes ──▶ services ──▶ core ──▶ repositories ──▶ models
     │             │          │             │
     │             ▼          ▼             ▼
     │         utils ──── config ──── repositories
     │
     ▼
api/middleware
api/streaming
```

**依赖规则**：
- 上层可依赖下层，下层不可依赖上层
- 同层之间避免循环依赖
- `utils` 和 `config` 是基础层，被所有层共享

---

## 5. 数据模型设计

### 5.1 PostgreSQL 关系模型

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│     users       │     │  user_profiles  │     │   diet_plans    │
├─────────────────┤     ├─────────────────┤     ├─────────────────┤
│ id (PK)         │◀───│ user_id (FK)    │◀───│ user_id (FK)    │
│ username        │     │ age             │     │ plan_type       │
│ email           │     │ gender          │     │ start_date      │
│ password_hash   │     │ height_cm       │     │ target_calories │
│ created_at      │     │ weight_kg       │     │ status          │
└─────────────────┘     │ activity_level  │     └─────────────────┘
                        │ goal_type       │              │
                        │ body_fat_pct    │              ▼
                        └─────────────────┘     ┌─────────────────┐
                                                │   diet_logs     │
                                                ├─────────────────┤
                                                │ id (PK)         │
                                                │ user_id (FK)    │
                                                │ plan_id (FK)    │
                                                │ food_name       │
                                                │ quantity        │
                                                │ meal_type       │
                                                │ log_date        │
                                                └─────────────────┘
```

### 5.2 ChromaDB 向量集合

| 集合名 | 维度 | 用途 | 数据来源 |
|--------|------|------|----------|
| `food_knowledge` | 1024 | 食物营养知识 | 食物成分表 |
| `health_guides` | 1024 | 食养指南 | 中医食养文档 |
| `diet_tips` | 1024 | 饮食建议 | 营养学文档 |

### 5.3 Redis Key 规范（V4 预留）

```
healthai:v1:user:{user_id}:profile          # 用户档案缓存
healthai:v1:user:{user_id}:plan:active      # 当前活动计划
healthai:v1:rag:session:{session_id}        # RAG 会话上下文
healthai:v1:login:limit:{username}          # 登录限流
healthai:v1:token:blacklist:{jti}            # JWT 黑名单
```

---

## 6. 关键技术实现

### 6.1 RAG 混合检索 + RRF 融合

**问题**：单一检索方式无法兼顾精确性（食物成分查询）和语义性（食养建议查询）。

**方案**：
- **SQL 查询**：精确匹配食物名称、营养成分
- **向量检索**：ChromaDB 语义匹配食养指南
- **RRF 融合**：`score = 1/(60+rank_sql) + 1/(60+rank_vector)`

**实现**：[src/core/rrf_fusion.py](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/core/rrf_fusion.py)

### 6.2 ClarificationNode 追问机制

**问题**：检索质量差时，强行生成答案会导致幻觉。

**方案**：
- `RetrievalEvaluatorNode` 评估检索结果相关性
- 若平均相关性 < 阈值（0.5），路由到 `ClarificationNode`
- `ClarificationNode` 生成追问问题，引导用户补充信息
- 用户补充后，重新进入检索流程

### 6.3 安全规则热更新

**问题**：规则变更需重启服务，影响可用性。

**方案**：
- 规则外置到 `config/safety_rules.yaml`
- `SafetyRuleLoader` 基于 mtime 检测文件变更
- 检测到变更时自动重新加载，无需重启
- 加载失败时保留旧规则，记录错误日志

**实现**：[src/config/safety_rule_loader.py](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/config/safety_rule_loader.py)

### 6.4 SSE 流式响应优化

**问题**：同步生成器阻塞事件循环；代理超时断开；客户端断开后浪费 LLM 资源。

**方案**：

```python
async def stream_with_heartbeat(sync_generator, request, endpoint):
    # 1. 在线程池中运行同步生成器
    queue = asyncio.Queue(maxsize=64)
    task = asyncio.create_task(_run_in_executor(sync_generator, queue))

    # 2. 主循环：从队列读取 + 心跳 + 断开检测
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
            yield format_sse(item)  # 发送数据
        except asyncio.TimeoutError:
            yield format_sse_heartbeat()  # 心跳保活
            if await request.is_disconnected():
                task.cancel()  # 断开终止
                break
```

**关键指标**：
- TTFB（首字延迟）：通常 < 500ms
- 心跳间隔：15s（可配置）
- 断开检测间隔：5s（可配置）

**实现**：[src/api/streaming.py](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/api/streaming.py)

### 6.5 OpenTelemetry 分布式追踪

**三层架构**：

1. **HTTP 层**：`TracingMiddleware` 自动为每个请求创建 Span
2. **RAG 流程层**：`@traced("rag_pipeline")` 装饰器
3. **节点层**：每个 LangGraph 节点独立 Span，包含输入/输出

**导出器配置**：
- 开发环境：Console Exporter
- 测试/生产：Jaeger OTLP Exporter

**实现**：[src/config/tracing.py](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/config/tracing.py)

---

## 7. 测试策略

### 7.1 测试金字塔

```
            ▲
           / \
          /   \         E2E 测试（多轮对话）
         / ─── \        ──────────────────
        /       \       集成测试（HTTP + DB）
       / ─────── \      ──────────────────
      /           \     单元测试（422 项）
     / ─────────── \    ──────────────────
```

### 7.2 测试分层

| 层级 | 目录 | 数量 | 说明 |
|------|------|------|------|
| 单元测试 | `tests/unit/` | ~400 | 模块级，Mock 依赖 |
| 集成测试 | `tests/integration/` | ~22 | HTTP + DB + Redis |
| E2E 测试 | `tests/e2e/` | - | 多轮对话流程 |
| 性能测试 | `tests/performance/` | - | Locust 压测 |

### 7.3 流式响应测试（V2.0 重点）

#### 单元测试：[tests/unit/test_streaming.py](file:///g:/PyCharmMiscProject/HealthAI-Platform/tests/unit/test_streaming.py)

| 测试类 | 覆盖内容 | 用例数 |
|--------|----------|--------|
| `TestSSEFormatting` | dict/string/done/heartbeat 格式化 | 7 |
| `TestStreamBasic` | 正常/空/单事件/多事件 | 4 |
| `TestStreamHeartbeat` | 空闲触发/快速不触发 | 2 |
| `TestStreamError` | 异常 error 事件/首次 yield 异常 | 2 |
| `TestStreamDisconnect` | 客户端断开终止生成 | 1 |
| `TestStreamMetrics` | TTFB/token/duration/error/connection | 5 |
| `TestStreamingMetricsFunctions` | 指标函数 + 注册校验 | 7 |

#### 集成测试：[tests/integration/test_streaming_e2e.py](file:///g:/PyCharmMiscProject/HealthAI-Platform/tests/integration/test_streaming_e2e.py)

| 测试类 | 覆盖内容 | 用例数 |
|--------|----------|--------|
| `TestNormalStreaming` | Content-Type/事件数/SSE headers | 3 |
| `TestHeartbeat` | 慢速触发/心跳是注释 | 2 |
| `TestErrorRecovery` | 异常 error/异常前内容保留 | 2 |
| `TestClientDisconnect` | 断开后服务端终止 | 1 |
| `TestPrometheusMetrics` | 指标注册/token/错误 | 3 |

### 7.4 测试运行

```bash
# 全部测试
python -m pytest tests/ -v --tb=short

# 仅单元测试
python -m pytest tests/unit/ -v

# 仅流式测试
python -m pytest tests/unit/test_streaming.py tests/integration/test_streaming_e2e.py -v

# Locust 压测
cd tests/performance && locust -f locustfile.py
```

### 7.5 测试 Fixture 设计

```python
# tests/conftest.py
@pytest.fixture
def mock_llm():
    """Mock LLM 客户端，避免外部 API 调用"""
    ...

@pytest.fixture
def require_redis():
    """Redis 不可用时自动跳过"""
    ...

@pytest.fixture
def require_db():
    """数据库不可用时自动跳过"""
    ...

@pytest.fixture
def sample_user_profile():
    """标准用户档案样本"""
    ...
```

---

## 8. 可观测性设计

### 8.1 三位一体可观测性

```
┌─────────────────────────────────────────────────┐
│              可观测性体系                        │
├─────────────────────────────────────────────────┤
│                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ Metrics  │  │  Traces  │  │  Logs    │    │
│  │ Prometheus│  │  Jaeger  │  │ Structured│   │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘    │
│        │             │             │           │
│        └─────────────┴─────────────┘           │
│                      │                          │
│                      ▼                          │
│              ┌──────────────┐                   │
│              │   Grafana    │                   │
│              │   仪表盘     │                   │
│              └──────────────┘                   │
└─────────────────────────────────────────────────┘
```

### 8.2 Prometheus 指标体系

#### HTTP 指标

| 指标 | 类型 | 说明 |
|------|------|------|
| `http_requests_total` | Counter | 请求总数 |
| `http_request_duration_seconds` | Histogram | 请求耗时 |
| `http_requests_in_progress` | Gauge | 在途请求数 |

#### RAG 指标

| 指标 | 类型 | 说明 |
|------|------|------|
| `rag_queries_total` | Counter | RAG 查询总数 |
| `rag_retrieval_duration_seconds` | Histogram | 检索耗时 |
| `rag_llm_duration_seconds` | Histogram | LLM 生成耗时 |
| `rag_safety_blocked_total` | Counter | 安全护栏拦截数 |

#### 流式响应指标（V2.0 新增）

| 指标 | 类型 | 说明 |
|------|------|------|
| `stream_active_connections` | Gauge | 当前流式连接数 |
| `stream_ttfb_seconds` | Histogram | 首字延迟 |
| `stream_duration_seconds` | Histogram | 总耗时 |
| `stream_tokens_total` | Counter | Token 输出数 |
| `stream_errors_total` | Counter | 错误数 |
| `stream_client_disconnects_total` | Counter | 客户端断开数 |

### 8.3 结构化日志

```json
{
  "timestamp": "2026-08-04T13:38:05.766",
  "logger": "SSEStreaming",
  "level": "INFO",
  "trace_id": "abc123",
  "span_id": "def456",
  "endpoint": "rag/chat",
  "message": "流式完成: duration=0.22s, tokens=5, ttfb=0.004s"
}
```

### 8.4 Grafana 仪表盘

- **HTTP 概览**：QPS / P50 / P99 / 错误率
- **RAG 链路**：检索耗时 / LLM 耗时 / 拦截率
- **流式响应**：TTFB 分布 / token 速率 / 断开率
- **资源使用**：CPU / 内存 / 连接池

---

## 9. 部署架构

### 9.1 Docker Compose 部署

```
┌──────────────────────────────────────────────────┐
│                  Docker Network                  │
├──────────────────────────────────────────────────┤
│                                                  │
│  ┌────────┐  ┌────────┐  ┌────────┐           │
│  │ FastAPI │  │ Streamlit│  │  Nginx │           │
│  │ :8000  │  │ :8501  │  │ :80    │           │
│  └────┬───┘  └────┬───┘  └────┬───┘           │
│       │           │            │                │
│       └───────────┴────────────┘                │
│                   │                              │
│  ┌────────────────▼──────────────────┐          │
│  │         数据存储层                │          │
│  │  ┌──────────┐  ┌──────────┐     │          │
│  │  │PostgreSQL│  │ ChromaDB │     │          │
│  │  │ :5432   │  │ :8000    │     │          │
│  │  └──────────┘  └──────────┘     │          │
│  │  ┌──────────┐                   │          │
│  │  │  Redis   │                   │          │
│  │  │  :6379   │                   │          │
│  │  └──────────┘                   │          │
│  └─────────────────────────────────┘          │
│                                                  │
│  ┌──────────────────────────────────┐          │
│  │         可观测性层               │          │
│  │  ┌──────────┐  ┌──────────┐     │          │
│  │  │Prometheus│  │  Grafana │     │          │
│  │  │ :9090    │  │ :3000    │     │          │
│  │  └──────────┘  └──────────┘     │          │
│  │  ┌──────────┐                   │          │
│  │  │  Jaeger  │                   │          │
│  │  │ :16686   │                   │          │
│  │  └──────────┘                   │          │
│  └─────────────────────────────────┘          │
└──────────────────────────────────────────────────┘
```

### 9.2 环境配置

| 环境 | 用途 | 配置文件 |
|------|------|----------|
| 开发 | 本地开发 | `.env` |
| 测试 | 自动化测试 | `tests/conftest.py` 设置默认值 |
| 生产 | 线上部署 | Docker env / K8s ConfigMap |

### 9.3 核心环境变量

```bash
# 数据库
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=healthai
POSTGRES_USER=healthai
POSTGRES_PASSWORD=*******

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# LLM
LLM_API_KEY=sk-*******
LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=https://api.openai.com/v1

# JWT
JWT_SECRET_KEY=*******
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# 追踪
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_SERVICE_NAME=healthai-backend
```

---

## 10. 版本规划与路线图

### 10.1 版本演进

```
V1.0 ────▶ V1.4 ────▶ V2.0 ────▶ V4.0
 │         │         │          │
 │         │         │          └── 社交/专家/UGC/多租户
 │         │         │
 │         │         └── 模块化重构/E2E/压测/运维文档
 │         │             OpenTelemetry/流式优化
 │         │
 │         └── LangGraph/混合检索/安全护栏/Docker
 │             Prometheus/分层测试
 │
 └── MVP：基础 RAG + FastAPI
```

### 10.2 V1.4 已完成（核心能力）

- [x] LangGraph 状态机编排（7 节点 + 条件边）
- [x] SQL + ChromaDB 混合检索
- [x] RRF 融合算法
- [x] 安全护栏（正则 + LLM 双重校验）
- [x] ClarificationNode 追问节点
- [x] food_validator 食物安全校验
- [x] 安全规则 YAML 热更新
- [x] Docker Compose 一键部署
- [x] Prometheus + Grafana 可观测性
- [x] 分层测试（单元/集成/E2E/性能）

### 10.3 V2.0 已完成（工程化提升）

- [x] **模块化分层重构**
  - `src/api/core/services/repositories/models/utils/config` 七层架构
  - 单向依赖，避免循环
- [x] **E2E 多轮对话测试**
  - 覆盖意图识别 → 检索 → 生成完整流程
- [x] **Locust 压测 + P99 验证**
  - 验证 RAG 查询 P99 < 3s
- [x] **运维文档（4 份）**
  - ARCHITECTURE / DEPLOYMENT / OPERATIONS / USER_MANUAL
- [x] **V4 预留**
  - DB 字段预留 / 模块占位 / Redis Key 规范
- [x] **OpenTelemetry 分布式追踪**
  - 三层追踪架构（HTTP / RAG / 节点）
  - Jaeger OTLP 导出器
- [x] **流式响应优化**
  - SSE 工具模块（心跳/断开/指标）
  - 异步非阻塞生成器
  - 6 个 Prometheus 指标
  - 39 项流式测试用例

### 10.4 V4.0 规划（业务扩展）

- [ ] **社交模块**
  - 关注/粉丝关系
  - 动态发布与互动
- [ ] **专家服务**
  - 专家认证体系
  - 付费咨询调度
- [ ] **UGC 内容管理**
  - 用户生成内容
  - 审核与推荐
- [ ] **多租户隔离**
  - 租户级数据隔离
  - 资源配额管理
- [ ] **移动端 SDK**
  - iOS / Android SDK
  - 离线缓存策略

### 10.5 版本发布节奏

| 版本 | 周期 | 状态 |
|------|------|------|
| V1.0 ~ V1.4 | 核心能力建设 | ✅ 已完成 |
| V2.0 | 工程化与可观测 | ✅ 已完成 |
| V2.x | 小幅迭代优化 | 📋 规划中 |
| V4.0 | 业务扩展 | 📋 规划中 |

---

## 11. 开发流程与规范

### 11.1 分支策略

```
main ──────────────────────────────────────▶
   │                                         │
   ├── feature/v2-streaming ──▶ merge       │
   ├── feature/v2-tracing ────▶ merge       │
   └── fix/xxx ────────────────▶ merge ─────┘
```

**分支命名**：
- `feature/<version>-<feature>`：新功能
- `fix/<issue>`：bug 修复
- `refactor/<module>`：重构

### 11.2 提交规范

遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Type**：
- `feat`：新功能
- `fix`：bug 修复
- `refactor`：重构
- `docs`：文档
- `test`：测试
- `chore`：杂项

**示例**：
```
feat(streaming): 添加 SSE 心跳保活与客户端断开检测

- 新增 src/api/streaming.py 模块
- 实现异步流式包装器，避免阻塞事件循环
- 添加 6 个 Prometheus 流式指标
- 编写 39 项单元/集成测试

Closes #123
```

### 11.3 代码规范

- **PEP 8**：遵循 Python 官方代码规范
- **类型注解**：所有公共函数必须添加类型注解
- **文档字符串**：模块/类/公共函数必须有 docstring
- **导入顺序**：标准库 → 第三方库 → 本项目模块
- **命名**：模块/函数 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE`

### 11.4 测试要求

- **新增功能必须附带单元测试**
- **测试覆盖率目标 > 80%**
- **PR 必须通过全部 CI 测试**
- **关键路径必须有集成测试**

### 11.5 Code Review 要点

- [ ] 是否符合分层架构（单向依赖）
- [ ] 是否有类型注解和 docstring
- [ ] 是否包含单元测试
- [ ] 是否有硬编码配置（应外置）
- [ ] 是否记录了必要的日志
- [ ] 是否考虑了错误处理
- [ ] 是否有性能问题（N+1 查询、阻塞调用）

---

## 12. 附录

### 12.1 关键文件索引

| 文件 | 说明 |
|------|------|
| [README.md](file:///g:/PyCharmMiscProject/HealthAI-Platform/README.md) | 项目总览 |
| [docs/ARCHITECTURE.md](file:///g:/PyCharmMiscProject/HealthAI-Platform/docs/ARCHITECTURE.md) | 架构设计 |
| [docs/DEPLOYMENT.md](file:///g:/PyCharmMiscProject/HealthAI-Platform/docs/DEPLOYMENT.md) | 部署指南 |
| [docs/OPERATIONS.md](file:///g:/PyCharmMiscProject/HealthAI-Platform/docs/OPERATIONS.md) | 运维手册 |
| [docs/USER_MANUAL.md](file:///g:/PyCharmMiscProject/HealthAI-Platform/docs/USER_MANUAL.md) | 用户手册 |
| [docs/DEVELOPMENT.md](file:///g:/PyCharmMiscProject/HealthAI-Platform/docs/DEVELOPMENT.md) | 本开发文档 |
| [config/safety_rules.yaml](file:///g:/PyCharmMiscProject/HealthAI-Platform/config/safety_rules.yaml) | 安全规则配置 |
| [src/api/streaming.py](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/api/streaming.py) | SSE 流式工具 |
| [src/config/tracing.py](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/config/tracing.py) | OpenTelemetry 配置 |
| [src/utils/metrics.py](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/utils/metrics.py) | Prometheus 指标 |
| [tests/unit/test_streaming.py](file:///g:/PyCharmMiscProject/HealthAI-Platform/tests/unit/test_streaming.py) | 流式单元测试 |
| [tests/integration/test_streaming_e2e.py](file:///g:/PyCharmMiscProject/HealthAI-Platform/tests/integration/test_streaming_e2e.py) | 流式集成测试 |

### 12.2 技术栈版本

| 类别 | 技术 | 版本 |
|------|------|------|
| 语言 | Python | 3.11+ |
| Web 框架 | FastAPI | 0.115 |
| AI 编排 | LangGraph + LangChain | 0.2 / 0.3 |
| LLM | OpenAI GPT-4o-mini | - |
| Embedding | text-embedding-3-small | 1024 维 |
| 关系数据库 | PostgreSQL | 15 |
| 向量数据库 | ChromaDB | 0.5 |
| 缓存 | Redis | 7 |
| 追踪 | OpenTelemetry | 1.24.0 |
| 监控 | Prometheus + Grafana | latest |
| 容器化 | Docker Compose | v2 |
| 测试 | pytest + httpx + Locust | 8.3 / 0.28 / 0.2 |

### 12.3 常用命令速查

```bash
# 启动开发服务
docker-compose up -d

# 本地开发
conda activate health_ai_env
uvicorn src.main:app --reload --port 8000

# 运行测试
python -m pytest tests/ -v --tb=short
python -m pytest tests/unit/test_streaming.py -v
python -m pytest tests/integration/test_streaming_e2e.py -v

# 查看指标
curl http://localhost:8000/metrics

# 查看 Jaeger
open http://localhost:16686

# 查看 Grafana
open http://localhost:3000  # admin / admin

# 压测
cd tests/performance && locust -f locustfile.py
```

### 12.4 故障排查指南

| 问题 | 排查步骤 |
|------|----------|
| RAG 查询慢 | 1. 检查 `/metrics` 的 `rag_*` 指标<br>2. 查看 Jaeger trace 定位慢节点<br>3. 检查 LLM API 延迟 |
| 流式响应卡顿 | 1. 检查 `stream_ttfb_seconds`<br>2. 查看是否有 `stream_client_disconnects_total`<br>3. 确认心跳间隔配置 |
| 安全护栏误拦截 | 1. 查看 `rag_safety_blocked_total`<br>2. 检查 `config/safety_rules.yaml`<br>3. 查看拦截日志的 category |
| LLM 调用失败 | 1. 检查 `LLM_API_KEY` 有效性<br>2. 查看应用日志的 LLM 错误<br>3. 确认 `LLM_BASE_URL` 可达 |

### 12.5 联系方式

- **项目仓库**：HealthAI-Platform
- **文档反馈**：提交 Issue 标记为 `documentation`
- **安全问题**：禁止在公开 Issue 中讨论安全问题，请私聊维护者

---

## 📝 文档变更记录

| 日期 | 版本 | 变更内容 | 作者 |
|------|------|----------|------|
| 2026-08-04 | V2.0 | 初始版本，覆盖 V1.4 + V2.0 全部内容 | HealthAI 团队 |

---

**文档结束** — 如有疑问，请提交 Issue 或联系维护者。
