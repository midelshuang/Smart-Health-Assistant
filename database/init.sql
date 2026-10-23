-- 作用是一键初始化你的数据库结构
-- 创建五张核心数据表：
-- 1. authoritative_sources：权威信源层（指南、文献、专家媒体）
-- 2. nutrition_facts：结构化数据层（营养成分表）
-- 3. user_profiles：用户画像层（用户档案与特殊需求）
-- 4. chat_histories：交互记录层（历史对话，配合Redis使用）
-- 5. user_feedbacks：反馈评价层（用户评价与纠偏，配合ChromaDB使用）
-- 设置主键与约束，并为高频查询字段创建索引。

-- 作用：一键初始化数据库，创建所有核心表结构
-- 此脚本为最终版本，包含所有业务字段

-- ==========================================================
-- HealthAI-Platform Database Initialization Script
-- Version: 2.0 (V4 Architecture Ready)
-- Description: Creates all core tables, indexes, and extensions.
-- Usage: psql -U <user> -d <dbname> -f init.sql
-- ==========================================================

-- 1. 启用必要的 PostgreSQL 扩展
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- 用于模糊搜索和相似度匹配
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";  -- 用于生成标准的 UUID

-- ==========================================================
-- 1. 权威信源表 (authoritative_sources)
-- 作用: 存储指南、文献、专家媒体等 RAG 检索源
-- ==========================================================
CREATE TABLE IF NOT EXISTS authoritative_sources (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    source_type VARCHAR(20) NOT NULL CHECK (source_type IN ('guideline', 'literature', 'expert_media')),
    media_type VARCHAR(10) DEFAULT 'text' CHECK (media_type IN ('text', 'pdf', 'video', 'audio')),
    summary TEXT,
    transcript TEXT,
    file_url VARCHAR(500),
    publish_date DATE,
    source_version VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE authoritative_sources IS '权威信源层：统一管理指南、文献和专家经验';
COMMENT ON COLUMN authoritative_sources.id IS '信源唯一标识';
COMMENT ON COLUMN authoritative_sources.title IS '文章/视频标题';
COMMENT ON COLUMN authoritative_sources.source_type IS '来源类型（guideline/literature/expert_media）';
COMMENT ON COLUMN authoritative_sources.media_type IS '媒体格式（text/pdf/video/audio）';
COMMENT ON COLUMN authoritative_sources.summary IS '内容摘要';
COMMENT ON COLUMN authoritative_sources.transcript IS '视频/音频转录文本（用于AI向量化检索）';
COMMENT ON COLUMN authoritative_sources.file_url IS '原始文件存储地址';
COMMENT ON COLUMN authoritative_sources.publish_date IS '发布/出版日期';
COMMENT ON COLUMN authoritative_sources.source_version IS '信源版本号，用于追踪更新';
COMMENT ON COLUMN authoritative_sources.created_at IS '记录创建时间';

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_authoritative_sources_type') THEN
        CREATE INDEX idx_authoritative_sources_type ON authoritative_sources(source_type);
    END IF;
END $$;

-- ==========================================================
-- 2. 营养成分表 (nutrition_facts)
-- 作用: 结构化食物数据，支持精准计算
-- ==========================================================
CREATE TABLE IF NOT EXISTS nutrition_facts (
    id SERIAL PRIMARY KEY,
    food_name VARCHAR(100) NOT NULL,
    aliases VARCHAR(255),
    category VARCHAR(50),
    calories_per_100g DECIMAL(8,2),          -- ✅ 去掉 NOT NULL
    protein DECIMAL(8,2),                    -- ✅ 去掉 NOT NULL
    fat DECIMAL(8,2),                        -- ✅ 去掉 NOT NULL
    carbs DECIMAL(8,2),                      -- ✅ 去掉 NOT NULL
    details JSONB DEFAULT '{}',
    data_source VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- V4: 食物图片URL（模型 db_models.py 有此列，旧库补列用）
ALTER TABLE nutrition_facts ADD COLUMN IF NOT EXISTS food_image_url VARCHAR(500);

COMMENT ON TABLE nutrition_facts IS '结构化数据层：精准计算餐食营养信息';
COMMENT ON COLUMN nutrition_facts.id IS '食物唯一标识';
COMMENT ON COLUMN nutrition_facts.food_name IS '标准食物名称';
COMMENT ON COLUMN nutrition_facts.aliases IS '食物别名（用于模糊匹配）';
COMMENT ON COLUMN nutrition_facts.category IS '食物分类';
COMMENT ON COLUMN nutrition_facts.calories_per_100g IS '每100g热量（大卡/kcal）';
COMMENT ON COLUMN nutrition_facts.protein IS '每100g蛋白质（克）';
COMMENT ON COLUMN nutrition_facts.fat IS '每100g脂肪（克）';
COMMENT ON COLUMN nutrition_facts.carbs IS '每100g碳水化合物（克）';
COMMENT ON COLUMN nutrition_facts.details IS '其他微量元素详情（JSONB格式）';
COMMENT ON COLUMN nutrition_facts.data_source IS '数据来源（如 USDA, 中国食物成分表）';
COMMENT ON COLUMN nutrition_facts.created_at IS '记录创建时间';

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_nutrition_facts_food_name_gin') THEN
        CREATE INDEX idx_nutrition_facts_food_name_gin ON nutrition_facts USING gin (food_name gin_trgm_ops);
    END IF;
END $$;

-- ==========================================================
-- 3. 升糖指数表 (glycemic_index_facts)
-- 作用: 辅助糖尿病/控糖用户的饮食建议
-- ==========================================================
CREATE TABLE IF NOT EXISTS glycemic_index_facts (
    id SERIAL PRIMARY KEY,
    food_name VARCHAR(255) NOT NULL,
    gi_value DECIMAL(5, 2) NOT NULL,
    food_group VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE glycemic_index_facts IS '升糖指数(GI)数据表';
COMMENT ON COLUMN glycemic_index_facts.id IS '主键ID';
COMMENT ON COLUMN glycemic_index_facts.food_name IS '食物名称';
COMMENT ON COLUMN glycemic_index_facts.gi_value IS '升糖指数值';
COMMENT ON COLUMN glycemic_index_facts.food_group IS '食物大类';
COMMENT ON COLUMN glycemic_index_facts.created_at IS '创建时间';
COMMENT ON COLUMN glycemic_index_facts.updated_at IS '更新时间';

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_gi_food_name') THEN
        CREATE INDEX idx_gi_food_name ON glycemic_index_facts(food_name);
    END IF;
END $$;

-- ==========================================================
-- 4. 用户档案表 (user_profiles)
-- 作用: 存储用户画像、身体指标及健康目标
-- ==========================================================
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id VARCHAR(50) PRIMARY KEY,
    nickname VARCHAR(100),
    health_tags JSONB DEFAULT '[]', -- V4预留: 动态健康标签
    allergies TEXT,
    age INT,
    height DOUBLE PRECISION, -- 单位: 米
    weight DOUBLE PRECISION, -- 单位: 千克
    gender VARCHAR(10),
    bmi DOUBLE PRECISION,
    health_goal VARCHAR(100),
    province VARCHAR(50),
    body_fat_percentage DOUBLE PRECISION,
    sports_hobby TEXT,
    last_login TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- ============================================================
    -- V4 预留字段（社交/专家/权限/可见性扩展）
    -- 当前版本默认值或 NULL，不影响 V1 逻辑
    -- ============================================================
    role VARCHAR(20) DEFAULT 'user',                  -- V4: user/expert/admin
    expert_profile_id VARCHAR(50),                    -- V4: 关联专家档案表
    social_settings JSONB DEFAULT '{}',               -- V4: 社交偏好（关注/粉丝/可见性）
    visibility VARCHAR(20) DEFAULT 'private',          -- V4: public/friends/private
    following_count INT DEFAULT 0,                    -- V4: 关注数（冗余计数）
    follower_count INT DEFAULT 0,                      -- V4: 粉丝数（冗余计数）
    verified_status VARCHAR(20) DEFAULT 'unverified',  -- V4: unverified/pending/verified
    expert_verified_at TIMESTAMP WITH TIME ZONE        -- V4: 专家认证通过时间
);

COMMENT ON TABLE user_profiles IS '用户画像层：存储用户基本信息与特殊健康状况';
COMMENT ON COLUMN user_profiles.user_id IS '用户唯一标识';
COMMENT ON COLUMN user_profiles.nickname IS '用户昵称';
COMMENT ON COLUMN user_profiles.health_tags IS '健康标签（JSONB格式）';
COMMENT ON COLUMN user_profiles.allergies IS '过敏原信息';
COMMENT ON COLUMN user_profiles.age IS '年龄';
COMMENT ON COLUMN user_profiles.height IS '身高（米）';
COMMENT ON COLUMN user_profiles.weight IS '体重（千克）';
COMMENT ON COLUMN user_profiles.gender IS '性别';
COMMENT ON COLUMN user_profiles.bmi IS '身体质量指数（BMI）';
COMMENT ON COLUMN user_profiles.health_goal IS '健康目标（如：减重、增肌、维持健康等）';
COMMENT ON COLUMN user_profiles.province IS '所在省份/地区';
COMMENT ON COLUMN user_profiles.body_fat_percentage IS '体脂率（百分比，如 20.5 表示 20.5%）';
COMMENT ON COLUMN user_profiles.sports_hobby IS '运动爱好';
COMMENT ON COLUMN user_profiles.last_login IS '用户最后登录时间';
COMMENT ON COLUMN user_profiles.updated_at IS '档案最后更新时间';
-- V4 预留字段注释
COMMENT ON COLUMN user_profiles.role IS 'V4预留: 用户角色（user/expert/admin），默认 user';
COMMENT ON COLUMN user_profiles.expert_profile_id IS 'V4预留: 关联专家档案表 ID';
COMMENT ON COLUMN user_profiles.social_settings IS 'V4预留: 社交偏好（关注列表/粉丝列表/通知设置）';
COMMENT ON COLUMN user_profiles.visibility IS 'V4预留: 个人主页可见性（public/friends/private）';
COMMENT ON COLUMN user_profiles.following_count IS 'V4预留: 关注数（冗余计数，实际查关系表）';
COMMENT ON COLUMN user_profiles.follower_count IS 'V4预留: 粉丝数（冗余计数，实际查关系表）';
COMMENT ON COLUMN user_profiles.verified_status IS 'V4预留: 专家认证状态';
COMMENT ON COLUMN user_profiles.expert_verified_at IS 'V4预留: 专家认证通过时间';

-- 1. 确保 user_profiles 表里有 user_id 字段（如果没有就加上）
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS user_id VARCHAR(50);

-- 2. 外键约束已移至 users 表建表之后（原位置在 users 建表前，全新库会报错）

-- ==========================================================
-- 5. 每日饮食记录表 (daily_diet_logs) [新增核心表]
-- 作用: 将非结构化对话转化为结构化数据，支撑 TDEE 计算与趋势分析
-- ==========================================================
CREATE TABLE IF NOT EXISTS daily_diet_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(50) NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    log_date DATE NOT NULL DEFAULT CURRENT_DATE,
    meal_type VARCHAR(20) NOT NULL CHECK (meal_type IN ('breakfast', 'lunch', 'dinner', 'snack')),
    nutrition_id INT REFERENCES nutrition_facts(id),
    quantity_grams DECIMAL(8,2) NOT NULL,
    calculated_calories DECIMAL(8,2), -- 冗余字段，避免每次重新计算
    ai_parsed BOOLEAN DEFAULT FALSE, -- 标记是否由 AI 自动解析
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE daily_diet_logs IS '业务核心层：用户每日实际饮食摄入记录';
COMMENT ON COLUMN daily_diet_logs.id IS '饮食记录唯一标识';
COMMENT ON COLUMN daily_diet_logs.user_id IS '关联的用户ID';
COMMENT ON COLUMN daily_diet_logs.log_date IS '记录日期';
COMMENT ON COLUMN daily_diet_logs.meal_type IS '餐次类型（breakfast/lunch/dinner/snack）';
COMMENT ON COLUMN daily_diet_logs.nutrition_id IS '关联营养表ID';
COMMENT ON COLUMN daily_diet_logs.quantity_grams IS '食物重量（克）';
COMMENT ON COLUMN daily_diet_logs.calculated_calories IS '计算得出的热量（大卡/kcal）';
COMMENT ON COLUMN daily_diet_logs.ai_parsed IS '是否由AI自动解析生成（true/false）';
COMMENT ON COLUMN daily_diet_logs.created_at IS '记录创建时间';

-- 安全创建索引 (防止重复执行报错)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_daily_logs_user_date') THEN
        CREATE INDEX idx_daily_logs_user_date ON daily_diet_logs(user_id, log_date DESC);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_diet_nutrition_id') THEN
        CREATE INDEX idx_diet_nutrition_id ON daily_diet_logs(nutrition_id);
    END IF;
END $$;

-- ==========================================================
-- 6. 聊天记录表 (chat_histories)
-- 作用: 历史对话冷备份，配合 Redis 实现热冷分离
-- ==========================================================
CREATE TABLE IF NOT EXISTS chat_histories (
    id SERIAL PRIMARY KEY,
    message_uuid UUID DEFAULT uuid_generate_v4() UNIQUE,
    session_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(50),
    role VARCHAR(10) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    message_content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    model_version VARCHAR(20), -- V4预留: 追踪模型迭代效果
    metadata JSONB DEFAULT '{}' -- V4预留: 存储 Token 消耗、延迟等技术指标
);

COMMENT ON TABLE chat_histories IS '交互记录层：历史对话冷备份';
COMMENT ON COLUMN chat_histories.id IS '消息自增主键ID';
COMMENT ON COLUMN chat_histories.message_uuid IS '消息全局唯一ID（UUID）';
COMMENT ON COLUMN chat_histories.session_id IS '会话ID';
COMMENT ON COLUMN chat_histories.user_id IS '用户标识';
COMMENT ON COLUMN chat_histories.role IS '角色（user/assistant/system）';
COMMENT ON COLUMN chat_histories.message_content IS '对话内容';
COMMENT ON COLUMN chat_histories.timestamp IS '消息发送时间';
COMMENT ON COLUMN chat_histories.model_version IS '生成此回复的AI模型版本';
COMMENT ON COLUMN chat_histories.metadata IS '扩展元数据（JSONB格式，如Token消耗、延迟等）';

-- 安全创建索引 (防止重复执行报错)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_chat_session_time') THEN
        CREATE INDEX idx_chat_session_time ON chat_histories(session_id, timestamp DESC);
    END IF;
END $$;


-- ==========================================================
-- 7. 用户反馈表 (user_feedbacks)
-- 作用: RLHF 数据收集，用于后续模型微调
-- ==========================================================
CREATE TABLE IF NOT EXISTS user_feedbacks (
    id SERIAL PRIMARY KEY,
    message_id UUID NOT NULL REFERENCES chat_histories(message_uuid) ON DELETE CASCADE,
    rating SMALLINT CHECK (rating BETWEEN 1 AND 5),
    comment TEXT,
    feedback_type VARCHAR(20) DEFAULT 'rating',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE user_feedbacks IS '反馈评价层：收集用户满意度与改进建议';
COMMENT ON COLUMN user_feedbacks.id IS '反馈唯一标识';
COMMENT ON COLUMN user_feedbacks.message_id IS '关联的消息UUID';
COMMENT ON COLUMN user_feedbacks.rating IS '用户评价（1-5分）';
COMMENT ON COLUMN user_feedbacks.comment IS '用户具体评价内容';
COMMENT ON COLUMN user_feedbacks.feedback_type IS '反馈类型（如: rating, thumbs_up, thumbs_down）';
COMMENT ON COLUMN user_feedbacks.created_at IS '记录创建时间';

-- 安全创建索引 (防止重复执行报错)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_feedback_message') THEN
        CREATE INDEX idx_feedback_message ON user_feedbacks(message_id);
    END IF;
END $$;


-- ==========================================================
-- 8. 健身目标追踪表 (fitness_goals)
-- 作用: 记录用户的阶段性目标，AI 根据此表动态调整饮食与运动建议
-- ==========================================================
CREATE TABLE IF NOT EXISTS fitness_goals (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(50) NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    goal_type VARCHAR(50) NOT NULL CHECK (goal_type IN ('weight_loss', 'muscle_gain', 'maintenance', 'fat_loss')),
    target_value DECIMAL(8,2), -- 目标值（如目标体重 60kg，或体脂率 18%）
    start_value DECIMAL(8,2),  -- 开始时的基准值
    current_value DECIMAL(8,2),-- 当前进度值
    target_date DATE,          -- 期望达成的截止日期
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'completed', 'paused', 'cancelled')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE fitness_goals IS '目标追踪层：记录用户的阶段性健康与健身目标';
COMMENT ON COLUMN fitness_goals.id IS '目标记录唯一标识';
COMMENT ON COLUMN fitness_goals.user_id IS '关联的用户ID';
COMMENT ON COLUMN fitness_goals.goal_type IS '目标类型（weight_loss减重/muscle_gain增肌/maintenance维持/fat_loss减脂）';
COMMENT ON COLUMN fitness_goals.target_value IS '目标值';
COMMENT ON COLUMN fitness_goals.start_value IS '初始基准值';
COMMENT ON COLUMN fitness_goals.current_value IS '当前进度值';
COMMENT ON COLUMN fitness_goals.target_date IS '期望达成目标的日期';
COMMENT ON COLUMN fitness_goals.status IS '目标状态（active/completed/paused/cancelled）';
COMMENT ON COLUMN fitness_goals.created_at IS '目标创建时间';
COMMENT ON COLUMN fitness_goals.updated_at IS '目标更新时间';

-- 安全创建索引
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_fitness_goals_user_status') THEN
        CREATE INDEX idx_fitness_goals_user_status ON fitness_goals(user_id, status);
    END IF;
END $$;

-- ==========================================================
-- 9. 用户身份主表 (users)
-- 作用: 存储用户唯一标识，与业务数据（Profile/Plan）解耦
-- ==========================================================
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    external_id VARCHAR(64) NOT NULL UNIQUE,      -- 外部系统ID (微信OpenID/手机号Hash等)
    source VARCHAR(20) NOT NULL DEFAULT 'wechat', -- 来源渠道
    is_active BOOLEAN DEFAULT TRUE,               -- 账号状态
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE users IS '用户身份层：唯一标识与账号管理';
COMMENT ON COLUMN users.id IS '内部主键ID';
COMMENT ON COLUMN users.external_id IS '外部唯一标识 (用于登录鉴权)';
COMMENT ON COLUMN users.source IS '注册来源 (wechat/phone/email)';
COMMENT ON COLUMN users.is_active IS '是否激活';
COMMENT ON COLUMN users.created_at IS '注册时间';

-- 为 external_id 建立普通索引，加速登录查询
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_users_external_id') THEN
        CREATE INDEX idx_users_external_id ON users (external_id);
    END IF;
END $$;

ALTER TABLE users ADD COLUMN username VARCHAR(64) UNIQUE;
ALTER TABLE users ADD COLUMN password_hash VARCHAR(255);

-- ===== 邮箱注册 / 安全问题（V2） =====
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='users' AND column_name='email') THEN
        ALTER TABLE users ADD COLUMN email VARCHAR(128) UNIQUE;
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='users' AND column_name='email_verified') THEN
        ALTER TABLE users ADD COLUMN email_verified BOOLEAN DEFAULT FALSE;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='users' AND column_name='last_login_at') THEN
        ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP WITH TIME ZONE;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='users' AND column_name='last_login_ip') THEN
        ALTER TABLE users ADD COLUMN last_login_ip VARCHAR(64);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='users' AND column_name='security_question') THEN
        ALTER TABLE users ADD COLUMN security_question VARCHAR(255);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='users' AND column_name='security_answer_hash') THEN
        ALTER TABLE users ADD COLUMN security_answer_hash VARCHAR(255);
    END IF;
