# AI 大模型应用开发 / RAG / Agent 岗位面试题库（标准答案版）

> 📅 整理日期：2026-08-07
> 🎯 适用岗位：AI 大模型应用开发 / RAG 开发 / Agent 开发 / LLM 工程师
> 📖 使用方法：每题先盖住答案自己默答，再对照标准答案查漏补缺；每题末尾的"💡 口诀"用于快速记忆
> 📂 代码引用：所有 `file:///` 链接可在 IDE 中点击直接跳转

---

## 📑 目录

- [第一部分：项目核心 35 题（基于 HealthAI 项目）](#第一部分项目核心-35-题基于-healthai-项目)
  - [1.1 RAG 架构与实现（10题）](#11-rag-架构与实现10题)
  - [1.2 Agent 与 LangGraph（8题）](#12-agent-与-langgraph8题)
  - [1.3 工程与基础设施（7题）](#13-工程与基础设施7题)
  - [1.4 性能优化（5题）](#14-性能优化5题)
  - [1.5 安全与合规（3题）](#15-安全与合规3题)
  - [1.6 综合与场景题（2题）](#16-综合与场景题2题)
- [第二部分：项目深挖 20 题（高频追问点）](#第二部分项目深挖-20-题高频追问点)
- [第三部分：岗位通用面试题](#第三部分岗位通用面试题)
  - [3.1 基础篇（Python / 数据结构 / 网络 / 数据库）](#31-基础篇python--数据结构--网络--数据库
  - [3.2 进阶篇（系统设计 / 并发 / 设计模式）](#32-进阶篇系统设计--并发--设计模式
  - [3.3 深度篇（LLM 原理 / Transformer / 训练技巧）](#33-深度篇llm-原理--transformer--训练技巧
- [附录 A：背诵心法](#附录-a背诵心法)
- [附录 B：高频关键词速记表](#附录-b高频关键词速记表)

---

# 第一部分：项目核心 35 题（基于 HealthAI 项目）

## 1.1 RAG 架构与实现（10题）

### Q1：你项目里 RAG 的完整流程是怎样的？

**标准答案：**

按时间线分四个阶段：

1. **建库阶段**：PDF/Word/音视频 → `audio_loader.py` / `pdf_loader.py` 解析 → `semantic_chunker.py` 语义分块（500字，重叠80）→ Embedding API 向量化 → 双写存储：向量入 ChromaDB `guideline_chunks` 集合，摘要入 PostgreSQL `document_chunks` 表（通过 `chunk_id` 关联）

2. **检索阶段**：用户 query → `intent_recognizer_node` 意图识别 → 两阶段检索：
   - 阶段1：`guideline_registry.select_guidelines_by_profile(profile)` 根据用户画像（年龄/疾病/BMI）选出 guideline_title 集合
   - 阶段2：`vector_retriever_node` 用 `selected_guidelines` 构造 where 过滤 → TopK=10 召回

3. **融合阶段**：hybrid 意图下 SQL 路（食物检索）+ 向量路并行 → `rrf_fusion.reciprocal_rank_fusion(k=60)` 融合异构分数

4. **生成阶段**：Top5 chunks 注入 Context Prompt → LLM 生成 → 附来源引用

**关键指标**：Recall@10 = 0.82，Faithfulness = 0.91，P95 延迟 1.8s

**代码参考**：[graph_builder.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/graph_builder.py)、[nodes/__init__.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/nodes/__init__.py)

> 💡 **口诀**：建库（解析→分块→向量化→双写）→ 检索（意图→两阶段→TopK）→ 融合（RRF）→ 生成（Top5注入+溯源）

---

### Q2：两阶段检索相比纯向量检索有什么优势？

**标准答案：**

**核心优势：先过滤再检索，避免向量相似度被无关文档干扰。**

- **问题场景**：用户是"糖尿病+肥胖"，纯向量检索可能召回"骨质疏松指南"的 chunk（因为"饮食"这个词语义相近，向量相似度高）
- **两阶段解决**：
  - 阶段1 用注册表硬规则（`disease_tags=糖尿病/肥胖`）锁定 3-5 本相关指南
  - 阶段2 在限定范围内做向量相似度，召回准确率大幅提升
- **实测效果**：Recall@10 从 0.58 → 0.82（+41%），Precision@5 从 0.51 → 0.78（+53%）

**架构实现**：
- `info_collector_node` 调用 `select_guidelines_by_profile` 填充 `state.selected_guidelines`
- `vector_retriever_node` 使用 `selected_guidelines` 构造 where 过滤：`(source_type=guideline AND guideline_title ∈ selected) OR (source_type=expert_media)`

**代码参考**：[guideline_registry.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/guideline_registry.py)、[nodes/__init__.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/nodes/__init__.py)

> 💡 **口诀**：硬规则先过滤 → 软相似度后检索 → 召回准+精度高

---

### Q3：RRF 融合算法是怎么工作的？为什么不用加权平均？

**标准答案：**

**RRF 公式**：`score(d) = Σ 1/(k + rank_i(d))`，其中 k 默认 60，`rank_i(d)` 是文档 d 在第 i 个检索器结果中的排名（从1开始）

**示例**：
- 文档 A 在 SQL 路排名第1，在向量路排名第3
- RRF 分数 = 1/(60+1) + 1/(60+3) = 0.0164 + 0.0159 = 0.0323

**为什么不用加权平均？**
1. **量纲不同**：SQL 路分数（BM25 0-100）和向量路分数（余弦相似度 0-1）不可比
2. **分布不同**：BM25 长尾分布，余弦相似度正态分布，加权平均需要手动调权重
3. **RRF 优势**：只看排名不看绝对分数，天然解决异构检索器分数不可比；排名越靠前贡献越大，k=60 是业界经验值（平衡 Top1 和 TopN 的贡献差异）

**代码参考**：[rrf_fusion.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/rrf_fusion.py)

> 💡 **口诀**：只看排名不看分 → 异构分数可比 → k=60 是经验值

---

### Q4：你的分块策略是什么？为什么不直接按固定字符切？

**标准答案：**

**语义分块四步法**：
1. **识别章节标题**：正则 `^(第[一二三四五六七八九十百]+[章节部分篇]|[0-9]+[\.、)])\s` 匹配
2. **按段落切分**：空行 / `\n\n` 作为段落边界
3. **合并成目标块**：段落累加至 500字±200（最小300/最大700）
4. **应用重叠窗口**：相邻块重叠 80字，防止段间语义丢失

**为什么不固定字符切？**
- **固定字符切断语义完整性**：例如"成人肥胖患者每日蛋白质摄入量应达到1.2-"被切成两半，下一块开头是"1.6g/kg体重"，两块单独检索都不完整
- **语义分块保留上下文**：以段落/句子为最小单位，保证每个 chunk 是完整语义单元

**参数选择依据**：
- target_size=500：经验值，太小信息不足，太大检索精度下降
- overlap=80：约1-2句话，防止跨块语义丢失
- max_size=700：避免单块过大挤占 Context

**代码参考**：[semantic_chunker.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/utils/semantic_chunker.py)

> 💡 **口诀**：标题分段→段落累加→重叠80→语义完整

---

### Q5：ChromaDB 1.x 遇到过什么坑？怎么解决的？

**标准答案：**

**两个核心坑**：

**坑1：auth 兼容坑**
- 现象：ChromaDB 1.5.x 客户端连接未启用认证的服务端时，`get_user_identity` 抛 `ValueError`
- 原因：1.5.x 内部强制调用 `get_user_identity` 做权限校验，但未启用认证的服务端不支持
- 解决：monkey-patch `_Client.get_user_identity` 方法，异常时返回默认的 `_UserIdentity`
- 长期方案：升级到 1.6.0+ 或服务端开启 Token 认证

**坑2：where_filter 解析坑**
- 现象：`{"$and": [c1, c2]}` 无法正确转 SQL WHERE 子句
- 原因：`$and` 条件被当作 dict 而非 list 处理
- 解决：重写 `_parse_where_filter()` 递归解析 `$and/$or/$in/$eq` 操作符

**代码参考**：[vector_store.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/repositories/vector_store.py)

> 💡 **口诀**：1.5 认证坑 → patch 返回默认身份；过滤解析坑 → 递归处理 $and/$or

---

### Q6：怎么评估 RAG 质量？

**标准答案：**

**三维评估体系**：

1. **离线 RAGAS 指标**（自动跑）
   - **Context Precision**：检索到的 chunk 排序是否合理（越相关越靠前）
   - **Context Recall**：标注的关键事实是否都被召回
   - **Faithfulness**：生成回答是否忠于 Context（无幻觉）
   - **Answer Relevance**：回答是否切题
   - 本项目实测：Context Precision 0.84，Faithfulness 0.91

2. **在线指标**（Prometheus 采集）
   - 缓存命中率（目标 >40%）
   - P95 延迟（目标 <2s）
   - 错误率（目标 <1%）
   - LLM 调用失败率

3. **用户反馈**（Feedback 表）
   - 点赞率 / 踩率 / 计划使用率
   - 高踩率触发 Prompt 调整

**评估流程**：标注 100 条测试集 → 跑全流程 → 算 RAGAS 指标 → 低于阈值的迭代优化 → 上线后看用户反馈 → BadCase 回流

> 💡 **口诀**：离线 RAGAS（4指标）+ 在线 Prometheus（3指标）+ 用户反馈（3率）

---

### Q7：怎么处理 LLM 幻觉？

**标准答案：**

**四层防御**：

1. **工具调用防数学幻觉**
   - BMR/TDEE 不交给 LLM 算，本地纯函数 `nutrition_calc.py` 实现 Mifflin-St Jeor 公式
   - `tool_calling_node` 在进 LLM 前先调用纯函数 → 结果直接写进 State → Prompt 中明确"以下是精确计算结果"
   - 热量计算准确率 100%

2. **检索增强防知识幻觉**
   - 所有回答必须基于 RAG 召回的 chunks
   - Prompt 中明确"如果知识库中没有相关信息，请明确说明'未找到相关资料'，不要编造"

3. **知识溯源引用**
   - 生成回答时附带 chunk 的来源（指南标题+章节+页码）
   - 用户可点击来源验证

4. **Faithfulness 评估**
   - 离线跑 RAGAS，Faithfulness 低于 0.8 的 Prompt 版本打回
   - 线上抽样 5% 跑自动评估，低于阈值告警

**代码参考**：[nutrition_calc.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/utils/nutrition_calc.py)

> 💡 **口诀**：工具防数学 + 检索防知识 + 溯源可验证 + 评估兜底

---

### Q8：向量库的选择？ChromaDB vs Milvus vs Qdrant？

**标准答案：**

**阶段化选择**：

| 阶段 | 数据量 | 推荐方案 | 理由 |
|------|--------|---------|------|
| 原型期 | <10万条 | ChromaDB | Python 原生嵌入，零运维，部署最简单 |
| 成长期 | 10万-1亿条 | Qdrant | Rust 写的性能好，支持过滤+量化+分片，单机够 |
| 生产级 | >1亿条 | Milvus | 分布式架构，多副本/水平扩展，适合大公司 |

**本项目选择**：V2.0 用 ChromaDB（986条，杀鸡不用牛刀），V3.0 规划升级 Milvus 支持分布式

**选择维度**：
1. 数据规模（决定单机/分布式）
2. 查询 QPS（决定是否需要量化/分片）
3. 元数据过滤复杂度（ChromaDB 支持基础，Milvus 支持复杂过滤）
4. 运维成本（ChromaDB 零运维，Milvus 需要专门团队）

> 💡 **口诀**：小用 Chroma / 中用 Qdrant / 大用 Milvus

---

### Q9：如果知识库文档很大，比如1万页PDF，怎么处理？

**标准答案：**

**增量索引 + 分层策略**：

1. **文档去重**：先算 PDF 文件 hash（MD5），入库前查重，已存在则跳过
2. **轻量增量索引**：新文档只处理新增部分（记录 `last_modified`，只处理修改过的文件）
3. **定期全量重建**：每周低峰期（凌晨3点）跑 `unified_reindex.py` 重建脚本，删除旧集合→新建→切换别名（零停机）
4. **质量校验**：重建后用测试集跑 Recall@K，低于阈值自动回滚到上一版本

**扫描版 PDF 特殊处理**：
- 检测是否有文本层（fitz 提取字符数 < 100/页判定为扫描版）
- 触发 OCR 流程（PaddleOCR / Tesseract）
- OCR 结果再走分块→向量化流程

**本项目实际案例**：《中国居民膳食指南（2022）》74MB 扫描版，378 页仅 377 字符，需 OCR 才能入库（已知限制）

**代码参考**：[unified_reindex.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/scripts/ingest/unified_reindex.py)

> 💡 **口诀**：去重→增量→全量重建→质量校验→自动回滚

---

### Q10：Embedding 模型怎么选？

**标准答案：**

**三维选择标准**：

1. **维度与精度**：1024 维平衡精度和存储；bge-large-zh-v1.5 在中文任务上表现好；768 维省存储但精度略降
2. **向量库兼容**：ChromaDB 支持 l2 / cosine / ip 距离函数，cosine 最通用（不取向量长度）
3. **成本与延迟**：
   - 本地部署（sentence-transformers + bge-large-zh）：省 API 钱，但需 GPU
   - API 调用（ZhipuAI / OpenAI）：省 GPU 成本，QPS 够，按调用计费

**关键原则：建库和查询必须用同一模型**，否则向量空间不一致，相似度计算失效。

**本项目选择**：ZhipuAI Embedding API（1024 维），原因：
- 不需要 GPU
- 中文任务表现好
- API 调用 QPS 满足当前需求（<100 QPS）
- 已缓存 Embedding 结果（Redis 7天TTL），重复 query 不重复调用

**代码参考**：[embedding.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/embedding.py)、[settings.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/config/settings.py)

> 💡 **口诀**：建库查询同模型 → 1024维平衡 → API省GPU → 缓存省钱

---

## 1.2 Agent 与 LangGraph（8题）

### Q11：为什么选 LangGraph 而不是手写状态机？

**标准答案：**

**手写的三个痛点 → LangGraph 的对应优势**：

| 手写痛点 | LangGraph 优势 |
|---------|---------------|
| if-else 嵌套太深（3层以上不可维护） | Graph 声明式定义节点和边，可视化清晰 |
| 并行检索 asyncio.gather + 状态合并易错 | 原生 `add_node` 并行分支，自动等待+合并 |
| 中断恢复需手写持久化+恢复逻辑 | `checkpointer` 自动持久化，`interrupt` 恢复 |

**额外好处**：
- 与 LangChain 生态无缝集成（Tool/LLM/Memory）
- 内置 Reviver 机制支持状态序列化
- 支持 streaming（流式输出 token）
- 支持 human-in-the-loop（中断等用户输入）

**代码参考**：[graph_builder.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/graph_builder.py)

> 💡 **口诀**：声明式 > 命令式 → 并行自动 → 中断可恢复 → 生态丰富

---

### Q12：LangGraph 的 5 种控制流模式，你项目里怎么用的？

**标准答案：**

| 模式 | 项目应用 | 代码位置 |
|------|---------|---------|
| **条件分流** | `_route_after_intent` 按意图分 plan_generation/food_search/guideline_query/unknown/hybrid | [graph_builder.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/graph_builder.py) |
| **循环改写** | `_route_after_evaluator` 判断检索质量，不够则 rewrite query，最多 RAG_MAX_REWRITE=2 次 | [graph_builder.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/graph_builder.py) |
| **并行检索** | hybrid 意图下 sql_retriever_node + vector_retriever_node 并行执行 | [graph_builder.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/graph_builder.py) |
| **中断恢复** | info_collector_node 判断信息缺失 → needs_clarification=True → END → 用户补充后重新走 graph | [nodes/__init__.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/nodes/__init__.py) |
| **评估反馈** | evaluator_node 评估召回质量 → 决定继续 / 改写 / 追问 | [nodes/__init__.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/nodes/__init__.py) |

**实现细节**：
- 条件分流：`graph.add_conditional_edges("intent_node", _route_after_intent, {...})`
- 循环改写：`state.rewrite_count` 计数，达到 RAG_MAX_REWRITE 强制 END
- 并行检索：把两个节点都加为下游节点，LangGraph 自动并发执行并等待全部完成
- 中断恢复：用 `MemorySaver` 持久化 state，用户补充信息后从断点继续

> 💡 **口诀**：分流/循环/并行/中断/评估 → 五大模式覆盖所有 Agent 场景

---

### Q13：动态调整引擎的算法是什么？

**标准答案：**

**纯函数 `compute_adjustment`，三步走**：

1. **算差值**：`剩余热量 = 计划目标 - 今日已摄入`（按宏量蛋白/脂肪/碳水分别算）

2. **算权重**：`_get_remaining_meal_weights()` 找出从当前餐次开始未吃的餐次，按默认权重合计：
   - 早餐 25% / 午餐 35% / 晚餐 30% / 加餐 10%

3. **算调整比例**：
   ```
   adjustment_ratio = 剩余热量 / (目标热量 × 剩余餐次权重总和)
   下一餐目标 = 原计划该餐目标 × adjustment_ratio
   ```
   夹到 [MIN_MEAL_CALORIES=200, MAX_MEAL_CALORIES=1200] 安全区间

**状态判断**：
- `remaining_cal < -100` → over_budget（超标，削减）
- `remaining_cal > target × 0.5` → under_budget（不足，增加）
- 其他 → on_track（按计划）

**安全底线**：单餐不得低于 200 kcal（防止极低热量），不得高于 1200 kcal（防止暴食）

**代码参考**：[adjustment_service.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/services/adjustment_service.py)

> 💡 **口诀**：剩余=目标-已摄入 → 按餐次权重分摊 → 夹在[200,1200]安全区间

---

### Q14：工具调用节点怎么保证 LLM 不瞎编计算结果？

**标准答案：**

**核心思路：不让 LLM 算，只让 LLM 用结果。**

**架构**：
1. `nutrition_calc.py` 纯函数实现 Mifflin-St Jeor 公式（无副作用，输入决定输出）
2. `tool_calling_node` 在进 LLM 前先调用纯函数算 BMR/TDEE/宏量分配
3. 结果直接写进 `state.tool_results`
4. Prompt 中明确："以下是精确计算结果（BMR=1722 kcal, TDEE=2670 kcal），请基于它生成回答，不要重新计算"

**好处**：
- LLM 只做"用结果组织语言"，不做"推理计算"，彻底避免数学幻觉
- 热量计算准确率 100%（纯函数）
- 测试简单：28 个单元测试覆盖边界（0值/极端BMI/中文性别输入）

**Mifflin-St Jeor 公式**：
- 男性：BMR = 10×体重(kg) + 6.25×身高(cm) - 5×年龄 + 5
- 女性：BMR = 10×体重(kg) + 6.25×身高(cm) - 5×年龄 - 161

**代码参考**：[nutrition_calc.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/utils/nutrition_calc.py)

> 💡 **口诀**：本地纯函数算 → 结果注入 Prompt → LLM 只组织语言不计算

---

### Q15：怎么处理用户补充信息后的对话上下文？

**标准答案：**

**三要素**：

1. **PostgreSQL 持久化**：`ChatHistory` 表存每轮 user_id / role / content / session_id / created_at，支持多 session 隔离

2. **多轮累积注入**：
   - 每次进 graph 前拉最近 N=20 轮对话
   - 拼成 `conversation_context` 字段注入 state
   - LLM Prompt 中包含历史对话，理解上下文

3. **信息抽取避免重复追问**：
   - `intent_recognizer_node` 检测 AI 追问的上下文（含"请补充"/"您的年龄"/"必要信息"等关键词）
   - 若是 → 强制意图设为 `plan_generation`
   - `_extract_age_from_query` 支持纯数字输入（`r'^(\d{1,2})$'`）
   - 把用户新输入的信息提取到 profile 中，**不再重复追问已提供字段**

**分级追问策略**：
- 硬性阻断（缺这些字段 END 返回追问话术）：年龄、身高、体重、目标
- 软提示可跳过：地区
- 默认值：疾病默认"无"并提示用户

> 💡 **口诀**：DB持久化 → 历史注入 → 抽取新信息 → 不重复追问

---

### Q16：反馈机制怎么驱动 LLM 输出调整？

**标准答案：**

**闭环链路**：

1. **用户动作**：点赞 / 踩 / 使用计划 → 写入 `UserFeedback` 表
   - UPSERT 逻辑（同一用户对同一目标的反馈是更新而非新增）
   - 唯一约束 `(user_id, target_type, target_id)` 防刷分

2. **偏好聚合**：`_update_user_preferences()` 滑窗取最近 30 天反馈
   - 算点赞率 / 踩率 / 使用率
   - 写入 `UserProfile.feedback_preferences` JSON 字段

3. **阈值判断 + 风格指令生成**：
   - 高点赞率（>70%）→ 指令"保持当前风格"
   - 高踩率（>30%）→ 指令"请更详细/更简洁/更专业"（根据踩的具体内容）
   - 反馈不足（<5条）→ 不调整

4. **指令注入**：
   - `generator_node` 和 `plan_generator_node` 把风格指令拼到 System Prompt 后面
   - 流式模式 `_stream_system_prompt` 也包含反馈指令

**实测效果**：上线反馈机制后，用户点赞率提升 23%

**代码参考**：[feedback_service.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/services/feedback_service.py)

> 💡 **口诀**：用户动作 → 偏好聚合 → 阈值判断 → 指令注入 Prompt

---

### Q17：LangGraph 的 checkpointer 用过吗？

**标准答案：**

**用过，分两个层次**：

**当前用 `MemorySaver`（内存版）**：
- 用途1：中断恢复（info_collector 判断信息缺失后 END，用户补充信息后从断点继续）
- 用途2：回溯调试（graph 每步 state 快照，线上出问题回放每一步输入输出）

**V3.0 规划接 `PostgresSaver` / `SqliteSaver` 做持久化**：
- 多实例共享 state（K8s 多副本场景）
- 长时间任务（如复杂计划生成跨小时）持久化不丢

**踩坑经验**：
- LangGraph 1.x 调用 `Reviver(allowed_objects="core")` 序列化 state
- 需要 `langchain-core>=1.2.5` 才支持该参数
- 之前误降级到 0.3.x（langchain-core 0.3.6）报 `TypeError: Reviver.__init__() got an unexpected keyword argument 'allowed_objects'`
- 解决：requirements.txt 钉死 `langchain==1.3.11 / langchain-core==1.4.8 / langgraph==1.2.7`

> 💡 **口诀**：MemorySaver 内存版 → 持久化用 PostgresSaver → 钉死版本防 0.3.x 坑

---

### Q18：怎么设计 Agent 的状态（State）？

**标准答案：**

**设计原则：最小必要 + 字段分类 + 单向流动**

**字段分类**：
- **输入层**（只增不改）：user_query / user_profile / conversation_context
- **中间层**：intent / selected_guidelines / sql_results / vector_results / fused_results / rewrite_count / tool_results
- **输出层**：generated_answer / plan_config / needs_clarification / style_directive / safety_result

**关键原则**：
1. 每个节点只写自己负责的字段（如 `vector_retriever_node` 只写 `vector_results`）
2. 避免多节点写同一字段造成 race condition（特别是并行节点）
3. 并行节点写不同字段，由下游节点合并（如 fusion_node 合并 sql_results + vector_results）
4. 中间字段加默认值（如 rewrite_count=0），避免 KeyError

**State 定义**：用 TypedDict 或 Pydantic BaseModel（推荐 Pydantic，类型校验+自动序列化）

**代码参考**：[state.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/state.py)

> 💡 **口诀**：输入/中间/输出三层 → 单向流动 → 单节点写字段 → 并行写不同字段

---

## 1.3 工程与基础设施（7题）

### Q19：Redis 的 6 类缓存 Key 设计？

**标准答案：**

| 类别 | Key 模式 | TTL | 缓存内容 | 设计理由 |
|------|---------|-----|---------|---------|
| 查询缓存 | `rag:query:{hash}:{user_id}` | 24h | 完整 RAG 回答+来源 | 相同查询占比 30%+ |
| Embedding 缓存 | `rag:embed:{hash}` | 7d | 文本→向量 | Embedding 不变，省 API 钱 |
| 食物检索 | `rag:food:{hash}` | 1h | SQL 食物检索结果 | 食物库更新频率低 |
| TDEE 计算 | `rag:tdee:{user_id}:{hash}` | 7d | BMR/TDEE 结果 | 用户档案不变时结果恒定 |
| 饮食计划 | `rag:plan:{user_id}:{goal}:{cal}` | 6h | 生成的 Plan | 目标+档案不变时可复用 |
| 安全护栏 | `rag:safety:{hash}` | 24h | LLM 安全判断结果 | 同 query 可复用判断 |

**TTL 设计原则**：
- 越稳定的数据 TTL 越长（Embedding 7d > TDEE 7d > Plan 6h > Safety 24h > Food 1h）
- 用户档案更新时主动清缓存（`clear_user_cache(user_id)`）

**代码参考**：[redis_client.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/utils/redis_client.py)

> 💡 **口诀**：6 类 Key 差异化 TTL → 稳定长 TTL → 易变短 TTL → 档案变更主动清

---

### Q20：Redis 挂了系统还能跑吗？怎么实现降级？

**标准答案：**

**能跑，优雅降级三要素**：

1. **懒加载 + 健康检查**：
   - 首次使用才连接（避免无 Redis 时启动失败）
   - ping 失败标记 `_available=False`，60 秒内不重试
   - 60 秒后重新 ping，恢复则重新标记可用

2. **所有操作 try-except 返回 None/False**：
   - `get_json` 失败返回 None
   - `set_json` 失败返回 False
   - 调用方感知不到崩溃

3. **调用方兼容返回值**：
   - 所有 `cache.get()` 调用后判断 `if None 就走原逻辑`
   - 不依赖缓存命中才能工作
   - 缓存未命中时回源查 DB / 调 LLM

**测试覆盖**：单元测试 patch `is_available` 返回 False，断言所有操作返回 None/False，主流程 0 崩溃

**代码参考**：[redis_client.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/utils/redis_client.py)

> 💡 **口诀**：懒加载+健康检查 → try-except 返回降级值 → 调用方兼容 None

---

### Q21：Docker Compose 里的健康检查怎么写的？

**标准答案：**

**8 个服务都有健康检查，例如 PostgreSQL**：

```yaml
postgres:
  image: postgres:15
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U healthai -d healthai_db"]
    interval: 10s       # 每10秒检查一次
    timeout: 5s         # 超时5秒判定失败
    retries: 5          # 连续5次失败标记不健康
    start_period: 30s   # 启动后30秒开始检查（给初始化时间）

chromadb:
  depends_on:
    postgres:
      condition: service_healthy   # 关键：依赖健康才启动
```

**关键设计**：
- `depends_on.condition: service_healthy` 确保依赖真的 ready 再启
- 而不是 `depends_on: postgres`（这只等容器启动，不等服务 ready，很多人踩这个坑）

**8 个服务的健康检查端点**：
- PostgreSQL: `pg_isready`
- Redis: `redis-cli ping`
- ChromaDB: `curl http://localhost:8001/api/v1/heartbeat`
- FastAPI: `curl http://localhost:8000/health`

> 💡 **口诀**：healthcheck 四参数 → depends_on service_healthy → 不踩"容器启动≠服务就绪"坑

---

### Q22：安全护栏的双重机制？

**标准答案：**

**流程**：

```
用户 Query
   │
   ▼
全局开关检查（global_enabled=false 直接放行）
   │
   ▼
正则快筛（12类规则，<1ms）
   ├─ 命中 → 直接拦截，返回对应类别回复
   └─ 未命中 → 继续
           │
           ▼
    LLM 慢审（temperature=0，结构化JSON输出）
        ├─ 命中危险 → 拦截，附上 LLM 给出的理由
        ├─ 异常/超时 → 降级放行（但记录日志告警）
        └─ 通过 → 进入后续 RAG 流程
```

**双重机制设计理由**：
- 正则快筛：确定性高、<1ms、零成本，但只能匹配字面规则
- LLM 慢审：处理隐晦表达（如"二钾双胍"故意变体、"只喝水7天"语义级判断），但慢（500ms）+贵（API调用）
- 组合：90% 查询被正则放行，只有 10% 走 LLM 慢审，平衡性能与精度

**规则外置 YAML 热更新**：
- 所有正则规则、LLM 提示词、分类回复从 `config/safety_rules.yaml` 加载
- `SafetyRuleLoader` 内部 mtime 检测，文件修改后下次调用自动生效
- 无需重启服务

**代码参考**：[safety_checker.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/safety_checker.py)、[safety_rule_loader.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/config/safety_rule_loader.py)、[safety_rules.yaml](file:///G:/PyCharmMiscProject/HealthAI-Platform/config/safety_rules.yaml)

> 💡 **口诀**：正则快筛 90% → LLM 慢审 10% → 规则外置 YAML 热更新

---

### Q23：三角色权限怎么实现的？

**标准答案：**

**两层拦截**：

1. **认证层（FastAPI Depends）**：
   - `get_current_user_id` 解析 JWT Bearer Token
   - 无 token / token 过期 → 401 Unauthorized
   - 游客访问受保护端点直接 401

2. **授权层（装饰器）**：
   - `@require_role("admin")` 检查 `user.role` 字段
   - 非管理员 → 403 Forbidden

**RBAC 权限矩阵**：

| 功能 | 游客 | 普通用户 | 管理员 |
|------|------|---------|--------|
| 搜索食物 | ✅ | ✅ | ✅ |
| 生成计划 | ❌ 401 | ✅ | ✅ |
| 提交反馈 | ❌ 401 | ✅ | ✅ |
| 查看历史 | ❌ 401 | ✅ 仅自己 | ✅ 全部 |
| 管理用户 | ❌ 403 | ❌ 403 | ✅ |
| 入库脚本 | ❌ 403 | ❌ 403 | ✅ |

**JWT 双 Token 机制**：
- Access Token：30min（短时效，放 Authorization Header）
- Refresh Token：7天（长时效，HTTPOnly Cookie 防 XSS）
- Access 过期后用 Refresh 换新 Access

**登录限流**（防爆破）：
- 账号：5 次失败锁定 15 分钟
- IP：20 次失败锁定 15 分钟
- Redis 计数器实现

**代码参考**：[auth.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/api/routes/auth.py)、[auth_service.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/services/auth_service.py)、[login_limiter.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/services/login_limiter.py)

> 💡 **口诀**：认证 JWT → 授权 RBAC → 双 Token → 限流防爆破

---

### Q24：CI 流水线四阶段？

**标准答案：**

**四阶段流水线**：

```
git push ──→ GitHub Actions 触发
                  │
                  ▼
         ┌─────────────────┐
         │  阶段1: Lint     │  ruff check + black --check
         │  代码检查         │  失败 → 阻断
         └────────┬────────┘
                  │ 通过
                  ▼
         ┌─────────────────┐
         │  阶段2: Unit     │  pytest tests/unit/ --cov
         │  单元测试         │  覆盖率 < 80% → 阻断
         │  180用例 <30s    │
         └────────┬────────┘
                  │ 通过
                  ▼
         ┌─────────────────┐
         │  阶段3: Integration│  pytest tests/integration/
         │  集成测试           │  起 testcontainers (PG/Redis)
         │  120用例 <2min    │
         └────────┬────────┘
                  │ 通过
                  ▼
         ┌─────────────────┐
         │  阶段4: E2E       │  pytest tests/e2e/
         │  端到端测试        │  LLM 用 Mock 录制回放
         │  60用例 <5min    │
         └────────┬────────┘
                  │ 通过
                  ▼
              ✅ 允许合并
```

**质量门禁**：
- 任意阶段失败都阻断合并
- 覆盖率 < 80% 阻断
- E2E `--maxfail=1` 第一个失败就停止（避免浪费 CI 时间）

**LLM Mock 策略**：E2E 阶段不调真实 LLM API（贵且慢），用 VCR.py 录制响应回放

> 💡 **口诀**：lint → unit（覆盖率）→ integration（Docker）→ e2e（Mock），层层阻断

---

### Q25：日志与可观测性？

**标准答案：**

**三支柱**：

1. **结构化日志**：
   - JSON 格式（机器可解析）
   - 分级：DEBUG/INFO/WARNING/ERROR
   - 字段：timestamp / level / module / message / trace_id / user_id
   - TraceID 透传（一次请求所有日志关联）
   - 每个节点输入输出都打 INFO 日志
   - 敏感字段脱敏（手机号 `138****1234`）

2. **分布式追踪**：
   - OpenTelemetry + Jaeger
   - 每个 LangGraph 节点包一层 span
   - 火焰图看哪个节点慢
   - TraceID 与日志关联，从 trace 反查日志

3. **指标采集**：
   - Prometheus 暴露 `/metrics` 端点
   - 指标：延迟分位数（P50/P95/P99）、缓存命中率、LLM 调用失败率、QPS
   - Grafana 仪表盘可视化
   - 阈值告警（P95 > 3s 告警）

**代码参考**：[logger.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/utils/logger.py)、[tracing_helper.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/tracing_helper.py)、[metrics.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/utils/metrics.py)

> 💡 **口诀**：日志（结构化+TraceID）+ 追踪（OpenTelemetry）+ 指标（Prometheus）

---

## 1.4 性能优化（5题）

### Q26：性能优化做了哪些？延迟从多少降到多少？

**标准答案：**

**四轮优化，平均延迟 2.8s → 1.1s（↓61%），P95 3.5s → 1.8s**：

1. **Redis 分层缓存**：
   - 命中率 46%
   - 相同查询从 2.5s → 30ms（缓存命中）

2. **并行检索**：
   - hybrid 意图下 SQL 路和向量路并行
   - 从串行 800ms → 并行 480ms（节省 320ms）

3. **异步流式输出（SSE）**：
   - 首 Token 从 2.8s → 380ms
   - 用户体感快很多（边生成边显示）

4. **宽召回窄注入**：
   - TopK=20 召回 → 重排序取 Top5 注入
   - Context 从 5000字 → 2000字
   - LLM 推理时间减 40%（输入短了）

**关键指标对比**：

| 指标 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| 平均延迟 | 2.8s | 1.1s | 61% ↓ |
| P95 延迟 | 3.5s | 1.8s | 49% ↓ |
| 首 Token 延迟 | 2.8s | 380ms | 86% ↓ |
| 缓存命中率 | 0% | 46% | - |
| 并发无死锁 | - | 20 并发 | - |

> 💡 **口诀**：缓存→并行→流式→宽召回窄注入，四轮降 61%

---

### Q27：并发测试怎么做的？20并发怎么保证无死锁？

**标准答案：**

**三层保障**：

1. **DB 连接池**：
   - SQLAlchemy `pool_size=10, max_overflow=20`
   - 总共可支持 30 个并发连接
   - 避免连接耗尽

2. **Redis 连接池**：
   - `max_connections=10`，单例复用
   - 连接借出归还复用，不频繁创建

3. **无共享可变状态**：
   - 每个请求独立 Session（`SessionLocal()` 每次新建）
   - LangGraph State 每次请求新建一份，不跨请求共享
   - 避免多线程写同一变量

**测试方法**：
```python
from concurrent.futures import ThreadPoolExecutor

def test_concurrent_20_no_deadlock(user_client):
    def make_request():
        return user_client.post("/chat", json={"message": "今天吃什么"})
    
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: make_request(), range(20)))
    
    assert all(r.status_code == 200 for r in results)
```

**压力测试（locust）**：模拟 100 并发用户，RPS 50，持续 5 分钟，监控 P95 延迟和错误率

> 💡 **口诀**：连接池兜底 → 单例复用 → State 不共享 → 20并发无死锁

---

### Q28：Whisper 视频转写遇到过什么坑？

**标准答案：**

**核心坑：ffmpeg 兼容性问题**

**现象**：
- imageio-ffmpeg 精简版不支持 wav 编码
- Whisper 内部调 ffmpeg 转 wav 时失败
- 错误：`ffmpeg error: Unknown encoder 'pcm_s16le'`

**排查过程**：
1. 查 Whisper 源码，发现它调 `subprocess.run(["ffmpeg", ...])` 用 PATH 中的 ffmpeg
2. 查 imageio-ffmpeg，发现它带的是精简版（移除了部分编码器）
3. 用 `ffmpeg -formats` 检测，确认精简版没有 wav 编码器

**解决方案**：
1. `_get_ffmpeg_path()` 优先用 imageio-ffmpeg（自带，免安装）
2. 调用前先 `_ffmpeg_supports_wav(ffmpeg_bin)` 检测（看 `ffmpeg -formats` 输出是否含 `pcm_s16le`）
3. 不支持就回退到系统 PATH 的完整版 ffmpeg
4. 或者把完整版 imageio-ffmpeg 副本覆盖到 PATH 最前面

**实测**：67 个陈伟专家视频全部成功转写，平均每个视频 15 秒（base 模型 + CPU）

**代码参考**：[audio_loader.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/loaders/audio_loader.py)

> 💡 **口诀**：imageio-ffmpeg �简简版缺 wav → 检测能力 → 回退系统 PATH → 覆盖 PATH 最前

---

### Q29：向量检索慢怎么优化？

**标准答案：**

**四个方向**：

1. **先过滤后检索（两阶段）**：
   - 把候选从 986 条缩到 100-300 条
   - 距离计算量↓70%
   - 本项目核心优化

2. **量化（Quantization）**：
   - IVF_PQ 量化（Milvus/Qdrant 支持）
   - 压缩 4-8 倍（1024维 → 128维量化码本）
   - 召回损失 1-3%（可接受）
   - ChromaDB 暂不支持，V3.0 升级 Milvus 后启用

3. **索引预热**：
   - 服务启动时跑一次 dummy 查询
   - 触发 ChromaDB 索引加载到内存
   - 避免首次查询慢（冷启动）

4. **结果缓存**：
   - 高频查询走 Redis（Top20% 的 Query 占 80% 流量）
   - 缓存 key 用 query 的 hash，TTL 24h
   - 不用查向量库

**HNSW 参数调优**（如果用 Milvus）：
- `M=16`（图连接度，越大越准但越慢）
- `ef_construction=200`（建索引时搜索宽度）
- `ef=64`（查询时搜索宽度，越大越准越慢）

> 💡 **口诀**：先过滤→量化→预热→缓存，四招降向量检索延迟

---

### Q30：Prompt 太长有什么优化方法？

**标准答案：**

**宽召回窄注入 + 动态 Context 窗口**：

1. **TopK=20 召回 → 重排序取 Top5 注入**：
   - 用 RRF 融合 + 交叉编码器 reranker（bge-reranker-base）
   - Context 长度砍 75%
   - 召回质量基本不掉

2. **摘要压缩**：
   - chunks 先过一个轻量 LLM 做 TL;DR 摘要（500字→100字）
   - 再拼进 Context
   - 适合 chunk 较多的场景

3. **滑动窗口**：
   - 如果总长超 LLM 上下文限制
   - 只取与 query 相似度最高的窗口段
   - 滑动步长 200 字

4. **结构化 Context**：
   - 用 XML/Prompt 标记块边界：`<doc id="1">...</doc>`
   - LLM 解析更准（知道哪段是哪个文档）
   - 引用溯源更清晰

**实测**：Context 从 5000字 → 2000字，LLM 推理时间从 1.2s → 0.7s（↓42%），Faithfulness 基本持平（0.91→0.90）

> 💡 **口诀**：宽召回窄注入 → 摘要压缩 → 滑动窗口 → 结构化标记

---

## 1.5 安全与合规（3题）

### Q31：医疗红线有哪些？怎么保证不越线？

**标准答案：**

**三类红线**：

1. **处方药/药物推荐**：
   - 二甲双胍、胰岛素、减肥药、抗生素等
   - 正则关键词拦截 + LLM 判断意图

2. **极端饮食方式**：
   - 催吐、断食≥3天、极低热量<800kcal
   - 正则 + LLM 慢审

3. **疾病诊断/治疗方案**：
   - "我是不是糖尿病""癌症怎么治"
   - 引导用户就医，不替代医生诊断

**双重护栏保证**：
- 正则快筛：12 类规则，覆盖 90% 明显违规
- LLM 慢审：处理变体表达（"二钾双胍"故意错字、"只喝水7天"语义级判断）

**E2E 验证**：30 条攻击用例（含变体表达），拦截率 100%，0 漏网

**拦截后处理**：
- 返回标准化拒绝回复（不直接说"你的问题违规"，而是引导）
- 附上分类建议（如"如需了解药物与食物相互作用，我可以为您提供饮食建议"）

**代码参考**：[safety_checker.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/safety_checker.py)、[safety_rules.yaml](file:///G:/PyCharmMiscProject/HealthAI-Platform/config/safety_rules.yaml)

> 💡 **口诀**：三类红线（药/极端/诊断）→ 双重护栏 → 30条攻击用例 100% 拦截

---

### Q32：极端饮食请求，LLM 放行正则没命中怎么办？

**标准答案：**

**这就是"LLM 慢审"的价值所在。**

**示例场景**：
- 用户问："我连续三天只喝水能瘦多少斤"
- 正则没有"只喝水"关键词，放行到 LLM

**LLM 慢审处理**：
- Prompt 明确列了 5 类极端饮食的判断标准
- temperature=0 稳定输出 JSON：
  ```json
  {
    "is_dangerous": true,
    "category": "extreme_diet",
    "reason": "连续断食属于极端饮食方式，存在健康风险"
  }
  ```
- 命中后返回标准化拒绝 + 引导建议

**LLM 异常时降级策略（权衡）**：
- 安全护栏 LLM 异常 → 降级放行（避免误伤正常用户）+ 打 WARNING 日志告警
- 高风险场景（明确含"药"字关键词但正则没命中）→ 降级拦截 + 提示"系统繁忙请稍后再试"
- 降级策略按业务风险定制，不是一刀切

**降级放行 vs 降级拦截的取舍**：
- 医疗场景误放行比误拦截危害更大
- 但全拦截影响用户体验
- 折中：默认降级放行 + 日志告警，关键场景降级拦截

**代码参考**：[safety_checker.py L130-L145](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/safety_checker.py#L130-L145)

> 💡 **口诀**：正则漏的 LLM 补 → 异常按风险降级 → 默认放行+告警 / 高风险拦截

---

### Q33：用户隐私数据怎么保护？

**标准答案：**

**四层防护**：

1. **敏感配置外置**：
   - `.env` 文件存 JWT_SECRET / DB 密码 / LLM API Key（不入库，gitignore）
   - 代码中 fail-fast：`if not SECRET_KEY: raise RuntimeError("JWT_SECRET_KEY 未设置")`
   - 加载不到配置直接起不来

2. **JWT 双 Token**：
   - Access Token：30min（短时效，放 Authorization Header）
   - Refresh Token：7天（长时效，HTTPOnly Cookie 防 XSS）
   - Access 过期后用 Refresh 换新 Access

3. **日志脱敏**：
   - 手机号正则打码：`138****1234`
   - 邮箱打码：`z***@example.com`
   - 身份证打码：`110***********1234`
   - 不打 JWT 原文，不打密码

4. **权限最小化**：
   - 普通用户只能看自己的记录/计划/反馈
   - 管理员才有权限看全表
   - SQL 查询都带 `WHERE user_id = ?` 强制隔离

**额外措施**：
- HTTPS 全站强制
- CORS 白名单
- SQL 注入防护（SQLAlchemy 参数化查询）
- XSS 防护（Pydantic 输入校验 + 前端转义）

> 💡 **口诀**：配置外置 + 双Token + 日志脱敏 + 权限最小化

---

## 1.6 综合与场景题（2题）

### Q34：现在让你做 V3.0，最优先做哪三件事？

**标准答案：**

**按业务价值排序**：

**第一优先：LLM 路由层**
- 根据任务复杂度选模型
- 简单食物查询用 `glm-4-flash`（便宜 10 倍）
- 复杂计划生成用 `glm-4`（质量高）
- 路由规则：关键词匹配 + 历史任务分类结果缓存
- 预期：成本降 60-70%，质量基本不掉

**第二优先：多模态输入（图片识别食物）**
- 用户拍一张餐盘照片
- 多模态模型（Qwen-VL / GLM-4V）识别食物种类 + 估算份量
- 反查食物库匹配营养 → 写入 DailyDietLog
- 价值：从"文字输入"到"拍照即可用"的体验升级，用户量增长关键功能

**第三优先：知识库更新流水线 + BadCase 回流**
- 增量索引：监听 raw_data 目录变化自动入库
- 每周全量重建 + 质量校验（Recall@K 阈值告警回滚）
- 用户"踩"的回答自动打标签进 BadCase 池
- 每周人工 review 后优化 Prompt/分块/检索策略
- 价值：质量持续提升飞轮，越用越准

**理由**：LLM 路由降本最多 → 多模态拉新最多 → BadCase 回流提质量最持续

> 💡 **口诀**：路由降本 → 多模态拉新 → BadCase 提质

---

### Q35：如果检索到的知识和你认为正确的医学常识冲突怎么办？

**标准答案：**

**三层解决机制**：

1. **知识库优先**：
   - Prompt 明确写："优先基于知识库中的权威指南回答，如果与常识冲突，以知识库内容为准，并说明来源"
   - 因为知识库是权威来源：《中国居民膳食指南》《成人肥胖食养指南》等卫健委发布文件
   - "我认为正确的常识"可能本身是错的（民间传言）

2. **知识溯源展示**：
   - 回答必须附 chunk 的来源（指南标题+章节+页码）
   - 医生/专业用户可以手动验证权威性
   - 用户看到"依据《成人肥胖食养指南》第3章"比"依据AI的判断"信任度高

3. **定期知识质检**：
   - 从用户"踩"的反馈 + 专家评审中提取冲突案例
   - 更新知识库（如果知识库真错了）→ 拉取最新版指南
   - 修正 Prompt（如果 Prompt 理解错了）→ 优化提示词
   - 这是 V3.0 BadCase 回流机制要解决的问题

**冲突处理流程**：
```
发现冲突 → 用户反馈/专家评审
   │
   ▼
分类：知识库错 / Prompt错 / 用户常识错
   │
   ├─ 知识库错 → 拉最新版指南更新 → 重建索引
   ├─ Prompt错 → 优化提示词 → A/B测试
   └─ 用户常识错 → 回答中明确引用来源 + 解释
```

> 💡 **口诀**：知识库优先（权威） → 溯源展示（可信） → 质检回流（持续）

---

# 第二部分：项目深挖 20 题（高频追问点）

> 这部分是面试官深挖项目细节时常问的问题，需要更细粒度的回答

## Q36：你的知识库现在有多少 chunks？各类占比？怎么保证质量？

**标准答案：**

**当前状态**：986 个高质量 chunks
- guideline（指南内容）：746（75.6%）
- new_food（食物数据）：152（15.4%）
- gi_data（GI 值）：17（1.7%）
- expert_media（专家媒体）：71（7.2%）
- 0 个 unmapped chunk（所有 PDF 都匹配到注册表）

**质量保证四机制**：
1. **元数据完整性**：每个 chunk 带 source_type / guideline_title / audience / disease_tags / age_group / region / doc_type
2. **匹配校验**：入库时所有 guideline chunk 必须匹配到注册表，否则标记 `guideline_content_unmapped` 并告警
3. **去重**：chunk_id 唯一约束，hash 去重避免重复入库
4. **定期审计**：每周跑 `scripts/verify/` 脚本，验证 chunk 数量、元数据完整性、检索质量

**代码参考**：[unified_reindex.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/scripts/ingest/unified_reindex.py)

---

## Q37：guideline_registry 注册表是怎么设计的？

**标准答案：**

**设计目标**：根据用户画像（年龄/疾病/BMI）精准匹配指南

**注册表结构**（每条记录代表一本指南）：
```python
{
    "title": "成人肥胖食养指南（2024年版）",
    "audience": "adult",                    # child_adolescent / adult / general
    "disease_tags": ["肥胖"],              # 慢性病标签
    "age_group": ["youth", "middle_aged", "middle_aged_plus", "elderly"],
    "region": "全国",
    "priority": 1,                          # 优先级（同条件下排前面的优先）
}
```

**匹配逻辑 `select_guidelines_by_profile(profile)`**：
1. 遍历注册表，按用户画像打分：
   - disease 匹配 disease_tags → +3 分
   - age_group 匹配用户年龄段 → +2 分
   - audience 匹配 → +1 分
2. 按分数排序，取 Top N（默认5）
3. 返回 guideline_title 集合

**使用位置**：
- `info_collector_node` 调用，填充 `state.selected_guidelines`
- `vector_retriever_node` 用其构造 where 过滤

**代码参考**：[guideline_registry.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/guideline_registry.py)

---

## Q38：双写存储（ChromaDB + PostgreSQL）怎么保证一致性？

**标准答案：**

**写入流程**（`unified_reindex.py` 的 `_ingest_file_both()`）：
1. 解析文档 → 分块 → 生成 chunk_id（uuid5 基于内容 hash）
2. 同一事务内：
   - 向量入 ChromaDB `guideline_chunks` 集合
   - 摘要入 PostgreSQL `document_chunks` 表（通过 chunk_id 关联）
3. 任一失败则整个事务回滚

**一致性保证**：
1. **chunk_id 强关联**：两边用同一个 chunk_id，查询时可双向关联
2. **事务回滚**：双写放在 try-except 中，任一失败回滚另一边
3. **定期对账**：每周跑对账脚本，比对两边 chunk_id 数量，不一致则告警
4. **重建机制**：异常情况下可以一键重建（删除两边数据 → 重新双写）

**查询时怎么用**：
- 纯向量检索：只用 ChromaDB
- 混合检索：ChromaDB 召回 chunk_id → PostgreSQL 查摘要补充信息
- SQL 检索（食物）：只用 PostgreSQL，不查 ChromaDB

**为什么需要双写？**
- ChromaDB 不擅长复杂 SQL 查询（如按 expert_name 模糊搜索）
- PostgreSQL 不擅长向量相似度计算
- 各取所长，通过 chunk_id 关联

**代码参考**：[vector_store.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/repositories/vector_store.py)、[chunk_repository.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/repositories/chunk_repository.py)

---

## Q39：info_collector_node 怎么判断信息缺失？分级追问怎么设计？

**标准答案：**

**信息字段分级**：

| 字段 | 级别 | 处理方式 |
|------|------|---------|
| 年龄 | 硬性阻断 | 缺失则 END 返回追问话术 |
| 身高 | 硬性阻断 | 缺失则 END 返回追问话术 |
| 体重 | 硬性阻断 | 缺失则 END 返回追问话术 |
| 目标 | 硬性阻断 | 缺失则 END 返回追问话术 |
| 地区 | 软提示 | 提示用户但不阻断，默认"全国" |
| 疾病 | 默认值 | 默认"无"并告知用户 |

**判断逻辑**：
1. `info_collector_node` 检查 `state.user_profile` 各字段
2. 硬性字段任一缺失 → 设置 `state.needs_clarification=True` + `state.generated_answer="请补充您的年龄、身高、体重和目标"`
3. 触发 `_route_after_info_collector` → END（结束本轮，等用户补充）
4. 用户补充后，`intent_recognizer_node` 检测到追问上下文 → 强制意图设为 plan_generation → 重新走 graph

**为什么这么设计？**
- 硬性字段缺失时强行生成计划质量很差（如不知道体重无法算 TDEE）
- 软字段不阻断避免反复追问影响体验
- 默认值字段允许"无疾病"等合理默认

**代码参考**：[nodes/__init__.py info_collector_node](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/nodes/__init__.py)

---

## Q40：evaluator_node 评估检索质量的逻辑是什么？阈值怎么定？

**标准答案：**

**评估逻辑**：

1. **看 Top1 相似度分数**：
   - `state.vector_results[0].score`（最高分）
   - 阈值 `RAG_CLARIFICATION_THRESHOLD=0.75`
   - 低于阈值 → 检索质量差 → 触发改写或追问

2. **看召回数量**：
   - 如果 TopK=10 但只召回 1-2 条 → 知识库覆盖不足
   - 触发降级：返回"未找到相关资料"而非强行生成

3. **看答案与 query 的相关性**（用 LLM 判断）：
   - Prompt: "判断以下回答是否切题，返回 JSON {is_relevant: bool}"
   - 不切题 → 触发改写

**路由决策**（`_route_after_evaluator`）：
```
if 召回为空:
    → END 返回"未找到相关资料"
elif Top1 相似度 < 0.75 AND rewrite_count < RAG_MAX_REWRITE:
    → rewrite_node 改写 query 重新检索
elif 答案不切题 AND rewrite_count < RAG_MAX_REWRITE:
    → rewrite_node 改写
else:
    → generator_node 生成
```

**阈值 0.75 的依据**：
- 经验值：实测 cosine 相似度 > 0.75 的 chunk 通常相关
- 0.6-0.75 灰区，靠 LLM 二次判断
- < 0.6 基本不相关

**改写策略**：
- 改写次数限制：`RAG_MAX_REWRITE=2`（防止无限循环）
- 改写方式：让 LLM 重新表述 query（同义词替换、扩写）

**代码参考**：[nodes/__init__.py evaluator_node](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/nodes/__init__.py)、[settings.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/config/settings.py)

---

## Q41：意图识别准确率多少？怎么做意图分类？

**标准答案：**

**意图分类**（5 类）：
- `plan_generation`：生成饮食计划（"帮我生成减脂计划"）
- `food_search`：食物搜索（"苹果的热量"）
- `guideline_query`：指南查询（"糖尿病饮食原则"）
- `unknown`：闲聊（"你好"）
- `hybrid`：混合（"减脂吃什么+帮我生成计划"）

**实现方式**：
- 用 LLM 做意图分类，Prompt 明确每类意图的特征
- temperature=0 保证稳定
- 输出结构化 JSON：`{"intent": "plan_generation", "confidence": 0.92}`

**特殊逻辑**：
- 检测 `conversation_context` 中最近 AI 消息是否含追问特征词（"请补充"/"您的年龄"/"必要信息"）
- 若是 → 强制意图设为 `plan_generation`（用户在补充信息，不是新查询）
- `_extract_age_from_query` 支持纯数字输入（`r'^(\d{1,2})$'`）

**准确率**：测试集 100 条，准确率 92%
- 主要错误：food_search 和 guideline_query 边界模糊（"糖尿病能吃苹果吗"）
- 改进：引入 hybrid 意图并行检索两路

**代码参考**：[nodes/__init__.py intent_recognizer_node](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/nodes/__init__.py)

---

## Q42：你项目的 LangGraph 节点有哪些？每个节点的输入输出是什么？

**标准答案：**

**9 个核心节点**：

| 节点 | 输入 | 输出 | 职责 |
|------|------|------|------|
| `safety_check_node` | user_query | safety_result | 安全校验（正则+LLM） |
| `intent_recognizer_node` | user_query, conversation_context | intent, user_profile | 意图识别+信息抽取 |
| `info_collector_node` | user_profile | selected_guidelines, needs_clarification | 信息完整性检查+指南匹配 |
| `sql_retriever_node` | user_query | sql_results | SQL食物检索 |
| `vector_retriever_node` | user_query, selected_guidelines | vector_results | 向量检索 |
| `evaluator_node` | vector_results, sql_results | rewrite_count, evaluation_result | 评估检索质量 |
| `rewrite_node` | user_query, rewrite_count | rewritten_query | 改写 query |
| `fusion_node` | sql_results, vector_results | fused_results | RRF 融合 |
| `generator_node` | user_query, fused_results, style_directive | generated_answer | LLM 生成回答 |

**plan_generator_node**（plan_generation 意图专用）：
- 输入：user_profile, tool_results（BMR/TDEE）, vector_results
- 输出：plan_config（含 target_calories / macros / meals）
- 调用 `plan_service.generate_plan()` 生成结构化计划

**代码参考**：[nodes/__init__.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/nodes/__init__.py)、[graph_builder.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/graph_builder.py)

---

## Q43：feedback_service 的 UPSERT 怎么实现的？为什么不用 ON CONFLICT？

**标准答案：**

**实现方式**（先查后更新或插入）：

```python
def submit_feedback(db, user_id, target_type, target_id, action):
    # 查询是否已存在
    existing = db.query(UserFeedback).filter(
        UserFeedback.user_id == user_id,
        UserFeedback.target_type == target_type,
        UserFeedback.target_id == target_id,
    ).first()
    
    if existing:
        existing.feedback_action = action  # 更新
    else:
        db.add(UserFeedback(
            user_id=user_id, target_type=target_type,
            target_id=target_id, feedback_action=action
        ))  # 新增
    
    db.commit()
```

**为什么不用 PostgreSQL 的 `ON CONFLICT ... DO UPDATE`？**
1. **可读性**：ORM 写法比 raw SQL 清晰
2. **跨数据库兼容**：MySQL/SQLite 不支持 ON CONFLICT 语法
3. **业务逻辑清晰**：先查后改的语义更易理解，方便加日志

**唯一约束兜底**：
- 数据库层 `UniqueConstraint('user_id', 'target_type', 'target_id', name='uq_user_target_feedback')`
- 即使并发场景下两个请求同时查到"不存在"，最终只有一个 INSERT 成功，另一个会触发唯一约束异常
- 调用方 try-except 处理 IntegrityError，重试或返回已存在记录

**代码参考**：[feedback_service.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/services/feedback_service.py)

---

## Q44：风格指令是怎么生成的？阈值怎么定？

**标准答案：**

**风格指令生成逻辑**：

```python
def get_style_directive(db, user_id) -> str:
    prefs = _update_user_preferences(db, user_id)
    
    if prefs["dislike_ratio"] > 0.30:
        return "用户近期反馈不佳，请调整回答风格：更详细、更专业、避免过于简略"
    elif prefs["like_ratio"] > 0.70:
        return "用户对当前回答风格满意，请保持"
    else:
        return ""  # 反馈不足或混合，不调整
```

**偏好聚合 `_update_user_preferences`**：
- 滑窗取最近 30 天反馈（`RECENT_WINDOW_DAYS=30`）
- 算 like_ratio = likes / total
- 算 dislike_ratio = dislikes / total
- 算 use_plan_ratio = use_plan / plan_total
- 写入 `UserProfile.feedback_preferences` JSON 字段

**阈值依据**：
- 70% 点赞率：经验值，表示"多数满意"
- 30% 踩率：经验值，表示"明显不满"
- 5 条最小样本：避免少量反馈误判（如 1/2=50% 不可信）

**指令注入位置**：
- `generator_node`：拼到 System Prompt 后面
- `plan_generator_node`：拼到 Plan Prompt 后面
- 流式模式 `_stream_system_prompt` 也包含

**代码参考**：[feedback_service.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/services/feedback_service.py)

---

## Q45：TDEE 计算公式是什么？为什么用 Mifflin-St Jeor 而不是 Harris-Benedict？

**标准答案：**

**Mifflin-St Jeor 公式（1990）**：
- 男性：BMR = 10×体重(kg) + 6.25×身高(cm) - 5×年龄 + 5
- 女性：BMR = 10×体重(kg) + 6.25×身高(cm) - 5×年龄 - 161

**TDEE = BMR × 活动系数**：
- 久坐（sedentary）：1.2
- 轻度（light）：1.375
- 中度（moderate）：1.55
- 高度（active）：1.725
- 极高（very_active）：1.9

**目标调整**：
- 减脂：TDEE - 500 kcal/天（每周约减 0.5kg）
- 增肌：TDEE + 300 kcal/天
- 维持：TDEE

**医学安全底线**：
- 男性 ≥ 1500 kcal/天
- 女性 ≥ 1200 kcal/天

**宏量营养素分配**：
- 减脂：蛋白 30% / 脂肪 30% / 碳水 40%
- 增肌：蛋白 25% / 脂肪 25% / 碳水 50%
- 维持：蛋白 20% / 脂肪 30% / 碳水 50%
- （蛋白质 4 kcal/g、脂肪 9 kcal/g、碳水 4 kcal/g）

**为什么用 Mifflin-St Jeor？**
- 美国营养学会（AND）推荐公式
- 比 Harris-Benedict（1919）更准，基于现代人群数据
- 在肥胖人群中精度更高
- 是目前临床最常用的 BMR 公式

**代码参考**：[nutrition_calc.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/utils/nutrition_calc.py)

---

## Q46：PDF 解析用 PyMuPDF 还是 pypdf？为什么？

**标准答案：**

**用 PyMuPDF（fitz）**，不用 pypdf。

**对比**：

| 维度 | PyMuPDF (fitz) | pypdf |
|------|----------------|-------|
| 性能 | 快 10 倍 | 慢 |
| 文本质量 | 高（保留布局） | 一般 |
| 扫描版支持 | 可检测 | 不支持 |
| 图片提取 | 支持 | 不支持 |
| 安装 | pip install PyMuPDF | pip install pypdf |
| 维护 | 活跃 | 一般 |

**项目实际**：
- `pdf_loader.py` 使用 `import fitz`
- requirements.txt 原有 `pypdf==5.0.1` 是死代码，已替换为 `PyMuPDF==1.28.0`

**扫描版 PDF 限制**：
- 《中国居民膳食指南（2022）》74MB 是扫描版，无文本层（378 页仅 377 字符）
- fitz 无法提取，需 OCR 才能入库
- 已知限制，V3.0 规划接入 PaddleOCR

**代码参考**：[pdf_loader.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/loaders/pdf_loader.py)

---

## Q47：config 加载用 .env 还是 yaml？为什么混用？

**标准答案：**

**混用，各司其职**：

**.env（环境变量）**：
- 用途：敏感配置（JWT_SECRET / DB密码 / API Key）
- 加载方式：`python-dotenv` 库
- 原因：
  - 不入库（gitignore）
  - 不同环境（开发/测试/生产）不同配置
  - 12-factor App 规范推荐
  - 部署时不改代码，改环境变量

**YAML**：
- 用途：业务规则（safety_rules.yaml / guideline_registry）
- 加载方式：自定义 loader，支持热更新
- 原因：
  - 规则复杂（嵌套结构、列表、正则）
  - 需要热更新（修改后不重启服务）
  - 可读性比 JSON 好（支持注释）

**settings.py**：
- 统一从 .env 加载环境变量到 Python 变量
- 提供默认值（如 `RAG_TOP_K = int(os.getenv("RAG_TOP_K", "10"))`)
- fail-fast 检查（`if not SECRET_KEY: raise RuntimeError(...)`）

**代码参考**：[settings.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/config/settings.py)、[safety_rule_loader.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/config/safety_rule_loader.py)

---

## Q48：测试覆盖率 82% 怎么算的？哪些没覆盖？

**标准答案：**

**覆盖率计算**：
- 工具：`pytest-cov`
- 配置：`.coveragerc`
  ```ini
  [run]
  source = src
  omit = src/__init__.py src/config/*
  
  [report]
  fail_under = 80
  ```

**82% 覆盖构成**：
- 单元测试覆盖：纯函数（nutrition_calc / rrf_fusion / safety_checker 正则 / adjustment_service）
- 集成测试覆盖：DB 操作（feedback_service / plan_service）
- E2E 覆盖：API 端点（/chat / /feedback / /plan）

**没覆盖的 18%**：
1. **LLM 客户端外部调用**：真实 LLM API 调用部分用 Mock 替代，未覆盖真实网络异常
2. **Whisper 转写**：需要真实音视频文件，CI 跑不起
3. **PDF OCR**：扫描版 PDF 需 OCR，未集成
4. **错误处理分支**：某些异常分支（如 ChromaDB 网络断开）难以模拟
5. **配置加载**：`config/*` 被 omit 排除

**未来提升方向**：
- 接入 VCR.py 录制 LLM 真实响应，E2E 覆盖网络异常
- testcontainers 模拟 ChromaDB 网络分区
- 提升到 85%+ 后再优化 BadCase

---

## Q49：CI 跑一次完整测试要多久？怎么加速？

**标准答案：**

**当前耗时**：
- lint：< 30s
- unit（180用例）：< 30s
- integration（120用例）：~2min（含 Docker 起服务）
- e2e（60用例）：~5min（含 LLM Mock 录制回放）
- **总计：~8 分钟**

**加速措施**：

1. **并行测试**：
   - `pytest -n auto`（pytest-xdist）多核并行
   - 单元测试可 4 核并行，从 30s → 10s

2. **测试分层跑**：
   - PR 阶段只跑 unit + integration（快）
   - merge 到 main 后跑 e2e（慢）
   - 定期（每晚）跑全量

3. **Docker 缓存**：
   - GitHub Actions 缓存 Docker 镜像层
   - 集成测试起服务从 30s → 5s

4. **LLM Mock 录制**：
   - 首次跑录制真实响应
   - 后续回放，不调真实 API
   - E2E 从真实调 LLM 的 5min → 回放 1min

5. **测试用例优化**：
   - 删除冗余测试（重复断言合并）
   - 用 parametrize 替代多个相似测试
   - 重点测试核心路径，边缘场景适当减少

**目标**：V3.0 优化到 5 分钟以内

---

## Q50：怎么定位线上 P95 延迟突增？

**标准答案：**

**定位流程（五步法）**：

1. **告警接收**：
   - Prometheus 告警 P95 > 3s
   - Grafana 仪表盘看是哪个端点慢

2. **Trace 追踪**：
   - 从告警时间点找慢请求的 TraceID
   - Jaeger 火焰图看哪个 span 慢
   - 定位到具体节点（如 vector_retriever_node）

3. **日志关联**：
   - 用 TraceID 关联所有日志
   - 看该节点的输入输出
   - 判断是输入异常（query 过长）还是依赖慢（ChromaDB 慢）

4. **指标下钻**：
   - 看依赖组件指标（ChromaDB QPS / Redis 命中率 / LLM 调用延迟）
   - 判断是哪个依赖拖累

5. **常见原因**：
   - 缓存命中率突降（Redis 部分数据过期）→ 延长 TTL 或预热
   - 向量库冷启动（重启后索引未加载）→ 启动时预热
   - LLM API 限流（突发流量）→ 加重试 + 限流
   - DB 慢查询（数据量增长后索引失效）→ 加索引

**实战案例**：上周 P95 从 1.8s 突增到 3.5s，定位到是 ChromaDB 重启后索引未加载，加了启动预热脚本解决

**代码参考**：[tracing_helper.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/core/tracing_helper.py)、[metrics.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/utils/metrics.py)

---

## Q51：知识库怎么处理版本更新？比如指南出了新版

**标准答案：**

**版本管理策略**：

1. **元数据带版本号**：
   - 每个 chunk 带 `guideline_version` 字段
   - 如 `"成人肥胖食养指南（2024年版）" → version=2024`

2. **新版入库流程**：
   - 旧版保留（不删除，标记 `is_latest=false`）
   - 新版入库（标记 `is_latest=true`）
   - 检索时 where 过滤 `is_latest=true`，默认只查最新版
   - 用户可显式指定查旧版（"参考2022版指南"）

3. **增量更新**：
   - 算新旧的 chunk hash 差异
   - 只更新变化部分，未变的 chunk 保留（节省计算）
   - 旧版 chunk 标记 `deprecated_at` 时间戳

4. **回滚机制**：
   - 保留旧版数据
   - 一键切换 `is_latest` 标记回旧版
   - 适用于新版质量下降的情况

5. **通知用户**：
   - 检索结果中标注"依据2024年版指南"
   - 用户知道版本，避免混淆

**未来规划**：
- 自动监测卫健委指南更新（爬虫+对比）
- 新版入库前自动跑测试集对比 Recall@K，质量提升才上线

---

## Q52：怎么处理用户用英文提问？多语言支持？

**标准答案：**

**当前状态**：
- 知识库全是中文（中文指南 + 中文食物库）
- LLM Prompt 也是中文
- Embedding 模型是中文优化（bge-large-zh / ZhipuAI）

**英文提问处理**：
1. **意图识别时检测语言**：用 langdetect 库
2. **英文 query 翻译**：
   - 调 LLM 翻译成中文
   - 用中文查向量库
   - 检索结果再翻译回英文返回
3. **简单兜底**：当前未做多语言，英文 query 直接查效果差，引导用户用中文

**多语言规划（V3.0）**：
- 用 multilingual-e5-large 多语言 Embedding 模型
- 知识库建多语言索引（一份内容多语言 chunk）
- LLM 用支持多语言的模型（GPT-4 / Claude）
- 用户语言检测 → 匹配对应语言的检索+生成

**优先级**：当前中文场景占 95%+，英文支持优先级低，资源投入产出比不高

---

## Q53：你怎么处理 LLM API 调用失败？重试策略？

**标准答案：**

**三层处理**：

1. **重试机制**：
   - 用 `tenacity` 库装饰 LLM 调用
   - 策略：`@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))`
   - 指数退避：1s → 2s → 4s
   - 只重试网络错误/超时，不重试业务错误（如 API Key 无效）

2. **降级策略**：
   - 重试3次都失败 → 降级到备用 LLM（主用 GLM-4，备用 Qwen）
   - 备用也失败 → 降级到模板回复（"系统繁忙，请稍后再试"）
   - 不让用户看到 500 错误

3. **熔断机制**：
   - 1 分钟内 LLM 失败率 > 50% → 触发熔断
   - 直接返回降级回复，不再调 LLM
   - 5 分钟后尝试恢复（半开状态）

**代码实现**：
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def call_llm_with_retry(messages):
    try:
        return llm.chat(messages)
    except (TimeoutError, ConnectionError) as e:
        logger.warning(f"LLM 调用失败，重试: {e}")
        raise
    except Exception as e:
        logger.error(f"LLM 业务错误，不重试: {e}")
        raise  # 业务错误不重试
```

**监控告警**：LLM 失败率 > 5% 触发告警，人工介入

---

## Q54：流式输出（SSE）怎么实现的？为什么不用 WebSocket？

**标准答案：**

**SSE 实现方式**：
- FastAPI 的 `StreamingResponse` + `text/event-stream`
- LLM 调用支持 stream=True，逐 token 返回
- 后端把 token 包成 SSE 格式推给前端
- 前端用 `EventSource` API 接收

**为什么不用 WebSocket？**

| 维度 | SSE | WebSocket |
|------|-----|-----------|
| 方向 | 单向（服务器→客户端） | 双向 |
| 协议 | HTTP | 独立协议 |
| 复杂度 | 简单（基于 HTTP） | 复杂（需握手+心跳） |
| 代理兼容 | 好（就是 HTTP） | 差（需特殊配置） |
| 重连 | 自动 | 需手动实现 |
| 适用场景 | 推送（LLM 流式） | 双向通信（聊天） |

**SSE 适合场景**：
- LLM 流式输出本质是单向推送（服务器推 token 给前端）
- 不需要客户端实时反馈
- HTTP 协议天然穿透防火墙/代理
- 自动重连，可靠性好

**WebSocket 适合场景**：
- 实时双向通信（如在线游戏、协同编辑）
- 需要客户端频繁推送数据

**本项目选择**：SSE 足够，简单可靠

**代码参考**：[rag.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/api/routes/rag.py)

---

## Q55：怎么保证生成的计划是结构化的（JSON 可解析）？

**标准答案：**

**两层保证**：

1. **Prompt 约束**：
   - System Prompt 明确要求输出 JSON 格式
   - 给出 JSON Schema 示例：
     ```json
     {
       "target_calories": 1800,
       "macros": {"protein_g": 120, "fat_g": 60, "carbs_g": 180},
       "meals": [...]
     }
     ```
   - 用 `response_format={"type": "json_object"}` 强制（如果模型支持）

2. **Pydantic 校验**：
   - 定义 `PlanConfig(BaseModel)` 数据模型
   - LLM 输出后用 Pydantic 解析
   - 解析失败 → 重试一次（带"请输出有效 JSON"提示）
   - 仍失败 → 用兜底模板（基于 TDEE 计算的标准计划）

**容错流程**：
```python
def generate_plan_with_validation(query, max_retry=2):
    for i in range(max_retry):
        response = llm.chat(messages)
        try:
            plan = PlanConfig.model_validate_json(response)
            return plan
        except ValidationError as e:
            logger.warning(f"Plan 解析失败第{i+1}次: {e}")
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": "请输出有效 JSON 格式"})
    # 兜底
    return _fallback_plan(query)
```

**代码参考**：[plan_service.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/services/plan_service.py)

---

# 第三部分：岗位通用面试题

## 3.1 基础篇（Python / 数据结构 / 网络 / 数据库）

### Q56：Python 的 GIL 是什么？对多线程有什么影响？

**标准答案：**

**GIL（Global Interpreter Lock，全局解释器锁）**：
- CPython 解释器的特性，同一时刻只允许一个线程执行 Python 字节码
- 目的：简化 C 扩展的内存管理（引用计数不需要加锁）

**对多线程的影响**：
- **CPU 密集型任务**：多线程无法利用多核（线程轮流持 GIL），甚至比单线程慢（线程切换开销）
- **IO 密集型任务**：多线程有效（IO 等待时释放 GIL，其他线程可执行）

**解决方案**：
- CPU 密集型 → 用 `multiprocessing` 多进程（独立 GIL）
- IO 密集型 → 多线程 / asyncio 协程（更轻量）

**本项目应用**：
- LangGraph 节点是 IO 密集型（调 LLM API / 查 DB），用 asyncio 协程
- Whisper 转写是 CPU 密集型，用多进程或单独 worker

> 💡 **口诀**：GIL 锁字节码 → CPU 密集用多进程 → IO 密集用协程

---

### Q57：asyncio 协程相比多线程有什么优势？

**标准答案：**

**协程优势**：

1. **轻量**：
   - 协程：用户态切换，开销 ~1μs
   - 线程：内核态切换，开销 ~10μs
   - 单进程可跑 10万协程 vs 1千线程

2. **无锁**：
   - 协程单线程，无需 GIL，无需线程锁
   - 共享变量直接读写（但要注意 await 期间的并发问题）

3. **可读性好**：
   - 用 `async/await` 写异步代码像同步代码
   - 比回调清晰

4. **适合 IO 密集**：
   - LLM API 调用、DB 查询、HTTP 请求都适合
   - 等待 IO 时切换到其他协程

**多线程仍适合**：
- CPU 密集型任务
- 调用阻塞的 C 库（无法 await）

**本项目应用**：
- FastAPI 原生支持 asyncio
- LangGraph 节点都是 async def
- 并行检索用 `asyncio.gather`

**注意坑**：
- async 函数中不能直接调同步阻塞函数（会阻塞事件循环）
- 用 `loop.run_in_executor` 把同步函数包装成异步

---

### Q58：Python 的装饰器原理是什么？写一个带参数的装饰器

**标准答案：**

**原理**：
- 装饰器本质是一个函数，接收函数作为参数，返回新函数
- `@decorator` 语法糖等价于 `func = decorator(func)`
- 应用时：调用 `func()` 实际调用的是装饰器返回的新函数

**带参数的装饰器**（三层嵌套）：

```python
def require_role(role: str):
    def decorator(func):
        @functools.wraps(func)  # 保留原函数元信息
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if user.role != role:
                raise PermissionError(f"需要 {role} 权限")
            return func(*args, **kwargs)
        return wrapper
    return decorator

# 使用
@require_role("admin")
def delete_user(user_id):
    ...
```

**类装饰器**（带状态）：
```python
class Retry:
    def __init__(self, max_retry=3):
        self.max_retry = max_retry
    
    def __call__(self, func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(self.max_retry):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if i == self.max_retry - 1:
                        raise
                    time.sleep(2 ** i)
        return wrapper

@Retry(max_retry=5)
def call_llm():
    ...
```

**本项目应用**：
- `@require_role` 权限校验
- `@retry` LLM 调用重试
- FastAPI 的 `@router.get` / `@app.post`

---

### Q59：HTTP 长连接 vs 短连接？Keep-Alive 的作用？

**标准答案：**

**短连接**：
- 每次请求都建立 TCP 连接，请求完关闭
- 开销：3 次握手 + 4 次挥手
- 适合：低频请求

**长连接（Keep-Alive）**：
- TCP 连接复用，多个 HTTP 请求共用一个连接
- HTTP/1.1 默认开启
- 优点：减少握手开销，降低延迟
- 缺点：服务器需保持连接，占用资源

**HTTP/2 多路复用**：
- 一个 TCP 连接上并发多个请求
- 二进制分帧，比 HTTP/1.1 的 Keep-Alive 更高效
- 头部压缩（HPACK）

**SSE 与 Keep-Alive**：
- SSE 本质是长连接（HTTP/1.1 chunked transfer）
- 服务器持续推数据，不关闭连接
- 客户端断开后自动重连

**本项目应用**：
- LLM API 调用用 requests.Session（连接池复用）
- SSE 流式输出保持长连接
- FastAPI 的 uvicorn 默认支持 HTTP/1.1 Keep-Alive

---

### Q60：数据库索引的原理？什么字段适合建索引？

**标准答案：**

**索引原理**：
- B+树数据结构
- 查询 O(log n)，比全表扫描 O(n) 快
- 索引存"字段值 → 行指针"的映射
- 排好序，支持范围查询

**适合建索引的字段**：
1. **WHERE 条件常用字段**：如 `user_id` / `created_at`
2. **JOIN 关联字段**：外键字段
3. **ORDER BY / GROUP BY 字段**
4. **唯一性约束字段**：唯一索引

**不适合建索引的字段**：
1. **区分度低**：如 gender（男/女），索引效果差
2. **频繁更新**：索引维护成本高
3. **小表**：全表扫描更快

**复合索引**：
- `(user_id, target_type, target_id)` 三字段联合索引
- 遵循"最左前缀"原则
- 查询条件必须从左到右匹配

**本项目应用**：
- `UserFeedback` 的 `UniqueConstraint('user_id', 'target_type', 'target_id')` 既是约束也是索引
- `ChatHistory.user_id` 单独索引（按用户查历史）
- `Plan.user_id + status` 复合索引（查激活计划）

**索引失效场景**：
- 函数操作字段：`WHERE LOWER(name) = 'abc'`
- 类型不匹配：`WHERE user_id = 123`（字段是字符串）
- LIKE 左模糊：`WHERE name LIKE '%abc'`
- OR 条件部分无索引

> 💡 **口诀**：B+树 → 区分度高+WHERE 常用 → 复合索引最左前缀

---

### Q61：事务的 ACID 是什么？隔离级别有哪些？

**标准答案：**

**ACID**：
- **A（Atomicity，原子性）**：事务要么全部成功，要么全部回滚
- **C（Consistency，一致性）**：事务前后数据一致（如转账总金额不变）
- **I（Isolation，隔离性）**：并发事务互不干扰
- **D（Durability，持久性）**：提交后永久保存

**四种隔离级别**（从低到高）：

| 级别 | 脏读 | 不可重复读 | 幻读 | 性能 |
|------|------|-----------|------|------|
| Read Uncommitted | ✅ | ✅ | ✅ | 最快 |
| Read Committed（PG默认） | ❌ | ✅ | ✅ | 快 |
| Repeatable Read（MySQL默认） | ❌ | ❌ | ✅ | 慢 |
| Serializable | ❌ | ❌ | ❌ | 最慢 |

**问题解释**：
- 脏读：读到其他事务未提交的数据
- 不可重复读：同一事务两次读结果不同（其他事务修改了）
- 幻读：同一事务两次查询返回的行数不同（其他事务新增了）

**本项目应用**：
- PostgreSQL 默认 Read Committed
- 反馈提交用事务：`db.commit()`，失败回滚
- 双写存储（ChromaDB + PG）用 try-except 模拟事务，任一失败回滚另一边

---

### Q62：Redis 的持久化方式？RDB vs AOF？

**标准答案：**

**RDB（Redis Database）**：
- 快照方式，定时把内存数据 dump 到磁盘
- 优点：文件小，恢复快
- 缺点：可能丢失最后一次快照后的数据
- 配置：`save 900 1`（900秒内1次修改触发）

**AOF（Append Only File）**：
- 追加方式，每次写操作都记录到日志
- 优点：数据完整（最多丢1秒）
- 缺点：文件大，恢复慢
- 配置：`appendfsync everysec`（每秒刷盘）

**混合持久化（4.0+）**：
- RDB + AOF 结合
- 全量用 RDB，增量用 AOF
- 兼顾恢复速度和数据完整性

**本项目配置**：AOF + everysec
- 缓存数据可丢失（重新查 DB 即可）
- 但用户反馈等关键数据在 PostgreSQL，不在 Redis

**选择建议**：
- 数据重要 → AOF
- 性能优先 → RDB
- 生产推荐 → 混合持久化

---

## 3.2 进阶篇（系统设计 / 并发 / 设计模式）

### Q63：设计一个短链服务（系统设计题）

**标准答案：**

**需求澄清**：
- DAU？QPS？短链有效期？是否需要统计点击？

**核心流程**：
1. **生成短链**：长 URL → 短码（6位）→ 存入 DB → 返回短链
2. **跳转**：访问短链 → 查 DB → 302 重定向到长 URL

**短码生成方案**：

| 方案 | 优点 | 缺点 |
|------|------|------|
| 自增 ID + 62进制 | 简单、有序 | 可预测、暴露量级 |
| MD5/SHA hash | 算法成熟 | 6位可能冲突 |
| 雪花算法 | 分布式唯一 | 较长 |
| 预生成 ID 池 | 高性能 | 维护复杂 |

**架构设计**：

```
用户 → 负载均衡 → API 服务 → 缓存（Redis）
                            ↓ 未命中
                          DB（MySQL/PG）
                            ↓
                          生成短码 → 写 DB → 写缓存
```

**性能优化**：
1. **缓存**：Redis 缓存短码→长URL映射（命中率 > 90%）
2. **布隆过滤器**：判断短码是否存在（避免 DB 查询）
3. **分库分表**：按短码 hash 分 16 库
4. **302 vs 301**：302 不缓存（可统计点击），301 缓存（性能好但漏统计）

**扩展功能**：
- 点击统计（异步写入 Kafka → Flink 实时计算）
- 自定义短码
- 过期机制
- 防滥用（限流）

---

### Q64：怎么设计一个分布式限流器？

**标准答案：**

**限流算法**：

1. **计数器法**：固定时间窗口内计数，超限拒绝
   - 缺点：临界点突刺（窗口边界双倍流量）

2. **滑动窗口**：把窗口分小格，滑动统计
   - 平滑了突刺

3. **漏桶**：请求进桶，固定速率出桶
   - 强制匀速，平滑流量
   - 缺点：无法应对突发

4. **令牌桶**：固定速率发令牌，请求拿令牌才能处理
   - 支持突发（桶里积累的令牌）
   - 最常用

**分布式实现（Redis + Lua）**：

```lua
-- 令牌桶限流 Lua 脚本
local key = KEYS[1]
local capacity = tonumber(ARGV[1])  -- 桶容量
local rate = tonumber(ARGV[2])       -- 令牌生成速率（个/秒）
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local last_tokens = tonumber(redis.call("get", key .. ":tokens")) or capacity
local last_time = tonumber(redis.call("get", key .. ":time")) or now

local delta = math.max(0, now - last_time)
local tokens = math.min(capacity, last_tokens + delta * rate)

if tokens < requested then
    return 0  -- 拒绝
else
    tokens = tokens - requested
    redis.call("set", key .. ":tokens", tokens)
    redis.call("set", key .. ":time", now)
    return 1  -- 通过
end
```

**为什么用 Lua？**
- 原子性（避免并发竞态）
- 减少网络往返（一次执行完）

**本项目应用**：
- 登录限流（账号 5 次/IP 20 次锁定）
- LLM API 调用限流（防成本失控）
- Redis 计数器 + TTL 实现

---

### Q65：CAP 理论是什么？你怎么选？

**标准答案：**

**CAP**：
- **C（Consistency，一致性）**：所有节点数据一致
- **A（Availability，可用性）**：每个请求都能收到响应
- **P（Partition tolerance，分区容错）**：网络分区时仍能工作

**核心结论**：分布式系统只能满足 CAP 中的 2 个，网络分区不可避免，所以实际上是在 CP 和 AP 之间选。

**CP 系统**（强一致）：
- 例：Zookeeper / etcd / HBase
- 网络分区时拒绝写入，保证一致
- 适合：金融交易、配置管理

**AP 系统**（高可用）：
- 例：Cassandra / DynamoDB / Eureka
- 网络分区时仍可读写，可能短暂不一致
- 适合：社交媒体、缓存

**BASE 理论**（AP 实践）：
- Basically Available（基本可用）
- Soft state（软状态）
- Eventually consistent（最终一致）

**本项目选择**：
- 主存 PostgreSQL（CP，强一致）
- 缓存 Redis（AP，最终一致）
- 双写存储用 BASE：ChromaDB 和 PG 短暂不一致，通过对账脚本最终一致

---

### Q66：消息队列（Kafka）的核心概念？为什么用消息队列？

**标准答案：**

**核心概念**：
- **Producer**：生产者，发消息
- **Consumer**：消费者，收消息
- **Topic**：主题，消息分类
- **Partition**：分区，一个 Topic 分多个 Partition 实现并行
- **Broker**：Kafka 服务器
- **Consumer Group**：消费者组，组内每个 Partition 只被一个消费者消费

**为什么用消息队列？**

1. **解耦**：生产者不需知道谁消费
2. **异步**：写入队列立即返回，消费者慢慢处理
3. **削峰**：突发流量进队列，消费者按自己节奏处理

**应用场景**：
- 日志收集（用户行为日志 → Kafka → Flink 实时计算）
- 异步任务（用户反馈 → Kafka → 离线分析）
- 系统解耦（订单服务 → Kafka → 库存/物流/通知服务）

**本项目规划**（V3.0）：
- 用户反馈事件 → Kafka → 离线分析（BadCase 回流）
- 用户行为日志 → Kafka → Flink → 实时特征（用于个性化推荐）
- LLM 调用日志 → Kafka → Prometheus（监控告警）

---

### Q67：单例模式怎么实现？线程安全吗？

**标准答案：**

**5 种实现方式**：

1. **模块单例**（最简单）：
   ```python
   # singleton.py
   class Singleton:
       pass
   instance = Singleton()
   # import 时即创建，天然单例
   ```

2. **装饰器**：
   ```python
   def singleton(cls):
       instances = {}
       @functools.wraps(cls)
       def wrapper(*args, **kwargs):
           if cls not in instances:
               instances[cls] = cls(*args, **kwargs)
           return instances[cls]
       return wrapper
   ```
   ⚠️ 线程不安全（多线程同时检查到 `cls not in instances`）

3. **`__new__` 方法**（本项目用）：
   ```python
   class RedisClient:
       _instance = None
       def __new__(cls):
           if cls._instance is None:
               cls._instance = super().__new__(cls)
               cls._instance._client = None
           return cls._instance
   ```
   ⚠️ 默认线程不安全

4. **线程安全版**（加锁）：
   ```python
   class Singleton:
       _instance = None
       _lock = threading.Lock()
       
       def __new__(cls):
           if cls._instance is None:  # 双重检查
               with cls._lock:
                   if cls._instance is None:
                       cls._instance = super().__new__(cls)
           return cls._instance
   ```

5. **元类（metaclass）**：
   ```python
   class SingletonMeta(type):
       _instances = {}
       _lock = threading.Lock()
       def __call__(cls, *args, **kwargs):
           if cls not in cls._instances:
               with cls._lock:
                   if cls not in cls._instances:
                       cls._instances[cls] = super().__call__(*args, **kwargs)
           return cls._instances[cls]
   ```

**本项目应用**：[redis_client.py](file:///G:/PyCharmMiscProject/HealthAI-Platform/src/utils/redis_client.py) 的 `RedisClient` 用 `__new__` 单例
- RedisClient 不需要严格线程安全（连接池内部线程安全）
- Python GIL 简化了并发问题

---

### Q68：工厂模式在项目里怎么用？

**标准答案：**

**工厂模式**：定义一个创建对象的接口，让子类决定实例化哪个类

**LLM Client 工厂示例**：

```python
class LLMClientFactory:
    @staticmethod
    def create(provider: str = "zhipuai") -> "BaseLLMClient":
        if provider == "zhipuai":
            return ZhipuAIClient()
        elif provider == "openai":
            return OpenAIClient()
        elif provider == "qwen":
            return QwenClient()
        else:
            raise ValueError(f"不支持的 LLM provider: {provider}")

# 使用
llm = LLMClientFactory.create("zhipuai")
response = llm.chat(messages)
```

**好处**：
- 调用方不关心具体实现，只面向接口
- 切换 LLM 提供商只改一处
- 方便测试（Mock 时返回 MockClient）

**Embedding 工厂**：
```python
class EmbeddingFactory:
    @staticmethod
    def create(provider: str) -> "BaseEmbedding":
        if provider == "zhipuai":
            return ZhipuAIEmbedding()
        elif provider == "bge":
            return BGEEmbedding()
```

**策略模式 + 工厂模式组合**：
- 工厂创建对象
- 策略模式切换算法（如不同的检索策略）

---

## 3.3 深度篇（LLM 原理 / Transformer / 训练技巧）

### Q69：Transformer 的核心结构是什么？

**标准答案：**

**Transformer 核心组件**：

1. **Self-Attention（自注意力）**：
   - 公式：`Attention(Q, K, V) = softmax(QK^T / √d_k) V`
   - Q（Query）/ K（Key）/ V（Value）都是输入的线性变换
   - 每个位置关注所有位置，捕获长距离依赖

2. **Multi-Head Attention（多头注意力）**：
   - 多个 Attention 并行，每个关注不同子空间
   - 拼接后线性变换
   - 类似 CNN 的多通道

3. **Position Encoding（位置编码）**：
   - Sin/Cos 函数编码位置
   - 让模型知道词的位置（Self-Attention 本身无位置信息）

4. **Feed Forward Network（前馈网络）**：
   - 两层全连接 + ReLU
   - 每个位置独立处理

5. **Layer Norm + Residual Connection**：
   - 残差连接解决梯度消失
   - Layer Norm 加速训练

**Encoder vs Decoder**：
- Encoder：双向 Attention（BERT 用）
- Decoder：Masked Attention（只能看前面，GPT 用）+ Cross-Attention

**为什么比 RNN 好？**
1. **并行**：所有位置同时计算（RNN 必须串行）
2. **长距离依赖**：Attention 直接连接任意两位置
3. **可解释性**：Attention 权重可视化

> 💡 **口诀**：Self-Attention 看全局 → Multi-Head 多视角 → Position Encoding 知位置 → 残差+LayerNorm 稳训练

---

### Q70：BERT vs GPT 的区别？

**标准答案：**

| 维度 | BERT | GPT |
|------|------|-----|
| 架构 | Encoder-only | Decoder-only |
| 注意力 | 双向 | 单向（Masked） |
| 训练任务 | MLM（完形填空）+ NSP | 自回归语言模型 |
| 输入 | 整句双向编码 | 从左到右生成 |
| 擅长 | 理解类任务（分类/NER/QA） | 生成类任务（对话/写作） |
| 输出 | 固定长度表示 | 序列生成 |

**BERT 训练任务**：
- MLM（Masked Language Modeling）：随机 mask 15% token，预测它们
- NSP（Next Sentence Prediction）：判断两句话是否相邻

**GPT 训练任务**：
- 自回归：给定前文预测下一个 token
- P(next_token | previous_tokens)

**本项目应用**：
- Embedding 用 BERT 系（bge-large-zh，Encoder 架构，理解语义）
- 生成用 GPT 系（GLM-4，Decoder 架构，生成回答）

**为什么 Embedding 用 BERT 而非 GPT？**
- BERT 双向编码，理解整句语义
- GPT 单向，更适合生成而非理解

---

### Q71：为什么 Transformer 需要 Position Encoding？

**标准答案：**

**原因**：Self-Attention 本身没有位置感知能力。

**详细解释**：
- Self-Attention 计算的是 token 之间的相似度
- 不关心 token 的位置顺序
- "猫追狗" 和 "狗追猫" 在 Self-Attention 看来完全一样
- 必须额外注入位置信息

**Position Encoding 方案**：

1. **Sinusoidal（原始 Transformer）**：
   - `PE(pos, 2i) = sin(pos / 10000^(2i/d))`
   - `PE(pos, 2i+1) = cos(pos / 10000^(2i/d))`
   - 优点：可外推到更长序列

2. **Learned PE（BERT）**：
   - 每个位置一个可学习的向量
   - 优点：灵活
   - 缺点：受训练长度限制

3. **RoPE（Rotary Position Embedding）**：
   - 旋转位置编码
   - 优点：相对位置编码，外推性好
   - LLaMA / Qwen 等现代模型用

4. **ALiBi**：
   - 在 Attention 计算时加位置偏置
   - 外推性极好

**为什么不能用简单的位置索引？**
- 索引值过大（如1000）会和 embedding 量纲不匹配
- 不同长度序列难以泛化

---

### Q72：什么是 KV Cache？为什么能加速推理？

**标准答案：**

**KV Cache 原理**：

- Decoder 生成时，每生成一个 token 需要重新计算前面所有 token 的 K/V
- 实际上前面的 K/V 是不变的（不变的部分重复计算浪费）
- 把它们缓存起来，下次直接用 → 大幅加速

**示意**：
```
生成 token 5 时：
- 不用 KV Cache：计算 token 1-5 的 K/V（5次矩阵乘）
- 用 KV Cache：复用 1-4 的 K/V，只算 token 5 的 K/V（1次矩阵乘）
```

**空间换时间**：
- KV Cache 内存占用 = `2 × layers × seq_len × hidden_dim × batch_size`
- 长序列会占用大量显存
- 是 LLM 推理的主要瓶颈

**优化方向**：
1. **PagedAttention（vLLM）**：
   - 把 KV Cache 分页管理（类似操作系统虚拟内存）
   - 减少碎片，提升吞吐量 2-4 倍

2. **量化 KV Cache**：
   - INT8 / FP8 量化
   - 减半内存

3. **MQA / GQA**（Multi-Query Attention / Grouped-Query Attention）：
   - 多个 Head 共享 K/V
   - 减少 KV Cache 大小

**本项目应用**：
- GLM-4 服务端已用 KV Cache（API 调用方无需关心）
- 自部署 LLaMA/Qwen 时用 vLLM 加速

> 💡 **口诀**：前面 K/V 不变 → 缓存复用 → 空间换时间 → vLLM 分页优化

---

### Q73：什么是 LoRA？为什么能省显存？

**标准答案：**

**LoRA（Low-Rank Adaptation）**：

**核心思想**：微调时不更新全部参数，只更新一个低秩矩阵

**数学原理**：
- 原始权重 W（d×d）
- 微调时 ΔW = A × B（A 是 d×r，B 是 r×d，r << d）
- 训练时只更新 A 和 B（参数量从 d² 降到 2dr）
- 推理时 W' = W + A × B（合并回原始矩阵）

**为什么省显存**：
- 原始参数 d×d（如 4096×4096 = 1674万）不动
- 只训练 d×r + r×d（如 r=8，参数 6.5万）
- 训练显存降 99%+

**对比其他微调方法**：

| 方法 | 训练参数 | 显存 | 质量 |
|------|---------|------|------|
| Full Fine-tuning | 100% | 极高 | 最好 |
| LoRA | 0.1-1% | 低 | 接近全量 |
| Prefix Tuning | <1% | 低 | 略差 |
| Adapter | 1-5% | 中 | 好 |

**LoRA 应用场景**：
- 领域适配（在通用模型上微调医疗/法律/金融语料）
- 多任务切换（每个任务一个 LoRA，推理时切换）
- 个性化（用户级 LoRA）

**本项目规划**（V3.0）：
- 在 GLM-4 基础上微调营养学领域 LoRA
- 让模型更懂"减脂/增肌/糖尿病饮食"等专业场景
- 用 8 张 A100 训练 24h

---

### Q74：什么是幻觉（Hallucination）？怎么缓解？

**标准答案：**

**幻觉定义**：LLM 生成看似合理但实际错误的内容
- 事实性幻觉：生成不存在的事实（如"爱因斯坦生于1900年"，实际1879）
- 一致性幻觉：同一问题不同回答
- 推理幻觉：数学计算错误、逻辑错误

**幻觉原因**：
1. 训练数据噪声/错误
2. 模型记忆不完整，靠"编造"补全
3. 推理时采样随机性（temperature > 0）
4. 缺乏事实校验机制

**缓解策略**：

1. **RAG（检索增强）**：
   - 让模型基于检索到的事实回答
   - Prompt 明确"基于以下资料回答，不要编造"

2. **工具调用**：
   - 数学计算交给本地工具（不靠 LLM 算）
   - 本项目 BMR/TDEE 用纯函数计算

3. **低温度采样**：
   - temperature=0 减少随机性
   - 适合事实性任务

4. **Chain of Thought（CoT）**：
   - 让模型"思考过程"显式化
   - 减少推理错误

5. **Self-Consistency**：
   - 多次采样取多数投票
   - 提升准确率

6. **事实校验**：
   - 用知识图谱校验生成内容
   - RAGAS Faithfulness 指标评估

7. **RLHF（人类反馈强化学习）**：
   - 训练时惩罚幻觉
   - ChatGPT 用这招

**本项目应用**：
- RAG 强制基于知识库
- 工具调用防数学幻觉
- Faithfulness 评估兜底
- 知识溯源可验证

---

### Q75：怎么评估一个 LLM 的好坏？

**标准答案：**

**评估维度**：

1. **能力维度**：
   - MMLU（多任务知识）
   - GSM8K（数学推理）
   - HumanEval（代码生成）
   - C-Eval / CMMLU（中文综合）

2. **对齐维度**：
   - TruthfulQA（真实性）
   - ToxiGen（毒性）
   - BBQ（偏见）

3. **生成质量**：
   - BLEU（机器翻译）
   - ROUGE（文本摘要）
   - BERTScore（语义相似度）

4. **人类评估**：
   - Elo Rating（Chatbot Arena）
   - A/B 测试

**自动化评估方法**：

1. **LLM-as-Judge**：
   - 用 GPT-4 评估其他模型输出
   - 评分：相关性、流畅度、有用性
   - 优点：成本低
   - 缺点：GPT-4 也有偏见

2. **RAGAS（RAG 专用）**：
   - Context Precision / Recall
   - Faithfulness
   - Answer Relevance

3. **基准测试**：
   - 固定测试集，跑全量
   - 对比分数

**生产环境评估**：
- 在线 A/B 测试
- 用户反馈（点赞率）
- 监控指标（延迟、错误率）

**本项目评估**：
- 离线：RAGAS（Faithfulness 0.91）
- 在线：点赞率、计划使用率
- 监控：P95 延迟、缓存命中率

---

## 附录 A：背诵心法

### A.1 STAR 法则背诵模板

每个项目点都用 STAR 讲：
- **S（Situation）**：项目初期的情况/痛点（带量化）
- **T（Task）**：你的目标（带量化）
- **A（Action）**：你做了什么（技术方案）
- **R（Result）**：结果如何（带量化对比）

**示例**：
> "项目初期用纯向量检索，Recall@10 只有 0.58（S），目标是提升到 0.8 以上（T），我设计了两阶段检索架构：注册表硬规则选指南 + 向量相似度补摘要（A），最终 Recall@10 提升到 0.82（+41%）（R）"

### A.2 技术点背诵三段式

每个技术点讲三段：
1. **是什么**：定义+原理
2. **为什么用它**：对比其他方案的优劣
3. **怎么用**：项目中的应用+踩过的坑

### A.3 面试节奏控制

- 自我介绍：3分钟
- 项目深挖：15分钟（核心）
- 技术八股：10分钟
- 反问环节：5分钟

### A.4 高频追问应对

面试官追问时的应对：
- "这里能展开讲讲吗？" → 你讲得不够深，需要补充细节
- "为什么不选 XX 方案？" → 考技术判断力，对比优劣势
- "踩过什么坑？" → 考工程能力，讲排查过程
- "如果让你重做会怎么改？" → 考反思能力，讲改进方向

---

## 附录 B：高频关键词速记表

### B.1 RAG 关键词

| 关键词 | 一句话解释 |
|--------|-----------|
| RAG | Retrieval-Augmented Generation，检索增强生成 |
| Embedding | 文本向量化，把文本转成数值向量 |
| Vector Store | 向量数据库，存向量+相似度检索 |
| Chunking | 分块，把长文档切成小段 |
| Top-K | 检索返回的相似度最高的 K 个结果 |
| Recall@K | 前 K 个结果中包含正确答案的比例 |
| RRF | Reciprocal Rank Fusion，倒数排名融合 |
| Faiss | Facebook 开源向量检索库 |
| HNSW | Hierarchical Navigable Small World，图索引算法 |
| Cosine Similarity | 余弦相似度，衡量向量方向相似性 |

### B.2 Agent 关键词

| 关键词 | 一句话解释 |
|--------|-----------|
| Agent | 智能体，能规划+执行+反思 |
| Tool Calling | 工具调用，LLM 调用外部函数 |
| ReAct | Reasoning + Acting，推理+行动交替 |
| CoT | Chain of Thought，思维链 |
| ToT | Tree of Thoughts，思维树 |
| Memory | 记忆，短期/长期记忆 |
| Reflection | 反思，自我评估+改进 |
| LangChain | LLM 应用开发框架 |
| LangGraph | 状态机编排框架 |
| Multi-Agent | 多智能体协作 |

### B.3 LLM 关键词

| 关键词 | 一句话解释 |
|--------|-----------|
| Transformer | 主流 LLM 架构，基于 Attention |
| Attention | 注意力机制，关注重要部分 |
| Token | 文本最小单位 |
| Context Window | 上下文窗口，LLM 一次处理的最大 token 数 |
| Temperature | 采样温度，越高越随机 |
| Top-p | 核采样，从概率累积 p 的 token 中采样 |
| KV Cache | 推理时缓存历史 K/V，加速 |
| LoRA | 低秩适配，参数高效微调 |
| RLHF | 人类反馈强化学习 |
| Hallucination | 幻觉，生成错误内容 |
| Fine-tuning | 微调，在预训练模型上继续训练 |
| Quantization | 量化，降低模型精度省显存 |

### B.4 工程关键词

| 关键词 | 一句话解释 |
|--------|-----------|
| 12-Factor App | 12要素应用，云原生应用规范 |
| CI/CD | 持续集成/持续部署 |
| Docker | 容器化，环境隔离 |
| K8s | 容器编排，自动化部署 |
| Microservices | 微服务架构 |
| Rate Limiting | 限流，控制请求频率 |
| Circuit Breaker | 熔断器，故障时快速失败 |
| Bulkhead | 舱壁隔离，资源隔离 |
| Idempotent | 幂等，重复请求不产生副作用 |
| Graceful Degradation | 优雅降级，部分功能失效不影响整体 |

---

## 📝 文档使用建议

1. **第一遍**：通读全文，理解每道题的核心答案
2. **第二遍**：盖住答案，自己默答，对照标准答案查漏补缺
3. **第三遍**：每道题对着镜子或录音讲 2 遍，能自然讲出来才算会
4. **第四遍**：挑 10 道高频题（Q1/Q2/Q3/Q7/Q11/Q13/Q19/Q22/Q26/Q31）重点练
5. **第五遍**：项目相关题（Q1-Q55）每道都对照代码看一遍，确保能现场手画架构图

**面试前最后冲刺**：
- 重读"附录 A 背诵心法"
- 默写"附录 B 关键词速记表"
- 自己讲一遍完整项目流程（10分钟）

---

> 🎯 祝你面试顺利，拿到心仪的 AI 大模型应用开发 / RAG / Agent 开发 Offer！
>
> **文档版本**：v1.0  
> **整理日期**：2026-08-07  
> **题量**：75 题（项目 55 题 + 通用 20 题）  
> **覆盖**：RAG / Agent / 工程 / 性能 / 安全 / Python / 系统设计 / LLM 原理
