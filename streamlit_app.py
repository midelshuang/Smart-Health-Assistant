"""
HealthAI Platform - Streamlit 演示应用

启动方式：
    streamlit run streamlit_app.py --server.port 8501 --server.address 0.0.0.0

功能模块：
    1. 仪表盘（系统状态）
    2. 用户认证（登录/注册）
    3. TDEE 计算器
    4. AI 健康问答（RAG）
    5. 饮食记录（Function Calling）
    6. 监控指标（Prometheus）
"""
import os
import sys
import json
import requests
from typing import Optional, Dict, Any

import streamlit as st

# ============================================================
# 配置
# ============================================================
# API 服务地址：可通过环境变量覆盖，默认与 API 同源（端口 8000）
API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")

# 会话状态初始化
if "token" not in st.session_state:
    st.session_state.token = st.session_state.get("healthai_token", "")
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []


# ============================================================
# API 调用工具
# ============================================================
def api_call(
    method: str,
    path: str,
    body: Optional[Dict] = None,
    require_auth: bool = True,
) -> Dict[str, Any]:
    """调用后端 API"""
    headers = {"Content-Type": "application/json"}
    if require_auth and st.session_state.token:
        headers["Authorization"] = f"Bearer {st.session_state.token}"

    try:
        if method.upper() == "GET":
            resp = requests.get(f"{API_BASE}{path}", headers=headers, timeout=30)
        else:
            resp = requests.request(
                method, f"{API_BASE}{path}", headers=headers,
                json=body, timeout=60,
            )
        try:
            data = resp.json()
        except Exception:
            data = resp.text
        return {"ok": resp.ok, "status": resp.status_code, "data": data}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "status": 0, "data": "无法连接到 API 服务，请检查后端是否启动"}
    except Exception as e:
        return {"ok": False, "status": -1, "data": str(e)}


# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="HealthAI Platform",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# 侧边栏
# ============================================================
with st.sidebar:
    st.title("🏥 HealthAI Platform")
    st.caption("v1.4.0 · Streamlit 演示版")

    st.markdown("---")
    st.markdown("### ⚙️ 配置")
    api_base_input = st.text_input("API 服务地址", value=API_BASE, key="api_base")
    if api_base_input != API_BASE:
        API_BASE = api_base_input

    st.markdown("---")
    st.markdown("### 🔑 认证状态")
    if st.session_state.token:
        st.success("✅ 已登录")
        if st.button("退出登录"):
            st.session_state.token = ""
            st.rerun()
    else:
        st.warning("⚠️ 未登录")

    st.markdown("---")
    st.markdown("### 📖 启动方式")
    st.markdown("""
    1. **API 模式**：
       ```bash
       python run.py api
       ```
    2. **HTML 前端模式**：
       ```bash
       python run.py web
       ```
    3. **Streamlit 模式**：
       ```bash
       python run.py streamlit
       ```
    """)

# ============================================================
# 主内容区
# ============================================================
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 仪表盘",
    "🔐 用户认证",
    "⚖️ TDEE 计算",
    "💬 AI 问答",
    "🍎 饮食记录",
    "📈 监控指标",
])

