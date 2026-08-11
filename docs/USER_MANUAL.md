# 用户手册

> 面向终端用户与运维人员的产品使用指南。

## 1. 产品概述

HealthAI-Platform 是一个基于 RAG（检索增强生成）的智能健康饮食助手，提供：

- **食物营养查询**：精确查询食物的热量、蛋白质、脂肪、碳水等营养素
- **饮食指南咨询**：基于权威指南文档（如《成人肥胖食养指南》）的语义问答
- **个性化饮食计划**：根据用户档案生成科学的饮食计划
- **多轮对话**：支持上下文延续，模拟真实咨询场景
- **安全护栏**：自动拦截违规医疗咨询，引导用户就医

## 2. 快速开始

### 2.1 访问入口

| 入口 | URL | 适用场景 |
|------|-----|---------|
| API 文档（Swagger） | http://localhost:8000/docs | 开发者联调 |
| Streamlit 演示 | http://localhost:8501 | 业务演示 |
| 健康检查 | http://localhost:8000/api/health | 运维监控 |

### 2.2 注册与登录

```bash
# 1. 注册新用户
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "alice",
    "email": "alice@example.com",
    "password": "StrongPwd@2026"
  }'

# 2. 登录获取 JWT token
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "alice",
    "password": "StrongPwd@2026"
  }'

# 响应示例
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

### 2.3 发起首次 RAG 查询

```bash
# 查询食物热量（food_search 意图）
curl -X POST http://localhost:8000/api/v1/rag/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "一碗米饭有多少热量"}'

# 响应示例
{
  "answer": "一碗米饭（约 150g）含有约 200 大卡热量，碳水约 45g...",
  "sources": [{"doc_id": "food_001", "name": "米饭"}],
  "intent": "food_search",
  "success": true
}
```

## 3. 核心功能使用

### 3.1 三种查询意图

系统会自动识别查询意图并路由到对应检索路径：

| 意图 | 触发关键词示例 | 检索路径 |
|------|---------------|---------|
| `food_search` | "XX有多少热量"、"XX的营养" | SQL 精确查询食物成分表 |
| `guideline_query` | "XX病的饮食原则"、"XX指南" | 向量检索权威指南文档 |
| `hybrid` | 模糊查询，可能两者都需要 | SQL + 向量并行检索 + RRF 融合 |
| `plan_generation` | "帮我生成饮食计划" | 跳过检索，直接调用计划生成器 |

### 3.2 多轮对话

通过 `conversation_context` 字段传递历史对话，实现上下文延续：

```python
import httpx

# 第 1 轮：用户告知病情
r1 = httpx.post(
    "http://localhost:8000/api/v1/rag/query",
    headers={"Authorization": f"Bearer {token}"},
    json={"query": "我刚确诊糖尿病"},
)
answer1 = r1.json()["answer"]

# 第 2 轮：带上第 1 轮上下文
r2 = httpx.post(
    "http://localhost:8000/api/v1/rag/query",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "query": "那我能吃苹果吗？",
        "conversation_context": [
            {"role": "user", "content": "我刚确诊糖尿病"},
            {"role": "assistant", "content": answer1},
        ],
    },
)
```

### 3.3 流式响应

RAG 查询支持两种流式模式：

#### 节点级流式（`/rag/query/stream`）

逐步返回 LangGraph 各节点的执行结果，适用于实时展示处理过程：

```bash
curl -N -X POST http://localhost:8000/api/v1/rag/query/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "糖尿病饮食原则", "stream": true}'

# 输出示例（SSE 格式）
data: {"node": "intent_recognizer", "output": {"intent": "guideline_query"}}

data: {"node": "query_rewriter", "output": {"rewritten_query": "..."}}

data: {"node": "vector_retriever", "output": {"vector_results": [...]}}

data: {"node": "generator", "output": {"answer": "糖尿病饮食应控制..."}}

data: [DONE]
```

#### Token 级流式（`/rag/chat`）

检索完成后逐 token 流式生成答案，实现打字机效果：

```bash
curl -N -X POST http://localhost:8000/api/v1/rag/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "糖尿病饮食原则"}'

# 输出
data: {"type": "sources", "data": [{"doc_id": "guide_001"}]}

data: {"type": "content", "data": "糖尿病"}

data: {"type": "content", "data": "饮食"}

data: {"type": "content", "data": "应控制..."}

