"""
scripts/import_gi.py
职责：升糖指数（GI）数据的 ETL 全流程
"""
import json
import os
import pandas as pd
from sqlalchemy import create_engine, text
from database.schema import GlycemicIndex

# 引入配置
import config.setting as ST
# 引入我们封装好的 Logger
from utils.logger import setup_logger

# 初始化当前模块的日志器
logger = setup_logger(name="GIImporter")

# GI 数据文件的路径，请根据实际情况修改
GI_DATA_FILE_PATH = ST.GI_file_path

def extract_data():
    """
    Extract: 读取并解析 GI 的 JSON 文件
    """
    if not os.path.exists(GI_DATA_FILE_PATH):
        logger.error(f"❌ GI 数据文件不存在: {GI_DATA_FILE_PATH}")
        return None

    logger.info(f"📖 正在读取 GI 数据文件: {GI_DATA_FILE_PATH}")
    try:
        with open(GI_DATA_FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)

        all_records = []
        for group in data:
            food_group = group.get('foodGroup')
            for item in group.get('list', []):
                # 清洗食物名称，去掉开头的 "* " 等符号
                food_name = item.get('foodName', '').lstrip('* ').strip()
                gi_value = item.get('GI')
                if food_name and gi_value is not None:
                    all_records.append({
                        'food_name': food_name,
                        'gi_value': gi_value,
                        'food_group': food_group
                    })

        df = pd.DataFrame(all_records)
        logger.info(f"✅ 成功解析数据，共 {len(df)} 条记录")
        return df

    except Exception as e:
        logger.exception(f"❌ 解析 GI 数据文件失败: {e}")
        return None

def transform_data(df):
    """
    Transform: 数据清洗
    """
    logger.info("🔧 开始数据清洗...")
    # 确保 GI 值为数字类型
    df['gi_value'] = pd.to_numeric(df['gi_value'], errors='coerce')
    # 删除 GI 值为空的行
    df.dropna(subset=['gi_value'], inplace=True)
    logger.info(f"✅ 数据清洗完成，剩余 {len(df)} 条有效记录")
    return df

def load_data(df):
    """
    Load: 写入 PostgreSQL
    """
    engine = create_engine(ST.DB_CONNECTION_STRING)
    table_name = GlycemicIndex.__tablename__

    try:
        # 1. 先清空表（因为 GI 数据是相对静态的参考数据，直接全量覆盖更简单）
        with engine.connect() as conn:
            conn.execute(text(f"TRUNCATE TABLE {table_name}"))
            conn.commit()
            logger.info(f"🧹 已清空表 {table_name} 的旧数据")

        # 2. 批量写入新数据
        df.to_sql(
            name=table_name,
            con=engine,
            if_exists='append',
            index=False,
            chunksize=1000
        )
        logger.info(f"✅ 成功写入 GI 数据，共 {len(df)} 条")

    except Exception as e:
        logger.exception(f"❌ 数据库写入失败: {e}")

# ================= 主入口 =================
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🚀 GI 数据导入任务启动")
    logger.info("=" * 50)

    raw_df = extract_data()
    if raw_df is not None and not raw_df.empty:
        clean_df = transform_data(raw_df)
        load_data(clean_df)

    logger.info("🏁 任务结束。")