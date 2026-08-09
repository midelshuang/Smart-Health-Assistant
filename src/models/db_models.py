"""
用于建立类到数据库表的映射 (ORM)
Version: 2.1 (Fixed Relationship Deadlocks)
        V4.0 (添加 V4 预留字段：AI 生成元信息 + 个性化推荐上下文)
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Float, ForeignKey, Date, Numeric, func, Boolean, BigInteger, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID, TSVECTOR
from sqlalchemy.orm import relationship, declarative_base
from pydantic import BaseModel
from typing import Optional
import uuid

# 创建 SQLAlchemy 的 ORM 基类
Base = declarative_base()

# 定义登录响应体模型
class Token(BaseModel):
    access_token: str
    token_type: str
    expires_in: int # 单位：秒

# 定义饮食记录请求体模型
class DietLogCreate(BaseModel):
    nutrition_id: int      # ✅ 必选，用户选中的食物ID
    quantity_grams: float
    meal_type: Optional[str] = "snack"


# 1. 权威信源表
class AuthoritativeSource(Base):
    __tablename__ = 'authoritative_sources'
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False, comment="信源标题")
    source_type = Column(String(20), comment="信源类型")
    media_type = Column(String(10), comment="媒体类型")
    summary = Column(Text, comment="信源摘要")
    transcript = Column(Text, comment="全文文本或转写")
    file_url = Column(String(500), comment="原始文件路径")
    publish_date = Column(Date, comment="发布日期")
    source_version = Column(String(50), comment="版本号")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    # ===== V4 预留字段：信源元数据增强 =====
    credibility_score = Column(Numeric(3, 2), comment="[V4] 信源可信度评分(0.00-1.00)")
    reviewed_by = Column(String(64), comment="[V4] 审核人标识")
    reviewed_at = Column(DateTime(timezone=True), comment="[V4] 审核时间")


# 9. 用户身份主表 (对应数据库 users)
class User(Base):
    __tablename__ = 'users'

    # 注意：数据库里是 SERIAL，对应 Integer
    id = Column(Integer, primary_key=True, autoincrement=True)

    # 业务唯一标识 (对应 external_id)
    external_id = Column(String(64), unique=True, nullable=False, index=True, comment="外部唯一标识")

    # 来源
    source = Column(String(20), nullable=False, default='wechat', comment="来源渠道")

    # 状态
    is_active = Column(Boolean, default=True, comment="是否激活")

    # 注册时间
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="注册时间")

    # ✅ 关键修复：补全这两个字段，否则注册必挂
    username = Column(String(64), unique=True, nullable=True, comment="用户名")
    password_hash = Column(String(255), nullable=True, comment="密码哈希")

    # ===== 邮箱注册 =====
    email = Column(String(128), unique=True, nullable=True, index=True, comment="邮箱")
    email_verified = Column(Boolean, default=False, comment="邮箱是否已验证")
    last_login_at = Column(DateTime(timezone=True), comment="最后登录时间")
    last_login_ip = Column(String(64), comment="最后登录 IP")

    # ===== 安全问题（用于忘记密码重置） =====
    security_question = Column(String(255), nullable=True, comment="安全问题")
    security_answer_hash = Column(String(255), nullable=True, comment="安全问题答案哈希")

    # ===== RBAC 角色 =====
    role = Column(String(20), default='user', nullable=False, comment="角色: user/admin/expert")

    # ===== V4 预留字段：会员体系 + 多端登录 =====
    membership_tier = Column(String(20), default='free', comment="[V4] 会员等级: free/plus/pro")
    membership_expires_at = Column(DateTime(timezone=True), comment="[V4] 会员到期时间")
    last_active_at = Column(DateTime(timezone=True), comment="[V4] 最后活跃时间（用于留存分析）")
    device_info = Column(JSONB, comment="[V4] 最近登录设备信息")

    # 关联关系
    profile = relationship("UserProfile", back_populates="user", uselist=False)


# 2. 用户档案表 (对应数据库 user_profiles)
class UserProfile(Base):
    __tablename__ = 'user_profiles'

    # ✅ 关键修复：必须加上 ForeignKey，并且类型要和数据库一致 (VARCHAR(50))
    user_id = Column(String(50), ForeignKey('users.external_id'), primary_key=True, comment="关联用户ID")

    nickname = Column(String(100), comment="昵称")
    health_tags = Column(JSONB, comment="健康标签")
    allergies = Column(Text, comment="过敏史")
    age = Column(Integer, comment="年龄")
    gender = Column(String(10), comment="性别")
    height = Column(Numeric, comment="身高")
    weight = Column(Numeric, comment="体重")
    bmi = Column(Numeric, comment="BMI")
    health_goal = Column(String(100), comment="健康目标")
    province = Column(String(50), comment="省份")
    body_fat_percentage = Column(Numeric, comment="体脂率")
    sports_hobby = Column(Text, comment="运动爱好")
    last_login = Column(DateTime(timezone=True), comment="最后登录")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间")

    # ===== V4 预留字段：深度个性化推荐上下文 =====
    dietary_preference = Column(JSONB, comment="[V4] 饮食偏好(素食/低盐/低糖/清真等)")
    metabolic_data = Column(JSONB, comment="[V4] 代谢数据(基础代谢率/活动系数等动态数据)")
    medical_conditions = Column(JSONB, comment="[V4] 慢性病记录(糖尿病/高血压/高血脂等)")
    target_heart_rate = Column(Integer, comment="[V4] 目标心率区间")
    recommended_daily_water = Column(Integer, comment="[V4] 推荐每日饮水量(ml)")
    profile_version = Column(Integer, default=1, comment="[V4] 档案版本号(用于触发推荐重算)")
    feedback_preferences = Column(JSONB, comment="[V5] 反馈偏好(点赞/踩/使用统计与风格偏好)")

    # ✅ 核心修复：补全所有反向关系 (One-to-Many)
    # 这样 DailyDietLog 里的 back_populates="diet_logs" 才能找到这里
    user = relationship("User", back_populates="profile")
    diet_logs = relationship("DailyDietLog", back_populates="user_profile")
    chat_histories = relationship("ChatHistory", back_populates="user_profile")
    fitness_goals = relationship("FitnessGoal", back_populates="user_profile")
    plans = relationship("Plan", back_populates="user_profile")


# 3. 每日饮食记录表 (新增核心表)
class DailyDietLog(Base):
    __tablename__ = 'daily_diet_logs'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(50), ForeignKey('user_profiles.user_id'), nullable=False, comment="用户ID")
    log_date = Column(Date, nullable=False, server_default=func.current_date(), comment="记录日期")
    meal_type = Column(String(20), nullable=False, comment="餐次(早餐/午餐/晚餐/加餐)")
    nutrition_id = Column(Integer, ForeignKey('nutrition_facts.id'), comment="关联营养表ID")
    quantity_grams = Column(Numeric(8, 2), nullable=False, comment="重量(克)")
    calculated_calories = Column(Numeric(8, 2), comment="计算热量(kcal)")
    ai_parsed = Column(Boolean, default=False, comment="是否AI解析")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")

    # ===== V4 预留字段：精细化营养追踪 + AI 解析溯源 =====
    actual_macros = Column(JSONB, comment="[V4] 实际宏量营养素(蛋白/脂肪/碳水/纤维)")
    meal_photo_url = Column(String(500), comment="[V4] 餐食照片URL(图像识别用)")
    confidence_score = Column(Numeric(3, 2), comment="[V4] AI解析置信度(0.00-1.00)")
    parse_model_version = Column(String(20), comment="[V4] 解析使用的模型版本")

    # ✅ 关联关系
    user_profile = relationship("UserProfile", back_populates="diet_logs")
    nutrition_info = relationship("NutritionFact", backref="logs")


# 4. 聊天记录表
class ChatHistory(Base):
    __tablename__ = 'chat_histories'
    id = Column(Integer, primary_key=True, autoincrement=True)
    message_uuid = Column(String(64), unique=True, nullable=False, comment="消息UUID")
    session_id = Column(String(64), nullable=False, index=True, comment="会话ID")
    user_id = Column(String(50), ForeignKey('user_profiles.user_id'), nullable=False, comment="用户ID")
    role = Column(String(10), nullable=False, comment="角色")
    message_content = Column(Text, nullable=False, comment="消息内容")
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), comment="消息时间")
    model_version = Column(String(20), comment="模型版本")
    chat_metadata = Column(JSONB, nullable=True)

    # ===== V4 预留字段：对话质量分析 + 多模态 =====
    tokens_used = Column(Integer, comment="[V4] 消耗的token数")
    cost_usd = Column(Numeric(10, 6), comment="[V4] 单次调用成本(USD)")
    response_time_ms = Column(Integer, comment="[V4] 响应耗时(毫秒)")
    user_feedback_score = Column(Integer, comment="[V4] 用户反馈评分(1-5)")
    attachments = Column(JSONB, comment="[V4] 附件信息(图片/语音等)")

    # ✅ 关联关系
    user_profile = relationship("UserProfile", back_populates="chat_histories")


# 5. 用户反馈表 (V5扩展: 支持回答/计划反馈 + 幂等性)
class UserFeedback(Base):
    __tablename__ = 'user_feedbacks'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), ForeignKey('user_profiles.user_id'), nullable=False, index=True, comment="用户ID")
    target_type = Column(String(20), nullable=False, index=True, comment="目标类型: answer / plan")
    target_id = Column(String(128), nullable=False, index=True, comment="目标ID(answer的message_uuid或plan的id)")
    feedback_action = Column(String(20), nullable=False, comment="反馈动作: like / dislike / use_plan")
    rating = Column(Integer, comment="评分(1-5, 可选)")
    comment = Column(Text, comment="评论内容")
    category = Column(String(50), comment="反馈分类(事实错误/风格不佳/不安全/其他)")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间")
    used_for_training = Column(Boolean, default=False, comment="是否已用于模型微调")

    # 联合唯一约束: 同一用户对同一目标只能有一条反馈(防刷分核心)
    __table_args__ = (UniqueConstraint('user_id', 'target_type', 'target_id', name='uq_user_target_feedback'),)


# 6. 营养成分表
class NutritionFact(Base):
    __tablename__ = 'nutrition_facts'
    id = Column(Integer, primary_key=True, autoincrement=True)
    food_name = Column(String(100), nullable=False, comment="食物名称")
    aliases = Column(String(255), comment="别名")
    category = Column(String(50), comment="分类")
    calories_per_100g = Column(Numeric(8, 2), nullable=False, comment="每100g热量")
    protein = Column(Numeric(8, 2), nullable=False, comment="蛋白质(g)")
    fat = Column(Numeric(8, 2), nullable=False, comment="脂肪(g)")
    carbs = Column(Numeric(8, 2), nullable=False, comment="碳水(g)")
    details = Column(JSONB, comment="微量元素详情")
    data_source = Column(String(100), comment="数据来源")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")

    # ===== V4 预留字段：食物画像增强 + 多版本数据 =====
    food_image_url = Column(String(500), comment="[V4] 标准食物图片URL")
    glycemic_load = Column(Numeric(5, 2), comment="[V4] 血糖负荷(GL)")
    satiety_index = Column(Numeric(5, 2), comment="[V4] 饱腹感指数")
    region_tags = Column(JSONB, comment="[V4] 地域标签(华北/华南/西部等)")
    verified = Column(Boolean, default=False, comment="[V4] 营养师审核标记")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间")


# 7. 升糖指数表 (补全)
class GlycemicIndex(Base):
    __tablename__ = 'glycemic_index_facts'
    id = Column(Integer, primary_key=True, autoincrement=True)
    food_name = Column(String(255), nullable=False, comment="食物名称")
    gi_value = Column(Numeric(5, 2), nullable=False, comment="GI值")
    food_group = Column(String(100), comment="食物大类")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间")

    # ===== V4 预留字段：GI 精细化 =====
    gi_category = Column(String(20), comment="[V4] GI分类(low/medium/high)")
    glucose_response = Column(JSONB, comment="[V4] 血糖响应曲线数据")


# 8. 健身目标表 (新增核心表)
class FitnessGoal(Base):
    __tablename__ = 'fitness_goals'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(50), ForeignKey('user_profiles.user_id'), nullable=False, comment="用户ID")
    goal_type = Column(String(50), nullable=False, comment="目标类型")
    target_value = Column(Numeric(8, 2), comment="目标值")
    start_value = Column(Numeric(8, 2), comment="初始值")
    current_value = Column(Numeric(8, 2), comment="当前值")
    target_date = Column(Date, comment="目标日期")
    status = Column(String(20), default='active', comment="状态")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间")

    # ===== V4 预留字段：智能调整建议 =====
    progress_history = Column(JSONB, comment="[V4] 进度历史快照")
    ai_adjustment_suggestion = Column(Text, comment="[V4] AI调整建议")
    completion_rate = Column(Numeric(5, 2), comment="[V4] 完成率百分比")

    # ✅ 关联关系
    user_profile = relationship("UserProfile", back_populates="fitness_goals")


# 10. 个性化计划表 (新增核心表)
class Plan(Base):
    __tablename__ = 'plans'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(50), ForeignKey('user_profiles.user_id'), nullable=False, index=True, comment="用户ID")
    goal_type = Column(String(50), nullable=False, comment="目标类型: weight_loss/muscle_gain/health_maintenance")
    config = Column(JSONB, default={}, comment="计划详细配置(JSONB)")
    start_date = Column(Date, nullable=False, comment="生效开始日期")
    end_date = Column(Date, comment="生效结束日期")
    status = Column(String(20), default='active', comment="当前状态")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")

    # ===== V4 预留字段：计划版本管理 + 动态调整 =====
    plan_version = Column(Integer, default=1, comment="[V4] 计划版本号(每次调整+1)")
    parent_plan_id = Column(BigInteger, ForeignKey('plans.id'), nullable=True, comment="[V4] 父计划ID(版本链)")
    adjustment_history = Column(JSONB, comment="[V4] 动态调整历史记录")
    ai_generated = Column(Boolean, default=False, comment="[V4] 是否AI生成")
    generation_prompt = Column(Text, comment="[V4] 生成时的Prompt(用于复现)")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间")

    # ✅ 关联关系
    user_profile = relationship("UserProfile", back_populates="plans")
    checkins = relationship("PlanCheckIn", back_populates="plan")
    parent_plan = relationship("Plan", remote_side=[id], backref="child_plans")


# 12. 文档分块表（RAG 双写存储：ChromaDB向量 + PostgreSQL全文）
class DocumentChunk(Base):
    __tablename__ = 'document_chunks'
    id = Column(Integer, primary_key=True, autoincrement=True)
    chunk_id = Column(String(128), unique=True, nullable=False, index=True, comment="分块唯一ID（与ChromaDB对应）")
    source_type = Column(String(32), nullable=False, index=True, comment="来源类型: guideline/new_food/expert_media")
    file_name = Column(String(500), comment="原始文件名")
    file_path = Column(String(1000), comment="原始文件路径")
    chunk_index = Column(Integer, comment="在原文件中的序号")
    content = Column(Text, nullable=False, comment="分块文本内容（用于BM25关键词检索）")
    content_summary = Column(Text, comment="分块摘要（用于快速展示）")
    token_count = Column(Integer, comment="Token数量")
    char_count = Column(Integer, comment="字符数量")
    guideline_title = Column(String(500), index=True, comment="指南标题")
    audience = Column(String(200), comment="受众（逗号分隔）")
    disease_tags = Column(String(500), comment="疾病标签（逗号分隔）")
    region = Column(String(50), comment="地区")
    age_group = Column(String(200), comment="年龄组（逗号分隔）")
    doc_type = Column(String(50), comment="文档类型标签")
    expert_name = Column(String(100), comment="专家名称")
    expert_category = Column(String(200), comment="专家分类")
    metadata_json = Column(JSONB, comment="扩展元数据（JSON）")
    # BM25 倒排索引列（由触发器自动维护，应用层不直接写入）
    content_tsvector = Column(TSVECTOR, comment="[BM25] 全文检索倒排向量（PostgreSQL tsvector）")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="入库时间")

    __table_args__ = (
        Index('ix_document_chunks_source_type_guideline_title',
              'source_type', 'guideline_title'),
        # BM25 GIN 倒排索引：大幅加速 @@ 查询
        Index('ix_document_chunks_content_tsv',
              'content_tsvector', postgresql_using='gin'),
    )


# 11. 计划打卡记录表 (新增核心表)
class PlanCheckIn(Base):
    __tablename__ = 'plan_checkins'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    plan_id = Column(BigInteger, ForeignKey('plans.id'), nullable=False, index=True, comment="关联计划ID")
    checkin_date = Column(Date, nullable=False, comment="打卡日期")
    status = Column(String(20), default='pending', comment="当日完成状态")
    actual_calories = Column(Numeric(8, 2), comment="当日实际摄入热量")
    notes = Column(Text, comment="打卡备注")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="打卡时间")

    # ===== V4 预留字段：精细化执行追踪 =====
    actual_macros = Column(JSONB, comment="[V4] 当日实际宏量营养素")
    mood_score = Column(Integer, comment="[V4] 心情评分(1-5)")
    energy_level = Column(Integer, comment="[V4] 精力评分(1-5)")
    sleep_hours = Column(Numeric(4, 2), comment="[V4] 昨晚睡眠时长")
    weight_snapshot = Column(Numeric(5, 2), comment="[V4] 打卡时体重快照")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间")

    # 联合唯一约束：同一天同一计划只能打卡一次
    __table_args__ = (UniqueConstraint('plan_id', 'checkin_date', name='uq_plan_checkin_date'),)

    # ✅ 关联关系
    plan = relationship("Plan", back_populates="checkins")