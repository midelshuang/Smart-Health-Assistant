-- 作用是一键初始化你的数据库结构
-- 创建五张核心数据表：
-- 1. authoritative_sources：权威信源层（指南、文献、专家媒体）
-- 2. nutrition_facts：结构化数据层（营养成分表）
-- 3. user_profiles：用户画像层（用户档案与特殊需求）
-- 4. chat_histories：交互记录层（历史对话，配合Redis使用）
-- 5. user_feedbacks：反馈评价层（用户评价与纠偏，配合ChromaDB使用）
-- 设置主键与约束，并为高频查询字段创建索引。

-- ==========================================================
-- 第1步：编写 CREATE TABLE authoritative_sources 语句
-- 定义权威信源层的字段（支持文本、PDF、视频等多种媒体格式）
-- ==========================================================
CREATE TABLE IF NOT EXISTS authoritative_sources (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    source_type VARCHAR(20) NOT NULL,         -- 来源类型：guideline(指南), literature(文献), expert_media(专家媒体)
    media_type VARCHAR(10) DEFAULT 'text',    -- 媒体格式：text, pdf, video, audio
    summary TEXT,                             -- 内容摘要或预处理后的文本块索引
    transcript TEXT,                          -- 【核心】视频的字幕/文稿（用于AI检索）
    file_url VARCHAR(500),                    -- 原始文件地址（PDF链接或视频链接）
    publish_date DATE,                        -- 发布/出版日期
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE authoritative_sources IS '权威信源层：统一管理指南、文献和专家经验';
COMMENT ON COLUMN authoritative_sources.id IS '信源唯一标识';
COMMENT ON COLUMN authoritative_sources.title IS '文章/视频标题';
COMMENT ON COLUMN authoritative_sources.source_type IS '来源类型（guideline/literature/expert_media）';
COMMENT ON COLUMN authoritative_sources.media_type IS '媒体格式（text/pdf/video/audio）';
COMMENT ON COLUMN authoritative_sources.summary IS '内容摘要';
COMMENT ON COLUMN authoritative_sources.transcript IS '视频的字幕/文稿（用于AI向量化检索）';
COMMENT ON COLUMN authoritative_sources.file_url IS '原始文件存储地址';
COMMENT ON COLUMN authoritative_sources.publish_date IS '发布/出版日期';
COMMENT ON COLUMN authoritative_sources.created_at IS '记录创建时间';

-- 为 source_type 创建索引，方便以后按来源类型筛选数据
CREATE INDEX idx_authoritative_sources_type ON authoritative_sources(source_type);


-- ==========================================================
-- 第2步：编写 CREATE TABLE nutrition_facts 语句
-- 定义结构化数据层的字段（精准计算餐食营养信息）
-- ==========================================================
CREATE TABLE IF NOT EXISTS nutrition_facts (
    id SERIAL PRIMARY KEY,
    food_name VARCHAR(100) NOT NULL,
    aliases VARCHAR(255) DEFAULT NULL,
    category VARCHAR(50) DEFAULT NULL,
    calories_per_100g DECIMAL(8,2) NOT NULL,
    protein DECIMAL(8,2) NOT NULL,
    fat DECIMAL(8,2) NOT NULL,
    carbs DECIMAL(8,2) NOT NULL,
    details JSONB DEFAULT '{}',               -- 扩展字段：存维生素、矿物质等不规则微量元素
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE nutrition_facts IS '结构化数据层：精准计算餐食营养信息';
COMMENT ON COLUMN nutrition_facts.id IS '食物唯一标识';
COMMENT ON COLUMN nutrition_facts.food_name IS '标准食物名称';
COMMENT ON COLUMN nutrition_facts.aliases IS '食物别名';
COMMENT ON COLUMN nutrition_facts.category IS '食物分类';
COMMENT ON COLUMN nutrition_facts.calories_per_100g IS '每100g热量（大卡/kcal）';
COMMENT ON COLUMN nutrition_facts.protein IS '每100g蛋白质（克）';
COMMENT ON COLUMN nutrition_facts.fat IS '每100g脂肪（克）';
COMMENT ON COLUMN nutrition_facts.carbs IS '每100g碳水化合物（克）';
COMMENT ON COLUMN nutrition_facts.details IS '其他微量元素详情（JSONB格式）';
COMMENT ON COLUMN nutrition_facts.created_at IS '记录创建时间';

-- 为 food_name 创建索引，满足“创建索引”的进阶要求，提升精准查询效率
CREATE INDEX idx_nutrition_facts_food_name ON nutrition_facts(food_name);


-- ==========================================================
-- 第3步：编写 CREATE TABLE user_profiles 语句
-- 定义用户画像层的字段（AI个性化推荐的基础）
-- ==========================================================
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id VARCHAR(50) PRIMARY KEY,          -- 用户ID作为主键
    nickname VARCHAR(100), -- 用户昵称
    health_tags JSONB DEFAULT '[]',           -- 健康标签，如 ["高血压", "减脂期"]
    allergies TEXT,                           -- 过敏原信息
    age INT,
    height DOUBLE PRECISION,
    weight DOUBLE PRECISION,
    gender VARCHAR(10),
    sports_hobby TEXT, -- 运动爱好
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE user_profiles IS '用户画像层：存储用户基本信息与特殊健康状况';
COMMENT ON COLUMN user_profiles.user_id IS '用户唯一标识';
COMMENT ON COLUMN user_profiles.nickname IS '用户昵称';
COMMENT ON COLUMN user_profiles.health_tags IS '健康标签（JSONB格式）';
COMMENT ON COLUMN user_profiles.age IS '年龄';
COMMENT ON COLUMN user_profiles.height IS '身高（米）';
COMMENT ON COLUMN user_profiles.weight IS '体重（千克）';
COMMENT ON COLUMN user_profiles.gender IS '性别';
COMMENT ON COLUMN user_profiles.sports_hobby IS '运动爱好';
COMMENT ON COLUMN user_profiles.allergies IS '过敏原信息';
COMMENT ON COLUMN user_profiles.updated_at IS '档案最后更新时间';


-- ==========================================================
-- 第4步：编写 CREATE TABLE chat_histories 语句
-- 定义交互记录层的字段（配合Redis使用，做冷备份）
-- ==========================================================
CREATE TABLE IF NOT EXISTS chat_histories (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,          -- 会话ID
    user_id VARCHAR(50),
    role VARCHAR(10) NOT NULL,                -- 角色：user 或 assistant
    message_content TEXT NOT NULL,            -- 对话内容
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE chat_histories IS '交互记录层：历史对话冷备份';
COMMENT ON COLUMN chat_histories.id IS '消息唯一标识';
COMMENT ON COLUMN chat_histories.session_id IS '会话ID';
COMMENT ON COLUMN chat_histories.user_id IS '用户标识';
COMMENT ON COLUMN chat_histories.role IS '角色（user/assistant）';
COMMENT ON COLUMN chat_histories.message_content IS '对话内容';
COMMENT ON COLUMN chat_histories.timestamp IS '消息发送时间';

-- 为 session_id 创建索引，方便按会话拉取历史记录
CREATE INDEX idx_chat_histories_session_id ON chat_histories(session_id);


-- ==========================================================
-- 第5步：编写 CREATE TABLE user_feedbacks 语句
-- 定义反馈评价层的字段（配合ChromaDB使用，闭环与优化）
-- ==========================================================
CREATE TABLE IF NOT EXISTS user_feedbacks (
    id SERIAL PRIMARY KEY,
    message_id VARCHAR(64),                   -- 关联的消息ID
    rating SMALLINT DEFAULT NULL,             -- 评分：1为踩, 5为赞
    comment TEXT,                             -- 用户具体评价内容
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE user_feedbacks IS '反馈评价层：收集用户满意度与改进建议';
COMMENT ON COLUMN user_feedbacks.id IS '反馈唯一标识';
COMMENT ON COLUMN user_feedbacks.message_id IS '关联的消息ID';
COMMENT ON COLUMN user_feedbacks.rating IS '用户评价（1-5）';
COMMENT ON COLUMN user_feedbacks.comment IS '用户具体评价内容（亮点字段）';
COMMENT ON COLUMN user_feedbacks.created_at IS '记录创建时间';

-- 为 message_id 创建索引，方便快速定位被评价的消息
CREATE INDEX idx_user_feedbacks_message_id ON user_feedbacks(message_id);