# ------------------------------------------------------------
# Tab 1：仪表盘
# ------------------------------------------------------------
with tab1:
    st.header("📊 系统仪表盘")

    col1, col2, col3, col4 = st.columns(4)

    # 检查 API 健康
    api_health = api_call("GET", "/api/health", require_auth=False)
    if api_health["ok"]:
        col1.metric("API 状态", "✅ 在线")
    else:
        col1.metric("API 状态", "❌ 离线")

    v1_health = api_call("GET", "/api/v1/health", require_auth=False)
    col2.metric("V1 版本", "✅ 正常" if v1_health["ok"] else "❌ 异常")

    v2_health = api_call("GET", "/api/v2/health", require_auth=False)
    col3.metric("V2 版本", "🚧 草稿" if v2_health["ok"] else "❌ 异常")

    col4.metric("Token", "🔑 已登录" if st.session_state.token else "⚠️ 未登录")

    st.markdown("---")
    if st.button("🔄 刷新状态"):
        st.rerun()

    if st.button("📋 查看 API 版本信息"):
        versions = api_call("GET", "/api/versions", require_auth=False)
        if versions["ok"]:
            st.json(versions["data"])
        else:
            st.error(f"获取失败: {versions['data']}")

    st.markdown("---")
    st.subheader("🔧 系统组件")
    st.markdown(f"""
    | 组件 | 说明 |
    |---|---|
    | API 服务 | {API_BASE} |
    | Swagger 文档 | [打开]({API_BASE}/docs) |
    | Prometheus 监控 | [查看]({API_BASE}/metrics) |
    | HTML 前端 | [访问]({API_BASE}/) |
    """)


