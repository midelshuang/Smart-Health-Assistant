# 专家媒体视频清洗入库报告

## 📊 数据概览

- **媒体类型**: 专家科普视频（MP4 格式）
- **源目录**: `raw_data/raw/academic/`
- **入库目标**: ChromaDB `authoritative_sources` 集合
- **语音转写**: Whisper 模型（`src/core/loaders/audio_loader.py`）
- **总文件数**: 67 个视频
- **总容量**: 约 516.2 MB

## 📁 文件统计

### 按专家分类

| 专家/专辑 | 视频数 | 文件大小 | 目录 |
|----------|--------|---------|------|
| 医学减重专家陈伟（抖音） | 62 | 467.6 MB | `academic/医学减重专家陈伟（抖音）/` |
| 陈伟21天减8斤 | 5 | 48.6 MB | `academic/陈伟21天减8斤/` |
| **合计** | **67** | **516.2 MB** | — |

### 文件命名规律

抖音视频使用以下命名格式：
- 自动下载：`抖音202683-{视频ID}.mp4`（共 64 个）
- 手动命名：`陈伟-如何正确测量体重.mp4`、`陈伟-水果如何选择.mp4`（共 2 个）

## 🔍 向量化状态

### 当前入库情况

| 指标 | 数值 |
|------|------|
| ChromaDB 中 expert_media 块数 | 6 块 |
| 已入库视频文件数 | 0 个（6 块来自 seed 文本摘要） |
| 源视频文件总数 | 67 个 |
| **实际覆盖率** | **0.0%** |

### Seed 摘要数据（非视频转写）

ChromaDB 中现存的 6 块 expert_media 数据来自 `scripts/seed_knowledge_base.py` 中的预置文本摘要，**并非**视频转写结果：

| 标题 | 块数 | 来源 |
|------|------|------|
| 减重饮食的常见误区与科学方法 | - | Seed 文本 |
| 江南地区（南京）四季饮食养生指南 | - | Seed 文本 |
| 膳食纤维与健康 | - | Seed 文本 |
| 蛋白质的吸收与利用 | - | Seed 文本 |
| 运动营养补充指南 | - | Seed 文本 |

## 🔧 视频处理流水线

### 处理流程

```
视频文件 (.mp4)
    │
    ▼
[1] ffmpeg 提取音轨 → 临时 WAV 文件
    │
    ▼
[2] Whisper 语音转写 → 完整文字稿
    │
    ▼
[3] 文本清洗（去口头禅、断句）
    │
    ▼
[4] 语义分块（~500 字符/块）
    │
    ▼
[5] Embedding 向量化 → ChromaDB 存储
```

### 关键代码模块

**音频加载器**: `src/core/loaders/audio_loader.py`
- `AudioLoader.load(file_path)`: 视频 → 音轨 → 文字稿
- `AudioLoader.load_and_chunk(file_path, metadata)`: 视频 → 分块文本
- 支持格式: MP3/WAV/M4A/FLAC/MP4/AVI/MOV/MKV
- Whisper 模型: 可配置 tiny/base/small/medium/large
- 设备: 自动检测 CUDA，无 GPU 回退 CPU

**文档处理器**: `src/services/document_processor.py`
- `DocumentProcessor.process_file(file_path, source_type="expert_media")`
- 自动路由音频/视频到 `AudioLoader`
- 注入专家媒体元数据

### 元数据结构

```python
{
    "source_type": "expert_media",
    "file_name": "抖音202683-011967.mp4",
    "file_path": "raw_data/raw/academic/医学减重专家陈伟（抖音）/抖音202683-011967.mp4",
    "media_type": "video",
    "expert_name": "陈伟",
    "expert_category": "医学减重专家陈伟（抖音）",
    "doc_type": "expert_media_transcript",
    "chunk_index": 0,
}
```

## ⚠️ 问题诊断

### 核心问题：视频完全未入库

**现状**: 67 个视频文件（516 MB）均未被转写和向量化。

**原因分析**:

1. **入库脚本不覆盖视频**: `scripts/reindex_guidelines.py` 仅处理 `guidelines/` 目录下的 PDF，未涉及 `academic/` 目录
2. **转写成本高**: Whisper 在 CPU 上转写 1 分钟视频约需 5-10 分钟，67 个视频总计可能需要数小时
3. **存储消耗大**: 向量化后预计产生 2000-5000 个文本块，显著增加 ChromaDB 体积
4. **命名不规范**: 大部分视频使用自动 ID 命名（如 `抖音202683-011967.mp4`），缺乏语义信息，影响检索体验

### 次要问题

1. **视频内容同质化**: 抖音短视频多为 1-3 分钟的减重科普，内容碎片化
2. **缺乏主题分类**: 未建立视频主题标签（如"碳水摄入"、"热量计算"、"饮食误区"）
3. **字幕质量**: Whisper 对中文专业术语（如"基础代谢率"）识别可能不准确

## 🚀 修复方案

