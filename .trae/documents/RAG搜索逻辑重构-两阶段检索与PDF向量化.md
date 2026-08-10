# RAG 搜索逻辑重构：两阶段检索 + 真实 PDF 向量化

## Context（为什么做这件事）

用户希望「帮我规划/减肥食谱/食养规划/减肥计划」类问题走一条清晰的检索链路：
收集个人信息（地区/年龄/疾病/身高/体重/目标）→ 向量+BM25 混合检索 → 先按用户画像匹配到具体指南（成人/儿童青少年/高尿酸…）→ 再在该指南内按地区查食谱 → 检索结果+问题交 LLM 整理。营养成分类问题查 nutrition_facts 表，闲聊类直接 LLM 回答。

勘探后发现**当前实现离目标有三处实质差距**（已逐行核实）：

1. **真实权威 PDF 从未被向量化。** `raw_data/raw/guidelines/` 下 16 份食养指南 PDF（含分地区食谱，含一份 74MB 的《中国居民膳食指南2022》）目前不在 ChromaDB 里。ChromaDB 现存数据全部来自 [seed_knowledge_base.py](file:///g:/PyCharmMiscProject/HealthAI-Platform/scripts/seed_knowledge_base.py) 的 13 条**人工硬编码摘要**——它们 source_type 正确（`guideline`/`expert_media`）、也已带 `age_group`/`region` 元数据，但内容浅、无分地区食谱细节。`academic/` 目录为空，所谓"已向量化的专家媒体"其实就是 seed 里 5 条 expert_media 摘要。

2. **检索未按用户画像路由到具体指南。** [nodes/__init__.py:355](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/core/nodes/__init__.py#L355) 的 `_build_metadata_filter` 只过滤 `source_type ∈ [guideline, expert_media]`，不区分成人/儿童/高尿酸等指南；重排序函数读的 `guideline_title` 字段在 chunk 元数据里根本不存在。结果是「28岁高尿酸男」和「10岁肥胖儿童」检索到的指南范围完全相同。

3. **信息收集不完整 + 意图路由错配。** [info_collector_node](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/core/nodes/__init__.py#L1719) 只在 age/gender 缺失时追问，不收集疾病，地区/身高/体重缺失不阻断；[_route_after_intent](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/core/graph_builder.py#L57) 把 `unknown`（闲聊）也丢进 hybrid 检索，浪费算力且可能拉回无关营养行。

另外发现一个**潜伏 bug**：[document_processor.py:25](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/services/document_processor.py#L25) 常量 `SOURCE_TYPE_GUIDE="guide"`/`SOURCE_TYPE_EXPERT="expert"`，与检索侧 `guideline`/`expert_media` 不一致。当前因没走 DocumentProcessor 入库真实 PDF 而未爆发，但一旦用它入库就会全量过滤为空——必须在入库真实 PDF 前统一。同时 [rag.py:204](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/api/routes/rag.py#L204) 的 `/rag/index` 端点用了未定义的 `pipeline` 变量（既有 bug）。

**已确认的两个决策**：①指南路由用「注册表定主标签 + 摘要向量参与语义匹配」的混合方式；②追问用分级策略（年龄/身高/体重/目标硬阻断，地区软提示可跳过，疾病默认"无"+提示）。

## 实现方案

### 阶段 P0：统一 source_type 词表 + 修 /rag/index Bug
- `src/services/document_processor.py`：`SOURCE_TYPE_GUIDE="guideline"`、`SOURCE_TYPE_EXPERT="expert_media"`、`SOURCE_SUBDIR_MAP` 的 key 同步（`guideline`→`guidelines`、`expert_media`→`academic`）。
- `src/services/rag_service.py`：`index_documents` 默认 `source_type="guideline"`，docstring 同步。
- `src/api/routes/rag.py`：`index_documents` 函数体 `pipeline.index_documents(...)` → `get_pipeline().index_documents(...)`。

### 阶段 P1：新建指南注册表模块 `src/core/guideline_registry.py`
覆盖 8 份主体指南（问答版与主体版通过 `match_guideline` 归一到同一条目）。**元数据字段名对齐现有重排代码已读的 `age_group`/`region`**（不新造 `age_groups`/`region_scope`，避免重排失效），新增 `guideline_title`/`disease_tags`/`doc_type`。
- `GUIDELINE_REGISTRY`：每条含 `guideline_title`、`abstract`（一句话摘要）、`audience`、`disease_tags`、`region`（全国）、`age_group`（comma-joined，复用 [AGE_GROUP_MAPPING](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/core/nodes/__init__.py#L1535) 的 key）、`file_patterns`。
- `DISEASE_KEYWORD_TO_TAG`：糖尿病/高血压/高尿酸(痛风)/肥胖(超重)/肾病/脑卒中(中风)/骨质疏松/肌少症 → disease_tag。
- `DISEASE_TAG_TO_GUIDELINES`：仅映射有对应食养 PDF 的疾病（高尿酸/肥胖/慢性肾脏病/脑卒中/骨质疏松/肌少症）；糖尿病/高血压无食养 PDF → 不映射，走《中国居民膳食指南》基础 + 软提示。
- 函数：`match_guideline(file_name)`（剥离「附件N.」前缀与「（问答）/(问答)/问答」后缀，双向子串匹配，兼容 Z-Library 后缀）、`extract_disease_tags(text)`、`normalize_diseases(raw)`、`age_matches_guideline(age, entry)`、`select_guidelines_by_profile(profile)`、`build_guide_catalog_chunks()`（每指南 1 条 `doc_type=guide_catalog` chunk，内容=标题+摘要+受众+疾病标签，参与阶段2语义匹配）。
- `select_guidelines_by_profile` 逻辑：疾病→对应食养指南（用 age 二次过滤，避免儿童拿到成人指南）；始终叠加《中国居民膳食指南（2022）》；无疾病成人 BMI≥24 追加《成人肥胖食养指南》。

### 阶段 P2：入库元数据增强
- `src/services/document_processor.py` `process_file`：PDF 处理后，用 `match_guideline(file_path)` 注入 `guideline_title`/`age_group`/`region`/`disease_tags`/`audience`/`doc_type=guideline_content` 到每个 chunk 的 metadata（ChromaDB 元数据值须标量，list 用 `,`join）。未匹配的标 `doc_type=guideline_content_unmapped` 便于排查。
- `build_guide_catalog_chunks()` 产出的摘要 chunk 在重建脚本中写入。

### 阶段 P3：重建入库脚本 `scripts/reindex_guidelines.py`
- 删除并重建 collection → 用增强后的 `DocumentProcessor` 入库 `raw_data/raw/guidelines/` 全部 PDF（`source_type=guideline`）→ 写入 guide_catalog 摘要 chunk → **保留现有 seed 的 5 条 expert_media 摘要**（减重误区/运动营养/蛋白质吸收/膳食纤维/江南地区，作为专家媒体补充与地区范例）。提供 `--keep-seed-expert` 开关。
- 风险：74MB《中国居民膳食指南2022》用 pypdf 解析可能慢/内存高——脚本对该文件加 try/except 与超时，失败时仅告警不中断其余入库。
- 入库后清空 Redis 查询缓存（`cache_service` 会缓存旧结构结果，[rag_service.py:86](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/services/rag_service.py#L86)）。

### 阶段 P4：info_collector 增强
[nodes/__init__.py:1719](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/core/nodes/__init__.py#L1719) `info_collector_node`：
- 新增 `_extract_diseases_from_query`（调 `extract_disease_tags`）；DB 档案疾病来源用 `health_tags` + `medical_conditions`(JSONB) 经 `normalize_diseases` 归一。
- 新增 `_accumulate_profile_from_context(conversation_context, profile)`：扫描历史 user 消息 `setdefault` 补全 age/height/weight/region/gender/disease，解决 guest 无 DB profile 的多轮追问信息丢失（依赖前端把历史塞进 `conversation_context`）。
- 调 `select_guidelines_by_profile(profile)` 选指南 → 写入 state `selected_guidelines`。
- 分级追问：age/height/weight 缺失→硬阻断 END 追问；goal 默认 `health_maintenance` 不阻断；region 缺失→软提示（可跳过）入 `soft_clarifications`；disease 空→软提示「按一般人群处理，若有…请告知」。
- BMI 用 height/weight 重算；`rewritten_query` 仍覆盖为 `_build_plan_rag_query` 结果（保留现有机制）。
- `src/core/state.py` `RAGState` 新增 `selected_guidelines`/`diseases`/`soft_clarifications` 字段。

### 阶段 P5：两阶段检索阶段2
[nodes/__init__.py:446](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/core/nodes/__init__.py#L446) `vector_retriever_node` + `_build_metadata_filter`：
- `_build_metadata_filter(profile, intent, selected_guidelines)` 真过滤：plan_generation 时用 `$or` 组合「(`source_type=guideline` AND `guideline_title ∈ selected`) OR (`source_type=expert_media`)」。`expert_media` 无 `guideline_title` 字段须 `$or` 单独放行，否则被误排除。ChromaDB 0.5.5 支持 `$and`/`$or`。
- `vector_retriever_node` 读 `state["selected_guidelines"]` 传入过滤器；`_keyword_search`(BM25) 已接受 `where_filter` 自动收缩候选范围，无需改；`_rerank_results_by_metadata` 现在元数据真有 `age_group`/`region`，重排生效（建议对 `guideline_title ∈ selected` 额外小幅加分）。
- `_build_plan_rag_query` 微调：where 已锁指南，查询聚焦「地区+目标+疾病名+食物偏好」以在指南内定位食谱 chunk，删掉无对应 PDF 的硬编码片段。

### 阶段 P6：意图路由修正
[graph_builder.py:57](file:///g:/PyCharmMiscProject/HealthAI-Platform/src/core/graph_builder.py#L57) `_route_after_intent`：`unknown` → 直接 `generator`（闲聊直答，走 SYSTEM_PROMPT_LLM），不走 hybrid 检索。`build_rag_graph` 的 `add_conditional_edges` 映射表加 `"generator":"generator"`。`generator_node` 已能处理空检索结果，无需改。

### 阶段 P7：软提示透传
`plan_generator_node` 末尾把 `state["soft_clarifications"]` 追加到 `generated_answer` 末尾。guest 持久化跳过已由 `generate_plan` 的 `user_exists` 判断覆盖，无需改。

## 关键文件
- 新建 `src/core/guideline_registry.py`（注册表 + 阶段1选指南 + catalog chunk）
- `src/core/nodes/__init__.py`（info_collector_node / _build_metadata_filter / vector_retriever_node / plan_generator_node / _build_plan_rag_query / 新增 _extract_diseases_from_query / _accumulate_profile_from_context）
- `src/core/state.py`（新增 selected_guidelines/diseases/soft_clarifications）
- `src/services/document_processor.py` + `src/services/rag_service.py`（source_type 统一 + 元数据注入）
- `src/core/graph_builder.py`（_route_after_intent unknown→generator）
- 新建 `scripts/reindex_guidelines.py`（重建入库）
- `src/api/routes/rag.py`（修 /rag/index bug）

## 验证
1. **入库校验**：跑 `python scripts/reindex_guidelines.py --keep-seed-expert`；用 Chroma 客户端 `collection.get(where={"doc_type":"guide_catalog"})` 应返回 8 条；`where={"guideline_title":"成人肥胖食养指南（2024年版）"}` 应非空且 `source_type=guideline`；清空 Redis 查询缓存。
2. **单元校验**：`match_guideline` 对 16 个 PDF 文件名逐一断言（注意「附件2/4/6 问答」与「(中国营养学会)(Z-Library)」后缀）；`select_guidelines_by_profile` 断言 `{age:10,diseases:[肥胖]}`→含儿童青少年指南不含成人指南、`{age:30,bmi:27}`→含成人肥胖指南、`{age:70,diseases:[骨质疏松]}`→含骨质疏松+中国居民膳食指南。
3. **场景A 计划生成(登录用户)**：`POST /rag/query {"query":"帮我规划减肥食谱"}` 带 token → intent=plan_generation，info_collector 读 DB profile 不阻断，日志见 `where={'$or':[...]}` 且 vector_results.metadata.guideline_title 均在 selected 内，返回结构化计划。
4. **场景B guest 分级追问**：仅「帮我规划减肥食谱」→硬阻断追问年龄/身高/体重；追加「28岁 175cm 80kg 想减肥」到 conversation_context → 多轮累积出完整 profile，BMI≈26.1 选成人肥胖指南，计划末尾带软提示；「我是高尿酸帮我规划」→ diseases=[高尿酸] 选高尿酸食养指南。
5. **场景C 营养查询**：「鸡蛋的热量是多少」→ intent=food_search → sql_retriever → 返回表格化营养数据。
6. **场景D 闲聊**：「你好你是谁」→ intent=unknown → 直接 generator，日志无 sql_retriever/vector_retriever，answer_source=llm_generated。
7. **场景E 指南问答**：「脑卒中患者怎么吃」→ guideline_query → 带指南来源回答。

## 风险
- 74MB 大 PDF 解析失败 → 脚本 try/except 隔离，不阻断其余入库。
- ChromaDB `$or`/`$and` 嵌套 where 若版本受限 → 退化为「先查 guideline_title∈selected，再 union expert_media」两轮客户端合并。
- guest 多轮追问信息丢失 → 依赖前端把历史塞进 `conversation_context`（前端契约需确认）。
- 糖尿病/高血压无食养 PDF → 走《中国居民膳食指南》基础 + 软提示，后续 PDF 到位后注册表加一条即可。
- intent 关键词「食谱」同时在 PLAN/GUIDELINE → 走 plan（符合产品目标，可接受）。