# ------------------------------------------------------------
# Tab 2：用户认证
# ------------------------------------------------------------
with tab2:
    st.header("🔐 用户认证")

    auth_mode = st.radio(
        "选择操作",
        ["🔑 登录", "📝 用户名注册", "📧 邮箱注册", "🔒 修改密码", "❓ 忘记密码"],
        horizontal=True,
    )

    # 持久化 refresh token
    if "refresh_token" not in st.session_state:
        st.session_state.refresh_token = ""

    # ---------- 登录 ----------
    if auth_mode.startswith("🔑"):
        with st.form("login_form"):
            username = st.text_input("用户名或邮箱", value="demo_user")
            password = st.text_input("密码", value="Demo1234", type="password")
            submitted = st.form_submit_button("🔑 登录")
        if submitted:
            r = api_call("POST", "/api/v1/auth/login",
                         {"username": username, "password": password}, require_auth=False)
            if r["ok"] and isinstance(r["data"], dict) and r["data"].get("access_token"):
                st.session_state.token = r["data"]["access_token"]
                if r["data"].get("refresh_token"):
                    st.session_state.refresh_token = r["data"]["refresh_token"]
                st.success("登录成功！Token 已保存")
            else:
                st.error(f"登录失败: {r['data']}")
            st.json(r["data"])

        if st.session_state.refresh_token:
            st.caption("💡 拥有 Refresh Token，可在此刷新：")
            if st.button("🔄 刷新 Token"):
                r = api_call("POST", "/api/v1/auth/refresh",
                             {"refresh_token": st.session_state.refresh_token},
                             require_auth=False)
                if r["ok"] and r["data"].get("access_token"):
                    st.session_state.token = r["data"]["access_token"]
                    if r["data"].get("refresh_token"):
                        st.session_state.refresh_token = r["data"]["refresh_token"]
                    st.success("Token 已刷新")
                else:
                    st.error(f"刷新失败: {r['data']}")
                st.json(r["data"])

    # ---------- 用户名注册 ----------
    elif auth_mode.startswith("📝"):
        with st.form("register_form"):
            username = st.text_input("用户名", placeholder="3-32 位字母/数字/下划线")
            password = st.text_input("密码", type="password", placeholder="至少 8 位字母+数字")
            question = st.text_input("安全问题（可选）", placeholder="如：您的宠物名？")
            answer = st.text_input("安全问题答案（可选）")
            submitted = st.form_submit_button("📝 注册")
        if submitted:
            body = {"username": username, "password": password}
            if question and answer:
                body["security_question"] = question
                body["security_answer"] = answer
            r = api_call("POST", "/api/v1/auth/register", body, require_auth=False)
            if r["ok"]:
                st.success("注册成功！现在可以登录了")
            else:
                st.error(f"注册失败: {r['data']}")
            st.json(r["data"])

    # ---------- 邮箱注册 ----------
    elif auth_mode.startswith("📧"):
        with st.form("email_reg_form"):
            email = st.text_input("邮箱", placeholder="you@example.com")
            password = st.text_input("密码", type="password", placeholder="至少 8 位字母+数字")
            col_c1, col_c2 = st.columns([3, 1])
            with col_c1:
                code = st.text_input("验证码", placeholder="6 位数字")
            with col_c2:
                st.write("")  # 占位对齐
            send_clicked = st.form_submit_button("📧 发送验证码")
            submit_clicked = st.form_submit_button("📧 完成注册")
        if send_clicked and email:
            r = api_call("POST", "/api/v1/auth/send-code", {"email": email}, require_auth=False)
            if r["ok"]:
                st.success("验证码已发送，请查收邮箱")
            else:
                st.error(f"发送失败: {r['data']}")
            st.json(r["data"])
        if submit_clicked:
            body = {"email": email, "password": password, "code": code}
            r = api_call("POST", "/api/v1/auth/register-with-email", body, require_auth=False)
            if r["ok"]:
                st.success("注册成功！现在可以登录了")
            else:
                st.error(f"注册失败: {r['data']}")
            st.json(r["data"])

    # ---------- 修改密码 ----------
    elif auth_mode.startswith("🔒"):
        if not st.session_state.token:
            st.warning("⚠️ 修改密码需要先登录")
        with st.form("change_pwd_form"):
            old_pwd = st.text_input("旧密码", type="password")
            new_pwd = st.text_input("新密码", type="password", placeholder="至少 8 位字母+数字")
            submitted = st.form_submit_button("🔒 确认修改")
        if submitted and st.session_state.token:
            r = api_call("POST", "/api/v1/auth/change-password",
                         {"old_password": old_pwd, "new_password": new_pwd},
                         require_auth=True)
            if r["ok"]:
                st.success("密码已修改，请重新登录")
            else:
                st.error(f"修改失败: {r['data']}")
            st.json(r["data"])

    # ---------- 忘记密码 ----------
    elif auth_mode.startswith("❓"):
        with st.form("forgot_form"):
            username = st.text_input("用户名或邮箱")
            col_q1, col_q2 = st.columns(2)
            with col_q1:
                question = st.text_input("安全问题", disabled=True,
                                         placeholder="点击下方按钮查询")
            with col_q2:
                answer = st.text_input("答案")
            new_pwd = st.text_input("新密码", type="password", placeholder="至少 8 位字母+数字")
            col_f1, col_f2 = st.columns(2)
            with col_f1:
                query_clicked = st.form_submit_button("🔎 查询安全问题")
            with col_f2:
                reset_clicked = st.form_submit_button("❓ 重置密码")
        if query_clicked and username:
            r = api_call("POST", "/api/v1/auth/security-question",
                         {"username": username}, require_auth=False)
            if r["ok"] and r["data"].get("security_question"):
                st.info(f"安全问题：{r['data']['security_question']}")
            else:
                st.error(f"查询失败: {r['data']}")
            st.json(r["data"])
        if reset_clicked:
            r = api_call("POST", "/api/v1/auth/reset-password",
                         {"username": username, "security_answer": answer,
                          "new_password": new_pwd}, require_auth=False)
            if r["ok"]:
                st.success("密码已重置，请使用新密码登录")
            else:
                st.error(f"重置失败: {r['data']}")
            st.json(r["data"])

    st.markdown("---")
    if st.session_state.token:
        st.subheader("当前 Token")
        st.code(st.session_state.token[:80] + "...", language="text")
        if st.button("🚪 退出登录"):
            st.session_state.token = ""
            st.session_state.refresh_token = ""
            st.rerun()


