"""
提取结构化的营养成分表的内容
  - 逻辑：
    - 抽取 (Extract)：使用 pandas 库的 pd.read_json方法，把 raw_data/chain-food-composition-data 下的文件读成一个 DataFrame。
        在读取文件时，把文件名里的分类“抠”出来，作为一列新数据塞进 DataFrame 里。
    - 转换 (Transform)：检查数据干不干净？有没有空值？单位统一吗？用 pandas 进行简单的清洗和格式统一。
    - 加载 (Load)：调用 SQLAlchemy 的引擎，使用 pandas 的 to_sql 方法，将处理好的 DataFrame 一次性“灌”进数据库。
"""
"""
scripts/import_nutrition.py
职责：营养成分数据的 ETL 全流程 (抽取、转换、加载)
"""
import os
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB

# 引入配置和 Schema
import config.setting as ST
from database.schema import NutritionFact

# 【关键】引入我们封装好的 Logger
from utils.logger import setup_logger

# 初始化当前模块的日志器
logger = setup_logger(name="NutritionImporter")


def extract_data():
    """
    Extract: 读取 JSON 文件并提取分类
    """
    file_path = ST.Nutrition_file_path

    # 检查路径是否存在
    if not os.path.exists(file_path):
        logger.error(f"❌ 数据路径不存在: {file_path}")
        return

    logger.info(f"📂 开始扫描目录: {file_path}")

    for file_name in os.listdir(file_path):
        if file_name.endswith('.json'):
            json_path = os.path.join(file_path, file_name)
            logger.info(f"📖 正在读取: {file_name}")

            try:
                # lines=True 兼容换行分隔的 JSON
                df = pd.read_json(json_path, lines=False)

                if df.empty:
                    logger.warning(f"⚠️ 文件内容为空，跳过: {file_name}")
                    continue

                # 提取分类名
                category = file_name.replace('merged-', '').replace('.json', '')
                df['category'] = category

                # 统一热量单位（注意：数据中 energyKCal 列的值放大了10000倍）
                if 'energyKCal' in df.columns:
                    # 先转为数值，除以10000修正格式错误，再除以4.184转换为kcal
                    df['calories_per_100g'] = pd.to_numeric(df['energyKCal'], errors='coerce') / 10000 / 4.184
                elif 'energyKJ' in df.columns:
                    df['calories_per_100g'] = pd.to_numeric(df['energyKJ'], errors='coerce') / 4.184

                # 安全删除旧列
                df.drop(columns=['energyKCal', 'energyKJ'], inplace=True, errors='ignore')

                yield df, category  # 同时传出数据和分类名

            except Exception as e:
                # 使用 logger.exception 会自动打印详细的报错堆栈
                logger.exception(f"❌ 读取文件失败 {file_name}: {e}")


def transform_data(df):
    """
    Transform: 清洗数据，对齐字段
    """
    # 1. 字段重命名
    column_mapping = {
        "foodName": "food_name",
        "protein": "protein",
        "fat": "fat",
        "CHO": "carbs"
    }
    existing_cols = {k: v for k, v in column_mapping.items() if k in df.columns}
    df.rename(columns=existing_cols, inplace=True)

    # 2. 打包微量元素到 details (JSONB)
    core_db_cols = list(existing_cols.values()) + ['category', 'calories_per_100g']
    trace_cols = [col for col in df.columns if col not in core_db_cols]

    if trace_cols:
        df['details'] = df[trace_cols].apply(lambda row: row.dropna().to_dict(), axis=1)
        df.drop(columns=trace_cols, inplace=True)
    else:
        df['details'] = None

    # 3. 数据清洗与类型转换
    df = df.replace(['-', 'Trace', 'Tr'], pd.NA)

    numeric_cols = ["calories_per_100g", "protein", "fat", "carbs"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    return df


def load_data(df, category):
    """
    Load: 写入 PostgreSQL
    """
    engine = create_engine(ST.DB_CONNECTION_STRING)
    table_name = NutritionFact.__tablename__

    # 定义特殊字段类型
    dtype_mapping = {'details': JSONB}

    try:
        # 1. 先清理该分类下的旧数据 (幂等性设计)
        with engine.connect() as conn:
            delete_sql = text(f"DELETE FROM {table_name} WHERE category = :cat")
            result = conn.execute(delete_sql, {"cat": category})
            conn.commit()
            logger.info(f"🧹 已清理 [{category}] 的旧数据 ({result.rowcount} 条)")

        # 2. 批量写入新数据
        df.to_sql(
            name=table_name,
            con=engine,
            if_exists='append',
            index=False,
            chunksize=1000,
            dtype=dtype_mapping
        )
        logger.info(f"✅ 成功写入 [{category}] 共 {len(df)} 条数据")

    except Exception as e:
        logger.exception(f"❌ 数据库写入失败 [{category}]: {e}")


# ================= 主入口 =================
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🚀 营养数据导入任务启动")
    logger.info("=" * 50)

    count = 0
    for raw_df, category in extract_data():
        clean_df = transform_data(raw_df)
        load_data(clean_df, category)
        count += 1

    logger.info(f"🏁 任务结束，共处理 {count} 个文件。")