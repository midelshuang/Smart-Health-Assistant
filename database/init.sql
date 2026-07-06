-- 作用是一键初始化你的数据库结构
-- 创建三张核心数据表：
-- 编写 CREATE TABLE expert_articles 语句，定义经验层的字段（如 id, title, content, source_tag, metadata, created_at）。
-- 编写 CREATE TABLE nutrition_facts 语句，定义结构化数据层的字段（如 id, food_name, aliases, category, calories_per_100g, protein, fat, carbs）。
-- 编写 CREATE TABLE user_feedback 语句，定义反馈层的字段（如 id, user_id, question, answer, rating, expert_correction, created_at）。
-- 设置主键与约束：
-- 为每张表指定 PRIMARY KEY（通常是 id 字段）。
-- 为必填字段添加 NOT NULL 约束。
-- 为 created_at 等字段设置合理的 DEFAULT 默认值。
-- 数据类型声明：
-- 准确选择数据类型，例如文本用 VARCHAR 或 TEXT，数值用 DECIMAL，扩展字段用 JSONB，时间用 TIMESTAMP。
-- （进阶加分项）创建索引：
-- 为了提升未来的查询效率，你可以考虑在 nutrition_facts 表的 food_name 或 category 字段上创建索引（CREATE INDEX）。

    -- 1. 如果表已存在，则删除
DROP TABLE IF EXISTS expert_articles;
DROP TABLE IF EXISTS nutrition_facts;
DROP TABLE IF EXISTS user_feedback;


-- 第一步：编写 CREATE TABLE expert_articles 语句，
-- 定义经验层的字段（如 id, title, content, source_tag, metadata, created_at）。
-- 并为其设置主键与约束、数据类型和创建索引。
-- 1. 创建表结构（括号内只保留字段定义、主键、默认值等）
CREATE TABLE IF NOT EXISTS expert_articles (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    source_tag VARCHAR(50),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. 为字段添加注释（必须在建表语句外面单独写）
COMMENT ON TABLE expert_articles IS '经验层：存储卫健委指南、专家视频提取文本等';
COMMENT ON COLUMN expert_articles.id IS '文章唯一标识';
COMMENT ON COLUMN expert_articles.title IS '文章标题';
COMMENT ON COLUMN expert_articles.content IS '文章正文内容';
COMMENT ON COLUMN expert_articles.source_tag IS '来源标签（如：卫健委、专家视频）';
COMMENT ON COLUMN expert_articles.metadata IS '扩展元数据（JSON格式）';
COMMENT ON COLUMN expert_articles.created_at IS '记录创建时间';

-- 3. 创建索引（满足你“创建索引”的要求）
-- 为 source_tag 创建索引，方便以后按来源筛选数据
CREATE INDEX idx_expert_articles_source_tag ON expert_articles(source_tag);

-- 第2步：编写 CREATE TABLE nutrition_facts 语句，
-- 定义结构化数据层的字段（如 id, food_name, aliases, category, calories_per_100g, protein, fat, carbs）。
-- 并为其设置主键与约束、数据类型和创建索引。
-- 1. 创建表结构（括号内只保留字段定义、主键、默认值等）
create table IF NOT EXISTS nutrition_facts(
    id serial primary key,
    food_name varchar(100) not null,
    aliases varchar(255) default null,
    category varchar(50) default null,
    calories_per_100g decimal(8,2) not null,
    protein decimal(8,2) not null,
    fat decimal(8,2) not null,
    carbs decimal(8,2) not null
);

-- 2. 为字段添加注释（必须在建表语句外面单独写）
COMMENT ON TABLE nutrition_facts IS '结构化数据层：精准计算餐食营养信息';
COMMENT ON COLUMN nutrition_facts.id IS '食物唯一标识';
COMMENT ON COLUMN nutrition_facts.food_name IS '标准食物名称';
COMMENT ON COLUMN nutrition_facts.aliases IS '食物别名';
COMMENT ON COLUMN nutrition_facts.category IS '食物分类';
COMMENT ON COLUMN nutrition_facts.calories_per_100g IS '每100g热量（大卡/kcal）';
COMMENT ON COLUMN nutrition_facts.protein IS '每100g蛋白质（克）';
COMMENT ON COLUMN nutrition_facts.fat IS '每100g脂肪（克）';
COMMENT ON COLUMN nutrition_facts.carbs IS '每100g碳水化合物（克）';

-- 3. 创建索引（满足你“创建索引”的要求）
CREATE INDEX idx_nutrition_facts_food_name ON nutrition_facts(food_name);

-- 第3步：编写 CREATE TABLE user_feedback 语句，
-- 创建反馈层的字段（如 id, user_id, question, answer, rating, expert_correction, created_at）。
-- 并为其设置主键与约束、数据类型和创建索引。
-- 1. 创建表结构（括号内只保留字段定义、主键、默认值等）
create table IF NOT EXISTS user_feedback (
    id serial primary key,
    user_id varchar(100) default null,
    question text not null,
    answer text not null,
    rating smallint default null,
    expert_correction text default null,
    created_at timestamp default CURRENT_TIMESTAMP
);

-- 2. 为字段添加注释（必须在建表语句外面单独写）
COMMENT ON TABLE user_feedback IS '反馈层：闭环与优化';
COMMENT ON COLUMN user_feedback.id IS '反馈唯一标识';
COMMENT ON COLUMN user_feedback.user_id IS '用户标识';
COMMENT ON COLUMN user_feedback.question IS '用户提问';
COMMENT ON COLUMN user_feedback.answer IS 'AI/专家回答';
COMMENT ON COLUMN user_feedback.rating IS '用户评价';
COMMENT ON COLUMN user_feedback.expert_correction IS '亮点字段，用于记录用户对AI/专家的改进建议';
COMMENT ON COLUMN user_feedback.created_at IS '记录创建时间';

-- 第4步：创建索引
-- 为 user_feedback 表的 user_id 创建索引，方便以后按用户筛选数据
CREATE INDEX idx_user_feedback_user_id ON user_feedback(user_id);