END $$;

COMMENT ON COLUMN users.email IS '邮箱（可用于登录与找回密码）';
COMMENT ON COLUMN users.email_verified IS '邮箱是否已通过验证码验证';
COMMENT ON COLUMN users.last_login_at IS '最后登录时间（用于安全审计）';
COMMENT ON COLUMN users.last_login_ip IS '最后登录 IP（用于安全审计）';
COMMENT ON COLUMN users.security_question IS '安全问题（用于忘记密码重置）';
COMMENT ON COLUMN users.security_answer_hash IS '安全问题答案哈希';

-- user_profiles.user_id → users.external_id 外键（必须在 users 表建好后执行）
-- 原先此块位于 users 建表之前，导致全新库执行时报 relation "users" does not exist
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_user_profile_user') THEN
        ALTER TABLE user_profiles
        ADD CONSTRAINT fk_user_profile_user
        FOREIGN KEY (user_id) REFERENCES users(external_id);
    END IF;
END $$;

-- ==========================================================
-- 10. 个性化计划表 (plans)
-- 作用: 存储为用户生成的具体执行方案 (食养/减脂/增肌)
-- ==========================================================
CREATE TABLE IF NOT EXISTS plans (
    id BIGSERIAL PRIMARY KEY, -- 建议用 BIGSERIAL 以匹配 Python 的 BigInteger
    user_id VARCHAR(50) NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    goal_type VARCHAR(50) NOT NULL,
    config JSONB DEFAULT '{}',
    start_date DATE NOT NULL,
    end_date DATE,
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE plans IS '计划执行层：具体的饮食/运动方案';
COMMENT ON COLUMN plans.user_id IS '关联用户档案ID (对应 user_profiles.user_id)';
COMMENT ON COLUMN plans.goal_type IS '计划目标类型';
COMMENT ON COLUMN plans.config IS '计划详细配置 (JSONB格式)';
COMMENT ON COLUMN plans.start_date IS '生效开始日期';
COMMENT ON COLUMN plans.end_date IS '生效结束日期';
COMMENT ON COLUMN plans.status IS '当前状态';
COMMENT ON COLUMN plans.created_at IS '创建时间';

-- 索引
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_plans_user_status') THEN
        CREATE INDEX idx_plans_user_status ON plans (user_id, status);
    END IF;
END $$;

-- ==========================================================
-- 11. 计划打卡记录表 (plan_checkins)
-- 作用: 记录每日计划完成情况，用于追踪进度和反馈
-- ==========================================================
CREATE TABLE IF NOT EXISTS plan_checkins (
    id SERIAL PRIMARY KEY,
    plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    checkin_date DATE NOT NULL,                   -- 打卡日期
    status VARCHAR(20) DEFAULT 'pending',         -- 完成状态: pending, completed, failed, partial
    actual_calories DECIMAL(8,2),                 -- 实际摄入热量 (可选，从diet_logs聚合或手动填)
    notes TEXT,                                   -- 用户备注 (如: "今天聚餐吃多了")
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(plan_id, checkin_date)                 -- ✅ 联合唯一约束：同一天同一计划只能打卡一次
);

COMMENT ON TABLE plan_checkins IS '打卡追踪层：每日执行情况记录';
COMMENT ON COLUMN plan_checkins.plan_id IS '关联计划ID';
COMMENT ON COLUMN plan_checkins.checkin_date IS '打卡日期';
COMMENT ON COLUMN plan_checkins.status IS '当日完成状态';
COMMENT ON COLUMN plan_checkins.actual_calories IS '当日实际摄入热量';
COMMENT ON COLUMN plan_checkins.notes IS '打卡备注';
COMMENT ON COLUMN plan_checkins.created_at IS '打卡时间';

-- 防止重复打卡的索引
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_plan_checkins_unique') THEN
        CREATE UNIQUE INDEX idx_plan_checkins_unique ON plan_checkins (plan_id, checkin_date);
    END IF;
END $$;


-- ============================================================
-- V4 预留：兼容已有数据库的字段升级
-- 对已存在的 user_profiles 表执行幂等 ALTER TABLE，
-- 保证从 V1/V2 升级到 V4 时不会丢失既有数据。
-- 全部使用 ADD COLUMN IF NOT EXISTS，可重复执行。
-- ============================================================

ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'user';
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS expert_profile_id VARCHAR(50);
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS social_settings JSONB DEFAULT '{}';
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) DEFAULT 'private';
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS following_count INT DEFAULT 0;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS follower_count INT DEFAULT 0;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS verified_status VARCHAR(20) DEFAULT 'unverified';
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS expert_verified_at TIMESTAMP WITH TIME ZONE;

