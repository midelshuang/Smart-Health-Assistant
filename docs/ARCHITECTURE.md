# 架构文档

> HealthAI-Platform 系统架构设计，面向开发与运维团队。

## 1. 总体架构

### 1.1 架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         客户端层（Client）                          │
│   Web App   |   Mobile App   |   Streamlit Demo   |   curl/SDK    │
└────────────────────────────┬────────────────────────────────────────┘
                             │ HTTPS
┌────────────────────────────▼────────────────────────────────────────┐
│                       接入层（Gateway）                            │
│   Nginx 反向代理  |  TLS 终止  |  限流  |  CORS  |  日志           │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│                    应用层（Application Layer）                     │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                  FastAPI Application                       │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │  │
│  │  │ api/     │ │ services/│ │ core/    │ │ repositories/│  │  │
│  │  │ routes/  │ │ 业务服务 │ │ LangGraph│ │ 数据访问层   │  │  │
│  │  │ middleware│ │          │ │ 状态机  │ │              │  │  │
│  │  └──────────┘ └──────────┘ └────┬─────┘ └──────┬───────┘  │  │
│  │                               │                │          │  │
│  │  ┌──────────┐  ┌───────────┐ │                │          │  │
│  │  │ utils/   │  │ guideline │ │                │          │  │
│  │  │ 工具函数 │  │ _registry │ │                │          │  │
│  │  └──────────┘  └───────────┘ │                │          │  │
│  └───────────────────────────────┼────────────────┼──────────┘  │
└──────────────────────────────────┼────────────────┼─────────────┘
                                   │                │
              ┌────────────────────┴───┐    ┌───────▼──────────┐
              │   外部 LLM 服务        │    │   数据存储层     │
              │   OpenAI / 兼容 API    │    │ ┌──────────────┐ │
              │   ┌──────────────┐     │    │ │ PostgreSQL  │ │
              │   │ Embedding    │     │    │ │ 关系型数据  │ │
              │   │ embedding-2  │     │    │ └──────────────┘ │
              │   └──────────────┘     │    │ ┌──────────────┐ │
              │   ┌──────────────┐     │    │ │  ChromaDB   │ │
              │   │ Chat         │     │    │ │ 向量数据库  │ │
              │   │ glm-4-flash  │     │    │ └──────────────┘ │
              │   │ (ZhipuAI)    │     │    │ ┌──────────────┐ │
              │   └──────────────┘     │    │ │   Redis     │ │
              └────────────────────────┘    │ │ 缓存/限流   │ │
                                            │ └──────────────┘ │
                                            └──────────────────┘