# ------------------------------------------------------------
# Tab 3：TDEE 计算
# ------------------------------------------------------------
with tab3:
    st.header("⚖️ TDEE 计算器")
    st.caption("计算基础代谢率(BMR)与每日总能量消耗(TDEE)")

    col1, col2 = st.columns(2)
    with col1:
        weight = st.number_input("体重 (kg)", min_value=20.0, max_value=300.0, value=70.0, step=0.1)
        height = st.number_input("身高 (cm)", min_value=100.0, max_value=250.0, value=175.0, step=0.1)
        age = st.number_input("年龄", min_value=1, max_value=150, value=30)
    with col2:
        gender = st.selectbox("性别", ["male", "female"], format_func=lambda x: "男" if x == "male" else "女")
        activity_level = st.selectbox(
            "活动等级",
            ["sedentary", "light", "moderate", "active", "very_active"],
            format_func=lambda x: {
                "sedentary": "久坐",
                "light": "轻度活动",
                "moderate": "中度活动",
                "active": "高度活动",
                "very_active": "非常活跃",
            }[x],
            index=2,
        )
        goal_type = st.selectbox(
            "目标",
            ["weight_loss", "maintenance", "muscle_gain"],
            format_func=lambda x: {
                "weight_loss": "减脂",
                "maintenance": "维持",
                "muscle_gain": "增肌",
            }[x],
        )

    if st.button("🧮 计算 TDEE", type="primary"):
        if not st.session_state.token:
            st.error("请先在'用户认证'页登录获取 Token")
        else:
            with st.spinner("计算中..."):
                r = api_call("POST", "/api/v1/users/tdee", {
                    "weight_kg": weight,
                    "height_cm": height,
                    "age": age,
                    "gender": gender,
                    "activity_level": activity_level,
                    "goal_type": goal_type,
                })
            if r["ok"]:
                data = r["data"]
                if isinstance(data, dict):
                    col1, col2, col3 = st.columns(3)
                    col1.metric("BMR (基础代谢)", f"{data.get('bmr', 0):.0f} kcal")
                    col2.metric("TDEE (总消耗)", f"{data.get('tdee', 0):.0f} kcal")
                    col3.metric("目标热量", f"{data.get('target_calories', 0):.0f} kcal")

                    macros = data.get("macros", {})
                    st.markdown("### 🥩 宏量营养素分配")
                    m1, m2, m3 = st.columns(3)
                    m1.metric("蛋白质", f"{macros.get('protein_g', 0):.1f} g")
                    m2.metric("脂肪", f"{macros.get('fat_g', 0):.1f} g")
                    m3.metric("碳水化合物", f"{macros.get('carbs_g', 0):.1f} g")

                    if data.get("safety_warning"):
                        st.warning(f"⚠️ {data['safety_warning']}")

                    with st.expander("📋 查看完整结果"):
                        st.json(data)
                else:
                    st.json(data)
            else:
                st.error(f"计算失败: {r['data']}")


# ------------------------------------------------------------
# Tab 4：AI 健康问答
# ------------------------------------------------------------
with tab4:
    st.header("💬 AI 健康问答")
    st.caption("基于 RAG 的健康知识问答")

    # 显示对话历史
    chat_container = st.container()
    with chat_container:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # 输入框
    user_query = st.chat_input("输入你的健康问题...")

    if user_query:
        if not st.session_state.token:
            st.session_state.chat_history.append({
                "role": "assistant",
                "content": "⚠️ 请先在'用户认证'页登录获取 Token",
            })
            st.rerun()
        else:
            st.session_state.chat_history.append({"role": "user", "content": user_query})
            with st.spinner("AI 正在思考..."):
                r = api_call("POST", "/api/v1/rag/query", {"query": user_query})
            if r["ok"] and isinstance(r["data"], dict):
                answer = r["data"].get("answer", "(无答案)")
                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": answer,
                })
            else:
                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": f"❌ 查询失败: {r['data']}",
                })
            st.rerun()

    # 快捷问题
    st.markdown("---")
    st.markdown("💡 **快捷问题**")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("鸡蛋蛋白质"):
            st.session_state.pending_query = "鸡蛋的蛋白质含量是多少？"
    with col2:
        if st.button("糖尿病饮食"):
            st.session_state.pending_query = "糖尿病患者能吃水果吗？"
    with col3:
        if st.button("减肥三餐"):
            st.session_state.pending_query = "减肥时如何安排一日三餐？"

    if "pending_query" in st.session_state:
        # 通过 chat_input 的方式触发：将问题填入输入框
        st.info(f"已选择：{st.session_state.pending_query}（请按回车发送）")

    if st.button("🗑️ 清空对话"):
        st.session_state.chat_history = []
        st.rerun()


