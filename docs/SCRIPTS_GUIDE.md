# 运维脚本功能详解

> scripts/ 目录下所有运维与数据处理脚本的功能说明、使用方法和依赖关系。

## 📋 目录

- [脚本分类总览](#脚本分类总览)
- [ingest/ 入库脚本](#ingest-入库脚本)
- [etl/ 数据清洗脚本](#etl-数据清洗脚本)
- [verify/ 验证脚本](#verify-验证脚本)
- [utils/ 工具脚本](#utils-工具脚本)

---

## 脚本分类总览

| 类别 | 目录 | 数量 | 用途 |
|------|------|------|------|
| **入库** | `scripts/ingest/` | 6 | 数据抽取、转换、双写入库 |
| **清洗** | `scripts/etl/` | 3 | 数据质量修复、异常值处理 |
| **验证** | `scripts/verify/` | 3 | 入库一致性、数据质量检查 |
| **工具** | `scripts/utils/` | 2 | 缓存清理等辅助操作 |

---

## ingest/ 入库脚本

数据入库的核心流程：`raw_data/*` → 解析 → 语义分块 → 双写（ChromaDB + PostgreSQL）

### unified_reindex.py

**功能**：统一数据入库脚本，支持指南 PDF、新食品 PDF、GI 数据、专家媒体视频的一键入库。

**特点**：
- 双写存储：ChromaDB（向量语义检索）+ PostgreSQL document_chunks（BM25 关键词检索）
- 语义分块：使用 `SemanticChunker` 替代固定长度分块
- 支持增量/全量入库
- 大文件流式处理（74MB PDF 单文件隔离，不中断整体入库）

**用法**：
```bash
# 全量入库（指南 + 新食品 + GI + 专家媒体）
python scripts/ingest/unified_reindex.py --target all

# 仅入库指南
python scripts/ingest/unified_reindex.py --target guidelines

# 仅入库 GI 数据
python scripts/ingest/unified_reindex.py --target gi_data

# 仅入库新食品
python scripts/ingest/unified_reindex.py --target new_food

# 清空后全量重建
python scripts/ingest/unified_reindex.py --reset

# 专家媒体仅测试1个视频
python scripts/ingest/unified_reindex.py --target expert --test
```

**数据源**：
- `raw_data/raw/guidelines/` → 指南 PDF
- `raw_data/raw/new_foods/` → 新食品 PDF
- `glycemic_index_facts` 表 → GI 升糖指数数据
- `raw_data/raw/person_exps/` → 专家视频（需 Whisper 依赖）

---

### import_gi.py

**功能**：升糖指数（GI）数据的 ETL 全流程。

**流程**：
1. 从 `raw_data/china-food-composition-data/json_gi_of_foods/glycemic_index_of_foods.json` 读取原始 JSON
2. 解析每条记录的食物名称、GI 值、食物分类
3. 映射 GI 分类（低 GI ≤55 / 中 GI 56-70 / 高 GI >70）
4. 写入 PostgreSQL `glycemic_index_facts` 表

**用法**：
```bash
python scripts/ingest/import_gi.py
```

**输出**：259 条 GI 记录，覆盖 11 个食物大类（谷豆类、水果类、蔬菜类、饮料类等）

---

### import_nutrition.py

**功能**：营养成分数据的 ETL 全流程（抽取、转换、加载）。

**流程**：
1. 读取 `china-food-composition-data/json_data/` 下的食物营养成分 JSON
2. 清洗数值字段（处理缺失值、单位换算、异常值）
3. 标准化食物名称（别名归一）
4. 写入 PostgreSQL `nutrition_facts` 表

**用法**：
```bash
python scripts/ingest/import_nutrition.py
```

**数据覆盖**：能量、蛋白质、脂肪、碳水化合物、维生素、矿物质等 30+ 营养素字段。

---

### reindex_guidelines.py

**功能**：重建 ChromaDB 指南索引，清空 collection 后重新入库。

**特点**：
- 仅处理指南 PDF（不含新食品、GI 数据）
- 使用 `GuidelineRegistry` 元数据（年龄组、疾病标签、地区）
- 写入 `guide_catalog` 摘要 chunk 参与语义匹配

**用法**：
```bash
# 清空后重建指南索引
python scripts/ingest/reindex_guidelines.py

# 保留已入库的专家摘要
python scripts/ingest/reindex_guidelines.py --keep-seed-expert
```

**注意**：74MB 的《中国居民膳食指南2022》解析较慢，单文件失败会被 try/except 隔离。

---

### seed_knowledge_base.py

**功能**：知识库种子数据导入，包含权威来源的摘要 chunk 和专家媒体摘要。

**特点**：
- 使用 monkey-patch 绕过 ChromaDB 1.5.x HttpClient 的 auth 检查
- 为每份指南生成摘要 chunk（`doc_type=guide_catalog`）
- 写入专家媒体摘要（`source_type=expert_media`）

**用法**：
```bash
python scripts/ingest/seed_knowledge_base.py
```

---

### update_guide_metadata.py

**功能**：为已入库的指南文档批量添加年龄组和地区标签。

**特点**：
- 使用字符串存储（逗号分隔），支持 ChromaDB 的 where 过滤
- 内置 `GUIDE_METADATA` 映射表，按文件名模糊匹配
- 覆盖 8 份主体指南的年龄组、地区、疾病标签

**用法**：
```bash
python scripts/ingest/update_guide_metadata.py
```

---

## etl/ 数据清洗脚本

数据质量修复脚本，在入库前/后对 PostgreSQL 中的数据进行清洗。

### clean_nutrition_data.py

**功能**：营养成分数据清洗，处理缺失值、标准化食物名称、补充默认值。

**流程**：
1. 扫描 `nutrition_facts` 表中的空字段
2. 按食物名称匹配补全缺失数据
3. 标准化食物名称（去除括号、统一量词）
4. 补充默认值（如膳食纤维、钠等）

**用法**：
```bash
python scripts/etl/clean_nutrition_data.py
```

---

### fix_abnormal_data.py

**功能**：修正异常的营养成分数据。

**检查项**：
- 能量/蛋白质/脂肪/碳水是否满足 `能量 ≈ 蛋白质×4 + 脂肪×9 + 碳水×4`
- 营养素值是否在合理范围
- 单位是否正确（g / mg / μg）

**用法**：
```bash
python scripts/etl/fix_abnormal_data.py
```

---

### fill_missing_calories.py

**功能**：补充缺失热量的食物记录。

**策略**：
1. 扫描 `calories` 字段为 NULL 的记录
2. 按 `蛋白质×4 + 脂肪×9 + 碳水×4` 计算理论热量
3. 标记无法计算的记录（缺少三大营养素）

**用法**：
```bash
python scripts/etl/fill_missing_calories.py
```

---

## verify/ 验证脚本

数据验证和质量检查脚本。

### verify_ingestion.py

**功能**：验证双写入库的一致性，检查 ChromaDB 和 PostgreSQL 中的分块数量是否匹配。

**检查项**：
- `document_chunks` 表总数 vs ChromaDB collection 总数
- 按 `source_type`（guideline/new_food/gi_data/expert_media）分类统计
- PostgreSQL 关键词检索可用性测试

**用法**：
```bash
python scripts/verify/verify_ingestion.py
```

**输出示例**：
```
PostgreSQL document_chunks 总数: 915
  - guideline: 746 条
  - new_food: 152 条
  - gi_data: 17 条
  - expert_media: 0 条

关键词检索测试:
  [0] score=0.200 | source=guideline | guideline=儿童青少年肥胖食养指南
  ...
```

---

### check_kb_guides.py

**功能**：检查知识库中指南文档的完整性和元数据质量。

**检查项**：
- ChromaDB 中指南 chunk 数量
- 元数据完整性（guideline_title, age_group, disease_tags 等）
- 按指南标题统计 chunk 分布

**用法**：
```bash
python scripts/verify/check_kb_guides.py
```

---

### final_nutrition_report.py

**功能**：生成营养成分表数据质量最终报告。

**报告内容**：
- 数据总量与分类统计
- 字段缺失率分析
- 异常值检测结果
- 单位标准化覆盖率
- 质量评分

**用法**：
```bash
python scripts/verify/final_nutrition_report.py
```

---

## utils/ 工具脚本

辅助运维的小工具。

### clear_cache.py

**功能**：清空所有 HealthAI 相关的 Redis 缓存。

**清理范围**：
- `healthai:*` 前缀的所有缓存键
- 打印清理前的缓存数量和示例 key

**用法**：
```bash
python scripts/utils/clear_cache.py
```

---

### _clear_cache.py

**功能**：清空 RAG 相关的 Redis 缓存。

**清理范围**：
- `rag:query:*` — RAG 查询结果缓存
- `rag:embed:*` — 嵌入向量缓存
- `rag:food:*` — 食物检索缓存
- `rag:tdee:*` — TDEE 计算缓存
- `rag:plan:*` — 饮食计划缓存
- `rag:safety:*` — 安全护栏判断缓存

**用法**：
```bash
python scripts/utils/_clear_cache.py
```

---

## 依赖关系图

```
import_gi.py ──────▶ glycemic_index_facts 表
                          │
                          ▼
                    unified_reindex.py ──▶ ChromaDB (authoritative_sources)
                                        └──▶ PostgreSQL (document_chunks)
                                               │
                                               ▼
verify_ingestion.py ◀─────────────────────────┘

import_nutrition.py ──▶ nutrition_facts 表
        │
        ▼
clean_nutrition_data.py ──▶ 数据清洗
fix_abnormal_data.py ─────▶ 异常修正
fill_missing_calories.py ▶ 热量补充
        │
        ▼
final_nutrition_report.py ──▶ 质量报告

seed_knowledge_base.py ──▶ ChromaDB (guide_catalog chunks)
update_guide_metadata.py ──▶ ChromaDB (元数据批量更新)
reindex_guidelines.py ──▶ ChromaDB (指南索引重建)

clear_cache.py ──▶ Redis (缓存清理)
_clear_cache.py ──▶ Redis (RAG 缓存清理)
```

---

## 常见问题

**Q: 入库失败，提示 ChromaDB 连接超时？**
A: 确保 ChromaDB 服务已启动：`docker compose up -d chroma`。

**Q: 如何重新入库特定类型的数据？**
A: 使用 `--target` 参数，如 `python scripts/ingest/unified_reindex.py --target gi_data`。

**Q: 数据入库后查询结果没有更新？**
A: 清理 Redis 缓存：`python scripts/utils/clear_cache.py`。

**Q: 大文件（74MB 指南）解析失败？**
A: 已采用流式分块 + 单文件隔离策略，其他文件不受影响。可单独运行 `reindex_guidelines.py` 重试。