### 方案：批量视频转写入库

**前置条件**:
- 安装 ffmpeg（用于提取音轨）
- 下载 Whisper 模型（首次运行自动下载，约 1.5GB for small 模型）
- 预计耗时: CPU ~8-12 小时 / GPU ~1-2 小时

**步骤 1: 环境准备**

```bash
# 检查 ffmpeg
ffmpeg -version

# 安装 Whisper 依赖
pip install openai-whisper
```

**步骤 2: 编写批量入库脚本**

基于 `reindex_guidelines.py` 的模式，新增 `reindex_expert_media.py`：

```python
# scripts/reindex_expert_media.py 核心逻辑
def main():
    academic_dir = os.path.join(settings.Authoritative_file_path, "academic")
    embedding_fn = get_embedding_function()
    vs = VectorStore()
    vs.connect()
    
    processor = DocumentProcessor()
    total_chunks = 0
    
    for category in os.listdir(academic_dir):
        cat_dir = os.path.join(academic_dir, category)
        if not os.path.isdir(cat_dir):
            continue
        
        for fn in sorted(os.listdir(cat_dir)):
            fp = os.path.join(cat_dir, fn)
            if not os.path.isfile(fp):
                continue
            
            try:
                chunks = processor.process_file(
                    fp,
                    source_type="expert_media",
                    extra_metadata={
                        "expert_name": "陈伟",
                        "expert_category": category,
                    }
                )
                if chunks:
                    # 写入 ChromaDB
                    fh = hashlib.md5(fp.encode()).hexdigest()[:8]
                    ids = [f"expert_{fh}_{i}" for i in range(len(chunks))]
                    vs.add_documents(
                        ids=ids,
                        documents=[c["content"] for c in chunks],
                        metadatas=[c["metadata"] for c in chunks],
                        embedding_function=embedding_fn,
                    )
                    total_chunks += len(chunks)
                    print(f"✅ {fn} → {len(chunks)} chunks")
                else:
                    print(f"⏭️ {fn} → 无内容（可能为纯图片/转写失败）")
            except Exception as e:
                print(f"❌ {fn} 失败: {e}")
    
    print(f"入库完成：共 {total_chunks} 个文本块")
```

**步骤 3: 增量入库（推荐先试一个）**

```bash
# 先测试一个视频的转写效果
python -c "
from src.core.loaders.audio_loader import AudioLoader
loader = AudioLoader()
text = loader.load('raw_data/raw/academic/医学减重专家陈伟（抖音）/陈伟-如何正确测量体重.mp4')
print(f'转写长度: {len(text)} 字符')
print(f'前500字: {text[:500]}')
"
```

### 预期效果

| 指标 | 预期值 |
|------|--------|
| 总视频块数 | 2000-5000 块 |
| 平均每视频块数 | 30-75 块 |
| 转写准确率 | 85-92%（中文普通话题） |
| 检索覆盖率 | 显著提升（减重/饮食/运动相关查询） |

## 📋 后续优化建议

### 1. 视频预处理

- **重命名**: 将 `抖音202683-XXXXXX.mp4` 改为语义化名称（如"陈伟-减脂期如何吃主食.mp4"）
- **添加字幕**: 使用 Whisper 预生成字幕文件，后续复用
- **去重**: 检测内容重复的视频，避免冗余入库

### 2. 质量提升

- **GPU 加速**: 使用 CUDA 设备可将转写速度提升 10 倍以上
- **术语校正**: 建立健康/营养领域术语词典，提升转写准确率
- **人工校对**: 对关键视频的转写文本进行人工审核

### 3. 检索优化

- **主题标签**: 为每个视频自动生成主题标签（如"碳水"、"蛋白质"、"减脂"）
- **章节分割**: 长视频按主题自动分割为多段，每段独立索引
- **专家关联**: 建立视频与专家的关联图谱，支持"某专家怎么说"类查询

### 4. 存储管理

- **定期重建**: 每季度重新转写入库，确保模型升级后质量提升
- **缓存策略**: 转写文本缓存到本地，避免重复转写
- **容量规划**: 预计向量化后 ChromaDB 增长 2-5 MB

## 📊 总结

| 维度 | 现状 | 目标 |
|------|------|------|
| 视频文件 | 67 个（516 MB） | 67 个 |
| 已转写入库 | 0 个（0%） | 67 个（100%） |
| 文本块数 | 6 块（seed 摘要） | 2000-5000 块 |
| 检索价值 | 仅限 5 条摘要 | 丰富的减重/饮食/运动知识库 |

专家媒体视频是 RAG 系统中补充"真实案例"和"口语化表达"的重要数据源。当前完全未入库，建议尽快启动转写入库工作，可先在 GPU 环境下批量处理。

---

生成时间: 2026-08-06
检测脚本: `scripts/inspect_vectorization.py`
音频加载: `src/core/loaders/audio_loader.py`
文档处理: `src/services/document_processor.py`
