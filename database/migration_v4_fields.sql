-- ============================================================
-- HealthAI Platform V4.0 数据库字段预留迁移脚本
-- 作用：为所有现有表添加 V4 预留字段（不破坏现有数据）
-- 策略：全部使用 ADD COLUMN IF NOT EXISTS，保证可重复执行
-- 执行方式：psql -U postgres -d healthai -f migration_v4_fields.sql
-- ============================================================

BEGIN;

-- ============================================================
-- 1. authoritative_sources（权威信源表）
-- ============================================================
ALTER TABLE authoritative_sources
    ADD COLUMN IF NOT EXISTS credibility_score NUMERIC(3, 2),
    ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(64),
    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP WITH TIME ZONE;

COMMENT ON COLUMN authoritative_sources.credibility_score IS '[V4] 信源可信度评分(0.00-1.00)';
COMMENT ON COLUMN authoritative_sources.reviewed_by IS '[V4] 审核人标识';
COMMENT ON COLUMN authoritative_sources.reviewed_at IS '[V4] 审核时间';


-- ============================================================
-- 2. users（用户身份表）
-- ============================================================
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS membership_tier VARCHAR(20) DEFAULT 'free',
    ADD COLUMN IF NOT EXISTS membership_expires_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS device_info JSONB;

COMMENT ON COLUMN users.membership_tier IS '[V4] 会员等级: free/plus/pro';
COMMENT ON COLUMN users.membership_expires_at IS '[V4] 会员到期时间';
COMMENT ON COLUMN users.last_active_at IS '[V4] 最后活跃时间';
COMMENT ON COLUMN users.device_info IS '[V4] 最近登录设备信息';


-- ============================================================
-- 3. user_profiles（用户档案表）
-- ============================================================
ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS dietary_preference JSONB,
    ADD COLUMN IF NOT EXISTS metabolic_data JSONB,
    ADD COLUMN IF NOT EXISTS medical_conditions JSONB,
    ADD COLUMN IF NOT EXISTS target_heart_rate INTEGER,
    ADD COLUMN IF NOT EXISTS recommended_daily_water INTEGER,
    ADD COLUMN IF NOT EXISTS profile_version INTEGER DEFAULT 1;

COMMENT ON COLUMN user_profiles.dietary_preference IS '[V4] 饮食偏好(素食/低盐/低糖/清真等)';
COMMENT ON COLUMN user_profiles.metabolic_data IS '[V4] 代谢数据(基础代谢率/活动系数等动态数据)';
COMMENT ON COLUMN user_profiles.medical_conditions IS '[V4] 慢性病记录(糖尿病/高血压/高血脂等)';
COMMENT ON COLUMN user_profiles.target_heart_rate IS '[V4] 目标心率区间';
COMMENT ON COLUMN user_profiles.recommended_daily_water IS '[V4] 推荐每日饮水量(ml)';
COMMENT ON COLUMN user_profiles.profile_version IS '[V4] 档案版本号(用于触发推荐重算)';


-- ============================================================
-- 4. daily_diet_logs（每日饮食记录表）
-- ============================================================
ALTER TABLE daily_diet_logs
    ADD COLUMN IF NOT EXISTS actual_macros JSONB,
    ADD COLUMN IF NOT EXISTS meal_photo_url VARCHAR(500),
    ADD COLUMN IF NOT EXISTS confidence_score NUMERIC(3, 2),
    ADD COLUMN IF NOT EXISTS parse_model_version VARCHAR(20);

COMMENT ON COLUMN daily_diet_logs.actual_macros IS '[V4] 实际宏量营养素(蛋白/脂肪/碳水/纤维)';
COMMENT ON COLUMN daily_diet_logs.meal_photo_url IS '[V4] 餐食照片URL(图像识别用)';
COMMENT ON COLUMN daily_diet_logs.confidence_score IS '[V4] AI解析置信度(0.00-1.00)';
COMMENT ON COLUMN daily_diet_logs.parse_model_version IS '[V4] 解析使用的模型版本';


-- ============================================================
-- 5. chat_histories（聊天记录表）
-- ============================================================
ALTER TABLE chat_histories
    ADD COLUMN IF NOT EXISTS tokens_used INTEGER,
    ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10, 6),
    ADD COLUMN IF NOT EXISTS response_time_ms INTEGER,
    ADD COLUMN IF NOT EXISTS user_feedback_score INTEGER,
    ADD COLUMN IF NOT EXISTS attachments JSONB;

COMMENT ON COLUMN chat_histories.tokens_used IS '[V4] 消耗的token数';
COMMENT ON COLUMN chat_histories.cost_usd IS '[V4] 单次调用成本(USD)';
COMMENT ON COLUMN chat_histories.response_time_ms IS '[V4] 响应耗时(毫秒)';
COMMENT ON COLUMN chat_histories.user_feedback_score IS '[V4] 用户反馈评分(1-5)';
COMMENT ON COLUMN chat_histories.attachments IS '[V4] 附件信息(图片/语音等)';


-- ============================================================
-- 6. user_feedbacks（用户反馈表）
-- ============================================================
ALTER TABLE user_feedbacks
    ADD COLUMN IF NOT EXISTS category VARCHAR(50),
    ADD COLUMN IF NOT EXISTS used_for_training BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN user_feedbacks.category IS '[V4] 反馈分类(事实错误/风格不佳/不安全/其他)';
COMMENT ON COLUMN user_feedbacks.used_for_training IS '[V4] 是否已用于模型微调';


