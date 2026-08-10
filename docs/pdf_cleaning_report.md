# PDF 文档清洗入库报告

## 📊 数据概览

- **文档类型**: 指南类 PDF + 新食品 PDF
- **源目录**: `raw_data/raw/guidelines/` + `raw_data/raw/new_foods/`
- **入库目标**: ChromaDB `authoritative_sources` 集合
- **Embedding 模型**: ZhipuAI embedding-2 (1024 维)

## 📁 文件统计

### 指南类 PDF（guidelines）

共 **15** 个 PDF 文件，涵盖 **8** 部食养指南：

| 指南名称 | 版本 | 文件数 | 状态 |
|---------|------|-------|------|
| 中国居民膳食指南 | 2022 | 1 | ❌ 未入库 |
| 儿童青少年肥胖食养指南 | 2024版 | 2 | ✅ 已入库 |
| 成人慢性肾脏病食养指南 | 2024版 | 2 | ✅ 已入库 |
| 成人肥胖食养指南 | 2024版 | 2 | ✅ 已入库 |
| 成人高尿酸血症与痛风食养指南 | - | 2 | ✅ 已入库 |
| 成人脑卒中食养指南 | 2026版 | 2 | ✅ 已入库 |
| 成人骨质疏松症食养指南 | 2026版 | 2 | ✅ 已入库 |
| 成人肌少症食养指南 | 2026版 | 2 | ✅ 已入库 |

### 新食品 PDF（new_foods）

共 **15** 个 PDF 文件，涵盖食品添加剂新品种、新食品原料等：

| 文件 | 类型 | 状态 |
|------|------|------|
| 1.栀子油等5种新食品原料.pdf | 新食品原料 | ❌ 未入库 |
| 2.D-阿洛酮糖-3-差向异构酶等11种食品添加剂新品种.pdf | 食品添加剂 | ❌ 未入库 |
| 3.硫酸钙等6种食品相关产品新品种.pdf | 食品相关产品 | ❌ 未入库 |
| C.I.颜料白21等3种食品相关产品新品种.pdf | 食品相关产品 | ❌ 未入库 |
| D-阿洛酮糖等5种新食品原料.pdf | 新食品原料 | ❌ 未入库 |
| 乙烯醇均聚物等2种食品相关产品新品种.pdf | 食品相关产品 | ❌ 未入库 |
| 分支酶等8种食品添加剂新品种.pdf | 食品添加剂 | ❌ 未入库 |
| 威尼斯镰刀菌蛋白等3种新食品原料.pdf | 新食品原料 | ❌ 未入库 |
| 月桂酸铵等6种食品相关产品新品种.pdf | 食品相关产品 | ❌ 未入库 |
| 木聚糖酶等7种食品添加剂新品种.pdf | 食品添加剂 | ❌ 未入库 |
| 氨基肽酶等9种食品添加剂新品种.pdf | 食品添加剂 | ❌ 未入库 |
| 牡丹籽油等7种新食品原料.pdf | 新食品原料 | ❌ 未入库 |
| 1741760235904_75508.pdf | 待确认 | ❌ 未入库 |
| 1741760235962_63754.pdf | 待确认 | ❌ 未入库 |
| 1741760236048_98505.pdf | 待确认 | ❌ 未入库 |

## 🔍 向量化状态

### ChromaDB 统计

| 指标 | 数值 |
|------|------|
| 总文档块数 | 825 |
| 指南 (guideline) | 819 块 |
| 专家媒体 (expert_media) | 6 块 |
| 新食品 (new_food) | 0 块 |

### 指南标题块数分布

| 指南标题 | 块数 |
|---------|------|
| 成人脑卒中食养指南（2026年版） | 148 |
| 成人骨质疏松症食养指南（2026年版） | 133 |
| 成人肌少症食养指南（2026年版） | 125 |
| 成人慢性肾脏病食养指南（2024年版） | 123 |
| 成人肥胖食养指南（2024年版） | 120 |
| 成人高尿酸血症与痛风食养指南 | 104 |
| 儿童青少年肥胖食养指南（2024年版） | 65 |
| 中国居民膳食指南（2022） | 1 |

### 文本块质量

| 指标 | 数值 |
|------|------|
| 平均块大小 | 492 字符 |
| 最小块 | 66 字符 |
| 最大块 | 501 字符 |
| 总文本量 | 405,991 字符 |

## 🔧 清洗与入库流程

### 1. PDF 解析 (`src/core/loaders/pdf_loader.py`)

- 使用 `pdfplumber` 提取文本
- 自动识别章节结构（标题、正文、列表）
- 表格数据保留原始格式
- 处理扫描件和图片型 PDF（OCR 降级）