data: {"type": "done", "data": {"answer": "...", "sources": [...]}}
```

### 3.4 追问节点（ClarificationNode）

当用户查询过于模糊，向量检索相似度低于阈值时，系统会返回追问而非强行猜测：

```json
// 用户查询：那个东西能不能吃
// 系统响应：
{
  "answer": "请问您具体想了解哪种食物？份量是多少？",
  "intent": "guideline_query",
  "needs_clarification": true,
  "clarification_question": "请问您具体想了解哪种食物？份量是多少？"
}
```

用户补充具体信息后再次查询即可获得正常回答。

### 3.5 安全护栏

系统会自动拦截以下违规请求：

| 类别 | 触发示例 | 响应行为 |
|------|---------|---------|
| `prescription_drug` | "帮我开点降糖药" | 返回拒绝话术，引导就医 |
| `diagnosis_request` | "我是不是得了糖尿病" | 返回拒绝话术，引导就医 |
| `extreme_diet` | "21天辟谷减肥" | 返回拒绝话术，提供科学减重建议 |
| `treatment_plan` | "帮我制定治疗方案" | 返回拒绝话术，引导专科就诊 |
| `special_population_intervention` | "孕妇能吃什么药" | 返回拒绝话术，建议咨询专科医生 |

安全拦截后 `intent` 字段为 `"blocked"`，前端可据此展示特殊样式。

### 3.6 食物安全校验

针对用户档案（过敏原/疾病/药物）的食物安全检查：

```python
from src.utils.food_validator import validate_food_for_user

result = validate_food_for_user(
    foods=["牛奶", "葡萄柚"],
    user_profile={
        "allergens": ["milk"],         # 牛奶过敏
        "diseases": ["hypertension"],   # 高血压
        "medications": ["statins"],     # 服用他汀类
    },
)

# result["is_safe"] = False
# result["allergen_risks"] = [{"allergen": "milk", "food": "牛奶", ...}]
# result["drug_interaction_risks"] = [{"medication": "statins", "food": "葡萄柚", ...}]
```

### 3.7 饮食计划生成

```bash
curl -X POST http://localhost:8000/api/v1/plans/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "u123",
    "goal": "fat_loss",
    "daily_calories_target": 1800,
    "constraints": {
      "allergens": ["milk"],
      "diseases": ["diabetes"]
    }
  }'
```

## 4. 错误处理

### 4.1 常见错误码

| HTTP 状态码 | 错误类型 | 原因与解决 |
|------------|---------|-----------|
| 400 | 参数校验失败 | 检查请求体字段与类型 |
| 401 | 未认证 | 在 Header 中加入有效 JWT |
| 403 | 权限不足 | 用户角色无权访问该资源 |
| 404 | 资源不存在 | 检查 URL 路径与 ID |
| 422 | 查询语义无效 | RAG 查询为空或超过 2000 字符 |
| 429 | 请求过频 | 触发限流，等待 60 秒后重试 |
| 500 | 服务异常 | 查看服务端日志，联系运维 |

### 4.2 限流机制

- 登录接口：每 IP 每分钟 5 次（防暴力破解）
- RAG 查询：每用户每分钟 30 次
- 全局限流：每 IP 每秒 10 次（Nginx 层）

触发限流后返回 429，响应头包含：
```
X-RateLimit-Limit: 30
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 60
```

## 5. 最佳实践

### 5.1 查询写法建议

✅ **推荐**：清晰具体
- "100克鸡胸肉有多少蛋白质"
- "糖尿病人的饮食原则是什么"
- "减脂期三餐应该怎么搭配"

❌ **不推荐**：模糊笼统（会触发追问）
- "那个东西"
- "怎么吃"
- "健康建议"

### 5.2 多轮对话上下文管理

- 仅保留最近 5-10 轮对话，避免 token 超限
- 每轮 user/assistant 内容不超过 500 字
- 长对话建议分段重启，避免上下文污染

### 5.3 错误重试策略

- 网络超时：等待 2 秒后重试，最多 3 次
- 429 限流：按 `X-RateLimit-Reset` 等待
- 5xx 错误：指数退避重试（2s → 4s → 8s）

## 6. 常见问题（FAQ）

**Q1：为什么我的查询返回了追问而不是答案？**
A：查询过于模糊，向量检索相似度低于 0.75。请补充具体食物种类、份量或场景后重试。

**Q2：为什么有时回答中没有引用来源？**
A：当查询被安全护栏拦截或触发追问时不会返回来源。正常回答必定包含 `sources` 字段。

**Q3：饮食计划生成失败怎么办？**
A：检查用户档案是否完整（身高/体重/目标），确保约束条件不冲突（如过敏原排除所有蛋白质来源）。

**Q4：如何关闭安全护栏进行测试？**
A：在 `config/safety_rules.yaml` 中设置 `global_enabled: false`（仅测试环境）。修改后下次调用自动生效，无需重启。

**Q5：如何更新安全规则？**
A：直接编辑 `config/safety_rules.yaml`，保存后自动热加载。也可通过管理 API 强制重载：

```bash
curl -X POST http://localhost:8000/api/v1/admin/safety-rules/reload \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```