-- V4 预留：role 字段索引（管理员查询用）
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_user_profiles_role') THEN
        CREATE INDEX idx_user_profiles_role ON user_profiles(role);
    END IF;
END $$;

-- V4 预留：visibility 字段索引（社交可见性过滤）
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_user_profiles_visibility') THEN
        CREATE INDEX idx_user_profiles_visibility ON user_profiles(visibility);
    END IF;
END $$;

-- ==========================================================
-- V4 Schema 同步段（幂等）
-- 作用: 让 init.sql 与 src/models/db_models.py 的 V4 预留字段保持一致
-- 约束: 必须放在文件末尾（所有表已建好）；全部使用 ADD COLUMN IF NOT EXISTS
-- ==========================================================

-- authoritative_sources（信源可信度）
ALTER TABLE authoritative_sources ADD COLUMN IF NOT EXISTS credibility_score NUMERIC(3,2);
ALTER TABLE authoritative_sources ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(64);
ALTER TABLE authoritative_sources ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP WITH TIME ZONE;

-- users（会员体系）
ALTER TABLE users ADD COLUMN IF NOT EXISTS membership_tier VARCHAR(20) DEFAULT 'free';
ALTER TABLE users ADD COLUMN IF NOT EXISTS membership_expires_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS device_info JSONB;

