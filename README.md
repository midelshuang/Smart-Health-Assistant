# HealthAI-Platform 🏥

一个基于 **LangGraph 状态机** 和 **RAG（检索增强生成）** 技术的智能健康饮食助手。
支持结构化数据查询（如食物热量）与非结构化知识检索（如《成人肥胖食养指南》），旨在为用户提供专业、可溯源的健康建议。

## ✨ 核心特性
- 🧠 **LangGraph 状态机编排**：告别简单的线性对话，实现意图识别、工具调用、RAG检索的复杂流转。
- 🔍 **混合检索架构**：结合 SQL（精确查询食物成分）与 ChromaDB 向量检索（语义匹配食养指南）。
- 🛡️ **企业级工程规范**：严格遵循配置与代码分离，具备完善的日志、异常处理及 Docker 容器化部署能力。
- 📊 **数据溯源**：大模型回复自动附带引用来源，拒绝 AI 幻觉。

## 🛠️ 技术栈
- **后端服务**: FastAPI + Uvicorn
- **AI 编排**: LangGraph + LangChain
- **数据存储**: PostgreSQL (关系型) + ChromaDB (向量型)
- **前端演示**: Streamlit

## 🚀 快速开始

### 1. 克隆项目并创建虚拟环境
```bash
git clone https://github.com/midelshuang/Smart-Health-Assistant.git
cd HealthAI-Platform
python -m venv venv
source venv/bin/activate  # Windows 使用 `venv\Scripts\activate`