```

### 1.2 核心设计原则

1. **关注点分离**：API 路由 / 业务服务 / 核心编排 / 数据访问 分层
2. **配置外置**：敏感信息走 `.env`，业务规则走 `config/*.yaml`
3. **热更新优先**：合规规则、限流策略支持运行时变更
4. **fail-safe 优先于 fail-fast**：安全规则加载失败保留旧配置而非崩溃
5. **可观测性内建**：每个节点产出指标与日志，全链路可追溯
6. **混合检索架构**：向量语义检索 + BM25 关键词检索双路召回，RRF 融合优化排序
7. **语义分块优先**：基于段落/章节/句子的智能分块，确保查询精度
8. **双写存储**：ChromaDB（向量）+ PostgreSQL document_chunks（全文），支持多模态检索

## 2. 模块化分层结构

```
src/
├── api/                      # 应用层
│   ├── routes/               # 路由定义
│   │   ├── auth.py           # 认证（注册/登录/JWT）
│   │   ├── rag.py            # RAG 查询接口
│   │   ├── plan.py           # 饮食计划接口
│   │   ├── diet.py           # 饮食记录接口
│   │   ├── user.py           # 用户档案接口
│   │   └── v2.py             # V2 占位接口
│   ├── middleware/           # 中间件
│   │   ├── monitoring.py    # 指标采集
│   │   ├── tracing.py        # 链路追踪
│   │   └── version_control.py# API 版本控制
│   └── streaming.py          # SSE 流式响应工具
│
├── core/                     # 核心编排层
│   ├── nodes/                # LangGraph 节点
│   │   └── __init__.py       # 所有节点函数
│   ├── graph_builder.py      # 状态机编排
│   ├── state.py              # RAGState TypedDict 定义
│   ├── safety_checker.py     # 安全护栏（正则 + LLM）
│   ├── rrf_fusion.py         # RRF 跨源融合算法
│   ├── llm_client.py         # LLM 客户端封装
│   ├── embedding.py          # Embedding 服务封装
│   ├── guideline_registry.py # 指南注册表（年龄/疾病/地区路由）
│   ├── tracing_helper.py     # 追踪装饰器
│   └── loaders/              # 多模态加载器
│       ├── pdf_loader.py
│       └── audio_loader.py
│
├── services/                 # 业务服务层
│   ├── rag_service.py        # RAG 管道（封装 LangGraph）
│   ├── auth_service.py       # 认证业务
│   ├── plan_service.py       # 饮食计划业务
│   ├── food_parser.py       # 食物解析
│   ├── adjustment_service.py # 计划动态调整
│   ├── document_processor.py # 文档处理（语义分块）
│   ├── nutrition_calc.py    # 营养计算
│   ├── login_limiter.py      # 登录限流
│   ├── base_service.py       # 服务基类
│   └── cache_service.py     # 缓存服务
│
├── repositories/             # 数据访问层
│   ├── vector_store.py       # ChromaDB 访问
│   ├── chunk_repository.py   # PostgreSQL document_chunks（BM25 检索）
│   ├── session.py            # PostgreSQL 会话管理
│   └── init_db.py            # 数据库初始化
│
├── models/                   # 数据模型层
│   └── db_models.py          # SQLAlchemy ORM 模型
│
├── utils/                    # 工具函数层
│   ├── semantic_chunker.py   # 语义分块器（核心）
│   ├── food_validator.py     # 食物安全校验
│   ├── nutrition_calc.py     # 营养计算
│   ├── text_cleaner.py       # 文本清洗
│   ├── password_policy.py    # 密码策略
│   ├── redis_client.py       # Redis 客户端
│   ├── logger.py             # 日志配置
│   └── metrics.py            # Prometheus 指标
│
└── config/                   # 配置层
    ├── settings.py           # 全局配置（读取 .env）
    ├── safety_rule_loader.py # 安全规则热加载器
    ├── redis_key_spec.py     # Redis Key 规范
    └── tracing.py            # OpenTelemetry 配置
```

### 2.1 分层依赖规则

- `api/` 可依赖 `services/` 与 `models/`
- `services/` 可依赖 `core/` 与 `repositories/`
- `core/` 可依赖 `repositories/` 与 `utils/`（但不应依赖 `services/`）
- `repositories/` 仅依赖 `models/` 与外部 SDK
- `utils/` 无内部依赖（纯函数）
- `config/` 无内部依赖（仅读 .env 与 YAML）
- `guideline_registry.py` 是无状态只读模块，`core/` 和 `services/` 均可读取

违反依赖方向会触发循环引用，需通过依赖注入解耦。

## 3. LangGraph 状态机

### 3.1 完整状态流转图

```
START
  ↓
[ safety_check ] ─── (拦截) ──→ END（返回拒绝话术）
  ↓ (通过)
[ intent_recognizer ]
  ↓
[ query_rewriter ]
  ↓ ┌──────────────┬─────────────────┐
  │ (food_search) │ (guideline_query)│ (hybrid/unknown)
  ↓               ↓                  ↓
[sql_retriever] [vector_retriever]  并行执行
                                      ↓
                                [evaluator]
                                      ↓
                  ┌─────────────────┼─────────────────┐
                  ↓                 ↓                 ↓
              (通过)          (未通过+可改写)    (未通过+达上限)
                  ↓                 ↓                 ↓
            [fusion]        [query_rewriter]    [clarification]
                  ↓                              ↓
            [generator]                  (触发追问)→ END
                  ↓                              ↓
                 END                       (无需追问)→ [fusion]
```

### 3.2 混合检索架构（vector_retriever 内部）

```
vector_retriever_node(state)
  │
  ├── 路径1: 向量语义检索
  │   └── ChromaDB.query() → top_k 结果
  │
  ├── 路径2: PostgreSQL 关键词检索（BM25 风格）
  │   └── chunk_repository.keyword_search()
  │       ├── ILIKE 模糊匹配
  │       ├── 关键词位置加权排序
  │       └── where_filter 元数据过滤
  │
  └── [RRF 融合] → fused_results
      score = 1/(60+rank_vector) + 1/(60+rank_keyword)
      │
      └── 元数据重排序（匹配用户年龄/地区/疾病标签）
```

### 3.3 RAGState 状态字段

```python
class RAGState(TypedDict, total=False):
    # 输入
    original_query: str
    conversation_context: List[Dict[str, str]]
    user_id: str
    user_profile: Dict[str, Any]          # 用户档案（年龄/疾病/BMI）

    # 意图识别
    intent: Literal["food_search", "guideline_query", "hybrid",
                    "plan_generation", "unknown", "blocked"]
    intent_confidence: float

    # Query 改写
    rewritten_query: str
    rewrite_count: int
    max_rewrite_count: int

    # 检索结果（双路）
    sql_results: List[Dict[str, Any]]         # 路径1: 食物成分 SQL
    vector_results: List[Dict[str, Any]]      # 路径2: 向量检索
    keyword_results: List[Dict[str, Any]]     # 路径3: BM25 关键词检索

    # 评估
    eval_passed: bool
    eval_feedback: str

    # 追问
    needs_clarification: bool
    clarification_question: str

    # RRF 融合
    fused_results: List[Dict[str, Any]]

    # 指南路由（两阶段检索）
    matched_guidelines: List[str]            # 阶段1: 匹配的指南标题

    # 安全护栏
    safety_blocked: bool
    safety_category: str
    safety_reason: str

    # 输出
    generated_answer: str
    sources: List[Dict[str, str]]
```

### 3.4 条件路由策略

**意图分流（intent_recognizer → 检索器）**：

- `food_search` → 仅 SQL 检索
- `guideline_query` → 向量检索 + BM25 关键词检索双路
- `hybrid` / `unknown` → SQL + 向量 + BM25 三路并行检索
- `plan_generation` → 跳过检索，直接计划生成
- `blocked`（安全拦截）→ 直接 END

**指南路由（两阶段检索）**：

- 阶段1：`guideline_registry.select_guidelines_by_profile(profile)` 根据用户年龄/疾病/BMI 匹配指南列表
- 阶段2：将匹配的指南标题作为 `where_filter` 传入检索器，精确限定搜索范围

**评估循环（evaluator → 下一节点）**：

- 通过 → `fusion`（RRF 融合 + 生成）
- 未通过 + 改写次数 < max → `query_rewriter`（重试）
- 未通过 + 改写次数 = max → `clarification`（追问检查）

## 4. 指南注册表与两阶段检索

### 4.1 指南注册表（GuidelineRegistry）

```python
GUIDELINE_REGISTRY = {
    "中国居民膳食指南（2022）": {
        "guideline_title": "中国居民膳食指南（2022）",
        "abstract": "平衡膳食权威准则，涵盖食物多样、吃动平衡等八条核心推荐",
        "audience": "general",
        "disease_tags": [],
        "region": "全国",
        "age_groups": ["infant", "preschool", "child", "adolescent",
                       "youth", "middle_aged", "elderly"],
    },
    # ... 共 8 份主体指南
}
```

**核心函数**：

- `match_guideline(file_name)`：PDF 文件名 → 注册表条目
- `select_guidelines_by_profile(profile)`：用户画像 → 匹配的指南集合
- `build_guide_catalog_chunks()`：为每份指南生成摘要 chunk

### 4.2 年龄组映射

| 年龄组      | 年龄范围 | 适用指南            |
| ----------- | -------- | ------------------- |
| infant      | 0-1岁    | 婴幼儿喂养指南      |
| preschool   | 2-5岁    | 学龄前儿童指南      |
| child       | 6-12岁   | 儿童青少年指南      |
| adolescent  | 13-17岁  | 儿童青少年指南      |
| youth       | 18-35岁  | 成人肥胖/肾病等指南 |
| middle_aged | 36-60岁  | 成人系列指南        |
| elderly     | 60+岁    | 老年人指南          |

## 5. 语义分块架构

### 5.1 SemanticChunker 策略

```
原始文本
    │
    ▼
[1. 按段落切分] ─── 尊重原文结构
    │
    ▼
[2. 标题感知] ─── 识别章节标题 → 归入后续块
    │
    ▼
[3. 段落聚合] ─── 目标 500 字符，最小 300，最大 700
    │
    ▼
[4. 超长二次切分] ─── 按句子边界（。！？.!?；;）
    │
    ▼
[5. 重叠窗口] ─── 相邻块共享 80 字符，保持上下文连贯
    │
    ▼
[6. 生成 payload] ─── chunk_id + content + metadata
```

### 5.2 分块参数

| 参数              | 值   | 说明                         |
| ----------------- | ---- | ---------------------------- |
| TARGET_CHUNK_SIZE | 500  | 目标分块大小（字符）         |
| MIN_CHUNK_SIZE    | 300  | 最小分块大小                 |
| MAX_CHUNK_SIZE    | 700  | 最大分块大小                 |
| OVERLAP_SIZE      | 80   | 重叠窗口大小                 |
| HEADER_PATTERN    | 正则 | 识别「第X章/1.1/一、」等标题 |
| SENTENCE_END      | 正则 | 识别中英文句末标点           |

### 5.3 入库双写流程

```
DocumentProcessor.process_file(file_path)
    │
    ▼
[1. 提取文本] ─── PDFLoader / AudioLoader
    │
    ▼
[2. 语义分块] ─── SemanticChunker.chunk_with_headers()
    │
    ▼
[3. 生成 chunk_id] ─── "guideline_xxx_{index}"
    │
    ▼
[4. 双写存储]
    ├── ChromaDB.add_documents() ─── 向量 + 元数据
    └── PostgreSQL create_chunks() ─── 全文 + 元数据
    │
    ▼
[5. 一致性校验] ─── chunk_id 一一对应
```

## 6. 混合检索架构

### 6.1 三路召回

```
用户查询
    │
    ▼
[意图识别]
    │
    ├── food_search ───▶ [路径A: SQL 精确检索]
    │                    SELECT * FROM nutrition_facts
    │                    WHERE food_name ILIKE '%query%'
    │
    ├── guideline_query ───▶ [路径B: 向量语义检索]
    │                        ChromaDB.query(query_embedding)
    │                    +   [路径C: BM25 关键词检索]
    │                        PostgreSQL document_chunks
    │                        WHERE content ILIKE '%keyword%'
    │
    └── hybrid/unknown ───▶ [三路并行]
                              SQL + 向量 + BM25
    │
    ▼
[RRF 融合]
    score = 1/(60+rank_sql) + 1/(60+rank_vector) + 1/(60+rank_bm25)
    │
    ▼
[元数据重排序]
    匹配用户 age_group / disease_tags / region
    │
    ▼
[Evaluator 评估] → 通过/改写/追问
```

### 6.2 PostgreSQL BM25 检索

```python
# chunk_repository.keyword_search()
# 使用 PostgreSQL 原生 tsvector + ts_rank_cd 实现 BM25 风格检索：
# 1. 文档入库时自动生成 content_tsvector（GIN 倒排索引）
# 2. plainto_tsquery 分词 + ts_rank_cd(normalization=32) 文档长度归一化
# 3. where_filter 元数据过滤（$or/$and/$in 操作符）
# 4. 支持关键词位置加权排序

SELECT chunk_id, content, guideline_title, source_type,
       ts_rank_cd(content_tsvector, plainto_tsquery('关键词1 关键词2'), 32) AS score
FROM document_chunks
WHERE source_type IN (...) AND guideline_title IN (...)
ORDER BY score DESC
LIMIT :top_k
```

### 6.3 RRF 融合算法

```python
# rrf_fusion.py
def rrf_fusion(results_list, k=60):
    """Reciprocal Rank Fusion"""
    scores = {}
    for results in results_list:
        for rank, doc in enumerate(results):
            doc_id = doc["id"]
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

## 7. 安全护栏架构

### 7.1 双重校验流程

```
用户查询
    ↓
[ 正则规则匹配 ] ─── (命中) ──→ 返回拒绝回复
    ↓ (未命中)
[ LLM 语义判断 ] ─── (危险) ──→ 返回拒绝回复
    ↓ (安全)
继续 RAG 流程
```

### 7.2 规则外置与热更新

```
config/safety_rules.yaml (规则文件)
         ↓
SafetyRuleLoader (单例，热加载器)
  │
  ├─ 首次访问：加载 YAML，缓存 SafetyRulesConfig
  ├─ 后续访问：检测 mtime
  │   ├─ 未变化：返回缓存
  │   └─ 已变化：重新加载 + 更新缓存
  └─ 加载失败：保留上一次配置（fail-safe）
```

### 7.3 拦截类别

| 类别                              | 拦截示例             | 处理             |
| --------------------------------- | -------------------- | ---------------- |
| `prescription_drug`               | "帮我开点降糖药"     | 引导就医         |
| `diagnosis_request`               | "我是不是得了糖尿病" | 引导就医         |
| `extreme_diet`                    | "21天辟谷"           | 提供科学减重建议 |
| `treatment_plan`                  | "帮我制定治疗方案"   | 引导专科就诊     |
| `special_population_intervention` | "孕妇能吃什么药"     | 建议咨询专科医生 |

## 8. 数据存储架构

### 8.1 PostgreSQL（关系型）

存储结构化数据：

| 表名                   | 用途         | 关键字段                                        |
| ---------------------- | ------------ | ----------------------------------------------- |
| `users`                | 用户档案     | id, username, email                             |
| `user_profiles`        | 用户画像     | age, gender, height_cm, weight_kg, diseases     |
| `diet_plans`           | 饮食计划     | user_id, plan_type, target_calories             |
| `diet_logs`            | 饮食记录     | user_id, food_name, quantity, meal_type         |
| `nutrition_facts`      | 食物营养成分 | food_name, calories, protein, carbs, fat        |
| `glycemic_index_facts` | GI 升糖指数  | food_name, gi_value, food_group, gi_category    |
| `document_chunks`      | 文档分块全文 | chunk_id, content, source_type, guideline_title |
| `auth_logs`            | 认证日志     | user_id, action, ip_address                     |

**document_chunks 表（BM25 检索核心）**：

```sql
CREATE TABLE document_chunks (
    id SERIAL PRIMARY KEY,
    chunk_id VARCHAR(128) UNIQUE NOT NULL,  -- 与 ChromaDB 双写关联
    source_type VARCHAR(32) NOT NULL,        -- guideline/new_food/gi_data/expert_media
    content TEXT NOT NULL,                   -- 分块全文（用于 BM25）
    guideline_title VARCHAR(500),           -- 指南标题
    age_group VARCHAR(200),                 -- 年龄组
    disease_tags VARCHAR(500),              -- 疾病标签
    region VARCHAR(50),                     -- 地区
    char_count INTEGER,                     -- 字符数
    metadata_json JSONB,                    -- 扩展元数据
    created_at TIMESTAMPTZ DEFAULT NOW()
);
-- 索引
CREATE INDEX ix_document_chunks_source_type ON document_chunks(source_type);
CREATE INDEX ix_document_chunks_guideline_title ON document_chunks(guideline_title);
CREATE INDEX ix_document_chunks_source_type_guideline_title ON document_chunks(source_type, guideline_title);
```

### 8.2 ChromaDB（向量型）

存储非结构化文档的向量索引：

| 属性       | 值                                         |
| ---------- | ------------------------------------------ |
| Collection | `authoritative_sources`                    |
| Embedding  | `text-embedding-3-small`（1024 维）        |
| 数据源     | 指南 PDF + 新食品 PDF + GI 数据 + 专家媒体 |
| 分块总数   | ~915 条                                    |

**元数据字段**：

```python
{
    "chunk_id": "guideline_xxx_0",
    "source_type": "guideline",          # guideline/new_food/gi_data/expert_media
    "file_name": "儿童青少年肥胖食养指南.pdf",
    "guideline_title": "儿童青少年肥胖食养指南（2024年版）",
    "age_group": "child,adolescent",
    "region": "全国",
    "disease_tags": "肥胖,超重",
    "char_count": 492,
    "chunk_index": 0,
}
```

### 8.3 数据双写一致性

```
                    ┌─────────────────────────┐
                    │   文档分块（SemanticChunker）│
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │     chunk_id 生成       │
                    │  "guideline_xxx_{idx}"  │
                    └────────────┬────────────┘
                                 │
                 ┌───────────────┴───────────────┐
                 ▼                               ▼
    ┌──────────────────┐               ┌──────────────────┐
    │   ChromaDB       │               │  PostgreSQL      │
    │   向量 + 元数据   │               │  全文 + 元数据    │
    └──────────────────┘               └──────────────────┘
                 │                               │
                 └───────────────┬───────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   一致性校验（chunk_id）  │
                    └─────────────────────────┘
```

### 8.4 Redis（缓存与限流）

Key 命名规范：

```
healthai:{env}:{module}:{key}

例：
healthai:prod:cache:rag_query:{md5(query)}    # RAG 查询缓存
healthai:prod:ratelimit:login:{ip}            # 登录限流计数
healthai:prod:session:{user_id}               # 用户会话
healthai:prod:lock:plan_generation:{user_id}  # 计划生成分布式锁
```

## 9. 可观测性架构

### 9.1 三柱观测

| 维度    | 实现                   | 用途           |
| ------- | ---------------------- | -------------- |
| Metrics | Prometheus + Grafana   | 趋势分析、告警 |
| Logs    | 结构化 JSON 日志 + ELK | 故障定位、审计 |
| Traces  | OpenTelemetry          | 跨服务链路追踪 |

### 9.2 关键指标

**业务指标**：

- `rag_query_total{intent}` - RAG 查询总数（按意图分）
- `safety_blocked_total{category}` - 安全拦截总数（按类别分）
- `clarification_triggered_total` - 追问触发次数
- `plan_generated_total` - 计划生成总数
- `hybrid_search_total{path}` - 混合检索各路调用次数

**技术指标**：

- `http_request_duration_seconds` - HTTP 请求延迟
- `rag_query_duration_seconds` - RAG 查询端到端延迟
- `vector_retriever_duration_seconds` - 向量检索耗时
- `keyword_search_duration_seconds` - BM25 检索耗时
- `db_connection_pool_active` - DB 连接池使用
- `redis_operation_duration_seconds` - Redis 操作延迟

## 10. 性能架构

### 10.1 性能目标

| 路径                   | P50     | P95     | P99     | 备注             |
| ---------------------- | ------- | ------- | ------- | ---------------- |
| `/api/health`          | < 50ms  | < 100ms | < 200ms | 健康检查         |
| 安全拦截路径           | < 200ms | < 500ms | < 800ms | 正则快速匹配     |
| `food_search` 查询     | < 1s    | < 2s    | < 3s    | SQL 检索         |
| `guideline_query` 查询 | < 2s    | < 4s    | < 6s    | 向量 + BM25 双路 |
| `hybrid` 查询          | < 3s    | < 5s    | < 8s    | 三路检索 + RRF   |

### 10.2 性能优化策略

**缓存层**：

- RAG 查询结果缓存（key = md5(query + user_profile)）
- 食物成分表缓存（Redis Hash）
- Embedding 缓存（避免重复计算）
- BM25 检索结果缓存

**并行化**：

- SQL + 向量 + BM25 检索并行执行（LangGraph Send）
- 多文档 Embedding 批量计算

**降级策略**：

- LLM 不可用 → 降级为模板回复
- ChromaDB 不可用 → 降级为仅 SQL + BM25 检索
- PostgreSQL 不可用 → 降级为仅向量检索
- 改写循环超时 → 强制进入追问或融合

## 11. 扩展性设计

### 11.1 水平扩展

- FastAPI 无状态，可直接横向扩展（多实例 + 负载均衡）
- ChromaDB 支持分布式部署
- PostgreSQL 读写分离

### 11.2 模块化扩展点

新增 RAG 节点：

1. 在 `src/core/nodes/__init__.py` 实现 `xxx_node(state)` 函数
2. 在 `src/core/graph_builder.py` 注册节点并添加边
3. 更新 `RAGState` 添加必要字段
4. 编写单元测试

新增安全规则：

1. 编辑 `config/safety_rules.yaml` 添加 regex_rule
2. 添加对应 category_response
3. 无需重启，自动热加载

新增检索源：

1. 在 `src/core/nodes/__init__.py` 添加新的 retriever 路径
2. 在 `RAGState` 添加对应结果字段
3. 在 RRF 融合中注册新路径的分数

新增食物校验规则：

1. 在 `src/utils/food_validator.py` 的对应映射中添加
2. 编写单元测试覆盖新规则

## 12. 版本演进

### V1.4 ✅（核心能力）

- LangGraph 状态机编排
- SQL + ChromaDB 混合检索
- RRF 融合算法
- 安全护栏（正则 + LLM）
- ClarificationNode 追问节点
- food_validator 食物安全校验

### V2.0 ✅（工程化）

- 模块化分层重构
- E2E 多轮对话测试
- Locust 压测 + P99 验证
- 运维文档（4 份）
- OpenTelemetry 分布式追踪
- 流式响应优化

### V3.0 ✅（智能升级）

- **语义分块**：SemanticChunker 替代固定分块
- **混合检索升级**：向量 + BM25 双路召回 + RRF 融合
- **双写存储**：ChromaDB（向量）+ PostgreSQL document_chunks（全文）
- **指南注册表**：GuidelineRegistry 两阶段检索路由
- **GI 数据入库**：升糖指数数据双写支持
- **健康检查**：服务健康状态 API

### V4.0 📋（业务扩展）

- 社交模块（关注/粉丝/动态）
- 专家服务（认证/付费咨询）
- UGC 内容管理
- 多租户隔离