-- user_profiles（画像增强）
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS dietary_preference JSONB;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS metabolic_data JSONB;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS medical_conditions JSONB;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS target_heart_rate INTEGER;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS recommended_daily_water INTEGER;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS profile_version INTEGER DEFAULT 1;

-- daily_diet_logs（AI 解析增强）
ALTER TABLE daily_diet_logs ADD COLUMN IF NOT EXISTS actual_macros JSONB;
ALTER TABLE daily_diet_logs ADD COLUMN IF NOT EXISTS meal_photo_url VARCHAR(500);
ALTER TABLE daily_diet_logs ADD COLUMN IF NOT EXISTS confidence_score NUMERIC(3,2);
ALTER TABLE daily_diet_logs ADD COLUMN IF NOT EXISTS parse_model_version VARCHAR(20);

-- chat_histories（调用成本追踪）
ALTER TABLE chat_histories ADD COLUMN IF NOT EXISTS tokens_used INTEGER;
ALTER TABLE chat_histories ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10,6);
ALTER TABLE chat_histories ADD COLUMN IF NOT EXISTS response_time_ms INTEGER;
ALTER TABLE chat_histories ADD COLUMN IF NOT EXISTS user_feedback_score INTEGER;
ALTER TABLE chat_histories ADD COLUMN IF NOT EXISTS attachments JSONB;

