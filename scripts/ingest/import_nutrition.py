"""
scripts/import_nutrition.py
职责：营养成分数据的 ETL 全流程 (抽取、转换、加载)
"""
import os
import sys
import re
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text, Numeric
from sqlalchemy.dialects.postgresql import JSONB

# ✅ 1. 路径修正
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import src.config.settings as ST
from src.models.db_models import NutritionFact
from src.utils.logger import setup_logger

logger = setup_logger(name="NutritionImporter")


def clean_numeric(val):
    """通用数值清洗函数"""
    if pd.isna(val) or val == '' or val == '-':
        return np.nan

    s = str(val).strip().lower()
    s = s.replace('一', '-').replace('trace', '').replace('tr', '')

    # 处理 mg 单位转换
    if 'mg' in s:
        s = s.replace('mg', '').strip()
        try:
            num = float(re.sub(r'[^\d.]', '', s)) / 1000.0
            return round(num, 4)
        except ValueError:
            return np.nan

    # 处理空格分隔的脏数据 (如 "116 491")
    if ' ' in s:
        original_s = s
        s = s.split(' ')[0]
        # 可选：打印日志提醒你有脏数据
        # logger.warning(f"发现空格分隔数据 '{original_s}'，已截取为 '{s}'")

    try:
        return float(re.sub(r'[^\d.]', '', s))
    except ValueError:
        return np.nan


def extract_data():
    """Extract: 读取 JSON 文件并计算热量"""
    file_path = ST.Nutrition_file_path

    if not os.path.exists(file_path):
        logger.error(f"❌ 数据路径不存在: {file_path}")
        return

    logger.info(f"📂 开始扫描目录: {file_path}")
    for file_name in os.listdir(file_path):
        if file_name.endswith('.json'):
            json_path = os.path.join(file_path, file_name)
            logger.info(f"📖 正在读取: {file_name}")
            try:
                df = pd.read_json(json_path, lines=False)

                # ✅ 调试信息 (保留用于观察)
                # print(f"--- 正在处理: {file_name} ---")
                # if 'energyKCal' in df.columns:
                #     print(f"前3行能量: {df[['foodName', 'energyKCal']].head(3)}")

                if df.empty:
                    continue

                category = file_name.replace('merged-', '').replace('.json', '')
                df['category'] = category

                # ✅ 核心计算逻辑
                if 'energyKCal' in df.columns:
                    df['temp_kcal'] = df['energyKCal'].apply(clean_numeric)
                else:
                    df['temp_kcal'] = np.nan

                if 'energyKJ' in df.columns:
                    df['temp_kj'] = df['energyKJ'].apply(clean_numeric)
                else:
                    df['temp_kj'] = np.nan

                # 优先 KCal，无则用 KJ 换算
                df['calories_per_100g'] = np.where(
                    pd.notna(df['temp_kcal']),
                    df['temp_kcal'],
                    df['temp_kj'] / 4.184
                )

                # ✅ 智能修复：处理单位错误 (J vs kJ)
                # 逻辑：如果 > 1000，大概率是单位错了，除以 1000
                mask_high = df['calories_per_100g'] > 1000
                df.loc[mask_high, 'calories_per_100g'] = df.loc[mask_high, 'calories_per_100g'] / 1000

                # ✅ 物理极限校验：纯脂肪约 900，超过 950 视为无效数据
                # 注意：这里不要用 900，防止精度误差误杀
                df.loc[df['calories_per_100g'] > 950, 'calories_per_100g'] = np.nan

                # 清理临时列
                df.drop(columns=['temp_kcal', 'temp_kj', 'energyKCal', 'energyKJ'], inplace=True, errors='ignore')

                yield df, category
            except Exception as e:
                logger.exception(f"❌ 读取文件失败 {file_name}: {e}")


def transform_data(df):
    """Transform: 清洗、重命名、打包 JSONB"""

    # 1. 字段重命名
    column_mapping = {
        "foodName": "food_name",
        "protein": "protein",
        "fat": "fat",
        "CHO": "carbs",
    }
    existing_cols = {k: v for k, v in column_mapping.items() if k in df.columns}
    df.rename(columns=existing_cols, inplace=True)

    # 2. 识别微量元素列
    core_db_cols = ['food_name', 'category', 'calories_per_100g', 'protein', 'fat', 'carbs']
    trace_cols = [col for col in df.columns if col not in core_db_cols and col != 'details']

    # 3. 清洗微量元素并打包为 JSONB
    details_dict = {}
    for col in trace_cols:
        df[col] = df[col].apply(clean_numeric)
        # 简单起见，这里假设你把所有非核心列都塞进 details
        # 实际生产中可能需要更精细的映射
        pass

    # ✅ 简化版：直接将剩余列转为 dict 存入 details (如果 DB 需要)
    # 如果你的 DB schema 不需要 details，可以跳过这一步
    if 'details' not in df.columns:
        # 这里只是示例，根据你的实际需求调整
        df['details'] = df[trace_cols].apply(lambda x: x.dropna().to_dict(), axis=1)
        df.drop(columns=trace_cols, inplace=True, errors='ignore')

    # 4. 确保 data_source
    if 'data_source' not in df.columns:
        df['data_source'] = 'china_food_composition'

    # 5. 白名单过滤
    allowed_columns = [
        'food_name', 'category', 'calories_per_100g',
        'protein', 'fat', 'carbs', 'details', 'data_source'
    ]
    df = df[[col for col in allowed_columns if col in df.columns]]

    # 6. 最终类型转换
    numeric_cols = ["calories_per_100g", "protein", "fat", "carbs"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    return df


def load_data(df, category):
    """Load: 写入 PostgreSQL"""
    engine = create_engine(ST.DB_CONNECTION_STRING)
    table_name = NutritionFact.__tablename__

    dtype_mapping = {
        'details': JSONB,
        'calories_per_100g': Numeric(12, 2),
        'protein': Numeric(12, 2),
        'fat': Numeric(12, 2),
        'carbs': Numeric(12, 2),
    }

    try:
        with engine.connect() as conn:
            delete_sql = text(f"DELETE FROM {table_name} WHERE category = :cat")
            result = conn.execute(delete_sql, {"cat": category})
            conn.commit()
            logger.info(f"🧹 已清理 [{category}] 的旧数据 ({result.rowcount} 条)")

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