### 2. 文本清洗 (`src/utils/text_cleaner.py`)

- 去除页眉页脚、页码
- 合并跨行断句
- 清理多余空白和特殊字符
- 统一标点符号

### 3. 文本分块

- 分块策略: 按段落语义分块，结合 token 长度限制
- 目标块大小: ~500 字符（与 embedding 模型适配）
- 重叠窗口: 保证上下文连贯

### 4. 元数据注入 (`src/services/document_processor.py`)

**指南类元数据**：
```python
{
    "source_type": "guideline",
    "file_name": "成人肥胖食养指南（2024年版）.pdf",
    "guideline_title": "成人肥胖食养指南（2024年版）",
    "audience": "成人",
    "disease_tags": "肥胖",
    "region": "全国",
    "age_group": "adult",
    "doc_type": "guideline_content",
}
```

**新食品元数据**：
```python
{
    "source_type": "new_food",
    "file_name": "牡丹籽油等7种新食品原料.pdf",
    "doc_type": "new_food_content",
}
```

### 5. 向量化入库

- Embedding 模型: ZhipuAI embedding-2
- 向量维度: 1024
- 存储: ChromaDB (`authoritative_sources` 集合)
- 写入方式: 批量写入，自动生成唯一 ID

## 📈 覆盖率分析

| 类型 | 源文件 | 已入库 | 覆盖率 | 状态 |
|------|--------|--------|--------|------|
| 指南 PDF | 15 | 14 | 93.3% | ⚠️ 1 个未入库 |
| 新食品 PDF | 15 | 0 | **0.0%** | ❌ 全部未入库 |
| **合计** | **30** | **14** | **46.7%** | — |

## ⚠️ 问题诊断

### 1. 中国居民膳食指南（2022）入库失败

- **文件**: `中国居民膳食指南（2022） (中国营养学会) (Z-Library).pdf`
- **原因**: 文件体积过大（~74MB），解析超时或内存不足
- **现状**: ChromaDB 中仅有 1 块（可能为上次部分入库残留）
- **建议**: 分批处理或使用 OCR 方案

### 2. 新食品 PDF 完全未入库

- **原因**: 入库脚本 `reindex_guidelines.py` 仅处理 guidelines 目录，未覆盖 new_foods 目录
- **建议**: 需要编写新食品专用入库脚本
- **数据价值**: 新食品原料是饮食计划中食品安全提醒的重要依据

## 🚀 修复方案

### 方案 A：补入中国居民膳食指南

```bash
# 使用单文件处理模式，增加超时时间
python -c "
from src.services.document_processor import DocumentProcessor
from src.repositories.vector_store import VectorStore
from src.core.embedding import get_embedding_function
import hashlib

processor = DocumentProcessor()
vs = VectorStore()
vs.connect()
vs.get_or_create_collection(get_embedding_function())

fp = 'raw_data/raw/guidelines/中国居民膳食指南（2022） (中国营养学会) (Z-Library).pdf'
chunks = processor.process_file(fp, source_type='guideline')
fh = hashlib.md5(fp.encode()).hexdigest()[:8]
ids = [f'guide_{fh}_{i}' for i in range(len(chunks))]
vs.add_documents(
    ids=ids,
    documents=[c['content'] for c in chunks],
    metadatas=[c['metadata'] for c in chunks],
    embedding_function=get_embedding_function(),
)
print(f'成功入库 {len(chunks)} 个文本块')
"
```

### 方案 B：新食品批量入库

```bash
# 复用 guidelines 入库逻辑，切换 source_type
python scripts/reindex_guidelines.py --source-type new_food
```

需修改 `reindex_guidelines.py` 增加对 `new_foods` 目录的支持。

## 📋 后续优化建议

1. **新食品入库**: 编写 `reindex_new_foods.py`，批量入库 15 个新食品 PDF
2. **大文件处理**: 中国居民膳食指南（~74MB）采用流式解析或拆分入库
3. **增量更新**: 建立文件 MD5 校验机制，只入库新增/变更文件
4. **质量校验**: 入库后通过向量检索抽样验证内容质量
5. **缓存清理**: 入库完成后清空 Redis `rag:*` 缓存，避免旧结果干扰

## ✅ 结论

指南类 PDF 入库情况良好（93.3%），仅《中国居民膳食指南（2022）》因文件过大未完成入库。新食品 PDF 完全未入库（0%），需补充入库流程。整体 PDF 文档覆盖率为 46.7%，建议优先完成新食品入库和大文件处理。

---

生成时间: 2026-08-06
检测脚本: `scripts/inspect_vectorization.py`
入库工具: `scripts/reindex_guidelines.py`
文档处理: `src/services/document_processor.py`