# ------------------------------------------------------------
# Tab 5：饮食记录
# ------------------------------------------------------------
with tab5:
    st.header("🍎 饮食记录")
    st.caption("使用自然语言记录饮食，AI 自动解析食物与营养")

    diet_text = st.text_area(
        "饮食描述（自然语言）",
        value="今天早餐吃了两个鸡蛋、一杯牛奶和一片面包",
        height=100,
    )

    if st.button("🤖 AI 解析", type="primary"):
        if not st.session_state.token:
            st.error("请先登录获取 Token")
        else:
            with st.spinner("AI 解析中..."):
                r = api_call("POST", "/api/v1/diet/parse", {"text": diet_text})
            if r["ok"]:
                st.success("解析成功！")
                st.json(r["data"])
            else:
                st.error(f"解析失败: {r['data']}")

    st.markdown("---")
    st.subheader("🔍 食物搜索")
    food_keyword = st.text_input("食物名称", value="鸡蛋")
    if st.button("搜索"):
        r = api_call(
            "GET",
            f"/api/v1/diet/search?keyword={food_keyword}&limit=5",
            require_auth=False,
        )
        if r["ok"]:
            st.json(r["data"])
        else:
            st.error(f"搜索失败: {r['data']}")


# ------------------------------------------------------------
# Tab 6：监控指标
# ------------------------------------------------------------
with tab6:
    st.header("📈 Prometheus 监控指标")

    col1, col2 = st.columns([1, 2])
    with col1:
        if st.button("🔄 拉取指标"):
            st.session_state.metrics_loaded = True
        auto_refresh = st.checkbox("自动刷新（5秒）")

    if auto_refresh:
        import time
        time.sleep(5)
        st.session_state.metrics_loaded = True
        st.rerun()

    if st.session_state.get("metrics_loaded"):
        with st.spinner("拉取中..."):
            try:
                resp = requests.get(f"{API_BASE}/metrics", timeout=10)
                if resp.ok:
                    metrics_text = resp.text

                    # 解析关键指标
                    def extract_metric(pattern, text, group=1, default="N/A"):
                        import re
                        m = re.search(pattern, text)
                        return m.group(group) if m else default

                    # 关键指标卡片
                    m1, m2, m3, m4 = st.columns(4)

                    # HTTP 请求总数
                    import re
                    http_matches = re.findall(r"http_requests_total\{[^}]*\}\s+(\d+)", metrics_text)
                    total_reqs = sum(int(v) for v in http_matches)
                    m1.metric("HTTP 请求总数", total_reqs)

                    # 在途请求
                    in_flight = extract_metric(r"http_requests_in_progress\s+(\d+)", metrics_text, default="0")
                    m2.metric("在途请求", in_flight)

                    # Redis 状态
                    redis_avail = extract_metric(r"redis_available\s+(\d+)", metrics_text, default="0")
                    m3.metric("Redis 状态", "✅ 可用" if redis_avail == "1" else "❌ 不可用")

                    # 缓存命中率
                    cache_hit = extract_metric(
                        r'cache_hit_rate\{cache_type="query"\}\s+([\d.]+)',
                        metrics_text, default="0",
                    )
                    m4.metric("缓存命中率", f"{float(cache_hit) * 100:.1f}%")

                    st.markdown("---")
                    st.subheader("📜 原始指标（Prometheus 格式）")
                    st.code(metrics_text, language="text")
                else:
                    st.error(f"拉取失败: HTTP {resp.status_code}")
            except Exception as e:
                st.error(f"拉取失败: {e}")
    else:
        st.info("点击'拉取指标'按钮查看监控数据")