-- ============================================================
-- 7. nutrition_facts（营养成分表）
-- ============================================================
ALTER TABLE nutrition_facts
    ADD COLUMN IF NOT EXISTS food_image_url VARCHAR(500),
    ADD COLUMN IF NOT EXISTS glycemic_load NUMERIC(5, 2),
    ADD COLUMN IF NOT EXISTS satiety_index NUMERIC(5, 2),
    ADD COLUMN IF NOT EXISTS region_tags JSONB,
    ADD COLUMN IF NOT EXISTS verified BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;

COMMENT ON COLUMN nutrition_facts.food_image_url IS '[V4] 标准食物图片URL';
COMMENT ON COLUMN nutrition_facts.glycemic_load IS '[V4] 血糖负荷(GL)';
COMMENT ON COLUMN nutrition_facts.satiety_index IS '[V4] 饱腹感指数';
COMMENT ON COLUMN nutrition_facts.region_tags IS '[V4] 地域标签(华北/华南/西部等)';
COMMENT ON COLUMN nutrition_facts.verified IS '[V4] 营养师审核标记';
COMMENT ON COLUMN nutrition_facts.updated_at IS '[V4] 更新时间';


-- ============================================================
-- 8. glycemic_index_facts（升糖指数表）
-- ============================================================
ALTER TABLE glycemic_index_facts
    ADD COLUMN IF NOT EXISTS gi_category VARCHAR(20),
    ADD COLUMN IF NOT EXISTS glucose_response JSONB;

COMMENT ON COLUMN glycemic_index_facts.gi_category IS '[V4] GI分类(low/medium/high)';
COMMENT ON COLUMN glycemic_index_facts.glucose_response IS '[V4] 血糖响应曲线数据';


-- ============================================================
-- 9. fitness_goals（健身目标表）
-- ============================================================
ALTER TABLE fitness_goals
    ADD COLUMN IF NOT EXISTS progress_history JSONB,
    ADD COLUMN IF NOT EXISTS ai_adjustment_suggestion TEXT,
    ADD COLUMN IF NOT EXISTS completion_rate NUMERIC(5, 2);

COMMENT ON COLUMN fitness_goals.progress_history IS '[V4] 进度历史快照';
COMMENT ON COLUMN fitness_goals.ai_adjustment_suggestion IS '[V4] AI调整建议';
COMMENT ON COLUMN fitness_goals.completion_rate IS '[V4] 完成率百分比';


-- ============================================================
-- 10. plans（个性化计划表）
-- ============================================================
ALTER TABLE plans
    ADD COLUMN IF NOT EXISTS plan_version INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS parent_plan_id BIGINT REFERENCES plans(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS adjustment_history JSONB,
    ADD COLUMN IF NOT EXISTS ai_generated BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS generation_prompt TEXT,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;

COMMENT ON COLUMN plans.plan_version IS '[V4] 计划版本号(每次调整+1)';
COMMENT ON COLUMN plans.parent_plan_id IS '[V4] 父计划ID(版本链)';
COMMENT ON COLUMN plans.adjustment_history IS '[V4] 动态调整历史记录';
COMMENT ON COLUMN plans.ai_generated IS '[V4] 是否AI生成';
COMMENT ON COLUMN plans.generation_prompt IS '[V4] 生成时的Prompt(用于复现)';
COMMENT ON COLUMN plans.updated_at IS '[V4] 更新时间';

-- 为 parent_plan_id 建索引（版本链查询）
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_plans_parent_plan_id') THEN
        CREATE INDEX idx_plans_parent_plan_id ON plans (parent_plan_id);
    END IF;
END $$;


-- ============================================================
-- 11. plan_checkins（计划打卡记录表）
-- ============================================================
ALTER TABLE plan_checkins
    ADD COLUMN IF NOT EXISTS actual_macros JSONB,
    ADD COLUMN IF NOT EXISTS mood_score INTEGER,
    ADD COLUMN IF NOT EXISTS energy_level INTEGER,
    ADD COLUMN IF NOT EXISTS sleep_hours NUMERIC(4, 2),
    ADD COLUMN IF NOT EXISTS weight_snapshot NUMERIC(5, 2),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;

COMMENT ON COLUMN plan_checkins.actual_macros IS '[V4] 当日实际宏量营养素';
COMMENT ON COLUMN plan_checkins.mood_score IS '[V4] 心情评分(1-5)';
COMMENT ON COLUMN plan_checkins.energy_level IS '[V4] 精力评分(1-5)';
COMMENT ON COLUMN plan_checkins.sleep_hours IS '[V4] 昨晚睡眠时长';
COMMENT ON COLUMN plan_checkins.weight_snapshot IS '[V4] 打卡时体重快照';
COMMENT ON COLUMN plan_checkins.updated_at IS '[V4] 更新时间';


-- ============================================================
-- 记录迁移版本
-- ============================================================
DO $$ BEGIN
    BEGIN
        INSERT INTO schema_migrations (version, description, applied_at)
        VALUES ('V4.0.0', 'V4 数据库字段预留（AI 元信息 + 个性化推荐上下文）', NOW());
    EXCEPTION WHEN undefined_table THEN
        RAISE NOTICE 'schema_migrations 表不存在，跳过版本记录';
    END;
END $$;

COMMIT;

-- ============================================================
-- 迁移完成验证查询（可单独执行）
-- ============================================================
-- SELECT
--     t.table_name,
--     COUNT(c.column_name) AS v4_field_count
-- FROM information_schema.tables t
-- JOIN information_schema.columns c
--     ON c.table_name = t.table_name
-- WHERE c.column_name LIKE '%version%'
--    OR c.column_name LIKE '%ai_%'
--    OR c.column_name LIKE '%verified%'
--    OR c.column_name LIKE '%membership%'
--    OR c.column_name LIKE '%preference%'
--    OR c.column_name LIKE '%confidence%'
-- GROUP BY t.table_name
-- ORDER BY t.table_name;