-- user_feedbacks
ALTER TABLE user_feedbacks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE user_feedbacks ADD COLUMN IF NOT EXISTS used_for_training BOOLEAN DEFAULT FALSE;

-- nutrition_facts（食物画像）
ALTER TABLE nutrition_facts ADD COLUMN IF NOT EXISTS food_image_url VARCHAR(500);
ALTER TABLE nutrition_facts ADD COLUMN IF NOT EXISTS glycemic_load NUMERIC(5,2);
ALTER TABLE nutrition_facts ADD COLUMN IF NOT EXISTS satiety_index NUMERIC(5,2);
ALTER TABLE nutrition_facts ADD COLUMN IF NOT EXISTS region_tags JSONB;
ALTER TABLE nutrition_facts ADD COLUMN IF NOT EXISTS verified BOOLEAN DEFAULT FALSE;
ALTER TABLE nutrition_facts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;

-- glycemic_index_facts（GI 精细化）
ALTER TABLE glycemic_index_facts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE glycemic_index_facts ADD COLUMN IF NOT EXISTS gi_category VARCHAR(20);
ALTER TABLE glycemic_index_facts ADD COLUMN IF NOT EXISTS glucose_response JSONB;

-- fitness_goals（目标跟踪增强）
ALTER TABLE fitness_goals ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE fitness_goals ADD COLUMN IF NOT EXISTS progress_history JSONB;
ALTER TABLE fitness_goals ADD COLUMN IF NOT EXISTS ai_adjustment_suggestion TEXT;
ALTER TABLE fitness_goals ADD COLUMN IF NOT EXISTS completion_rate NUMERIC(5,2);

-- plans（计划版本链）
ALTER TABLE plans ADD COLUMN IF NOT EXISTS plan_version INTEGER DEFAULT 1;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS parent_plan_id BIGINT;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS adjustment_history JSONB;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS ai_generated BOOLEAN DEFAULT FALSE;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS generation_prompt TEXT;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;

-- plan_checkins（打卡多维数据）
ALTER TABLE plan_checkins ADD COLUMN IF NOT EXISTS actual_macros JSONB;
ALTER TABLE plan_checkins ADD COLUMN IF NOT EXISTS mood_score INTEGER;
ALTER TABLE plan_checkins ADD COLUMN IF NOT EXISTS energy_level INTEGER;
ALTER TABLE plan_checkins ADD COLUMN IF NOT EXISTS sleep_hours NUMERIC(4,2);
ALTER TABLE plan_checkins ADD COLUMN IF NOT EXISTS weight_snapshot NUMERIC(5,2);
ALTER TABLE plan_checkins ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
