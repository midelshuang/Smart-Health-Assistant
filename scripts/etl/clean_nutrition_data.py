"""
营养成分数据清洗脚本
职责：处理缺失值、标准化食物名称、补充默认值

执行顺序（ETL 流水线）：
    1. clean_nutrition_data.py   ← 本脚本：清洗 + 补算热量（4P+9F+4C）
    2. fix_abnormal_data.py      ← 修正异常高热量（>900kcal）和异常宏量营养素（>100g）
    3. fill_missing_calories.py  ← 补充 fix 步骤清空后的热量缺失

注意：三脚本有依赖关系，必须按顺序执行。fix 步骤会清空异常值，
导致部分记录热量为 NULL，需要 fill 步骤重新计算。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.repositories.session import SessionLocal
from src.models.db_models import NutritionFact
from sqlalchemy import func, or_
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def clean_nutrition_data():
    """清洗营养成分数据"""
    db = SessionLocal()
    
    try:
        total = db.query(func.count(NutritionFact.id)).scalar()
        logger.info(f"开始清洗营养成分数据，总计 {total} 条记录")
        
        # 1. 处理缺失的热量值（根据蛋白质+脂肪+碳水计算）
        # 公式: 4*蛋白质 + 9*脂肪 + 4*碳水
        records_without_cal = db.query(NutritionFact).filter(
            NutritionFact.calories_per_100g.is_(None),
            NutritionFact.protein.isnot(None),
            NutritionFact.fat.isnot(None),
            NutritionFact.carbs.isnot(None)
        ).all()
        
        updated_count = 0
        for record in records_without_cal:
            # 计算热量: 4*P + 9*F + 4*C
            estimated_cal = (
                4 * float(record.protein or 0) +
                9 * float(record.fat or 0) +
                4 * float(record.carbs or 0)
            )
            record.calories_per_100g = round(estimated_cal, 2)
            updated_count += 1
            
        db.commit()
        logger.info(f"✅ 已计算并补充 {updated_count} 条热量数据")
        
        # 2. 处理缺失的宏量营养素（设为0）
        for nutrient in ['protein', 'fat', 'carbs']:
            result = db.query(func.count(NutritionFact.id)).filter(
                getattr(NutritionFact, nutrient).is_(None)
            ).scalar()
            
            if result > 0:
                db.query(NutritionFact).filter(
                    getattr(NutritionFact, nutrient).is_(None)
                ).update({getattr(NutritionFact, nutrient): 0})
                
                logger.info(f"✅ 已将 {result} 条缺失的{nutrient}值设为0")
        
        db.commit()
        
        # 3. 标准化食物名称（去除多余空格）
        duplicate_names = db.query(
            NutritionFact.food_name,
            func.count(NutritionFact.id).label('count')
        ).group_by(
            NutritionFact.food_name
        ).having(
            func.count(NutritionFact.id) > 1
        ).all()
        
        if duplicate_names:
            logger.info(f"发现 {len(duplicate_names)} 个重复食物名称，保留最新记录")
            for name, count in duplicate_names:
                # 保留最新的一条，删除旧记录
                records = db.query(NutritionFact).filter(
                    NutritionFact.food_name == name
                ).order_by(NutritionFact.created_at.desc()).all()
                
                if len(records) > 1:
                    for record in records[1:]:
                        db.delete(record)
            
            db.commit()
            logger.info(f"✅ 已清理重复食物名称")
        
        # 4. 最终统计
        final_total = db.query(func.count(NutritionFact.id)).scalar()
        missing_cal = db.query(func.count(NutritionFact.id)).filter(
            NutritionFact.calories_per_100g.is_(None)
        ).scalar()
        
        logger.info("=" * 60)
        logger.info("数据清洗完成")
        logger.info(f"最终记录数: {final_total}")
        logger.info(f"仍缺失热量: {missing_cal} 条 ({missing_cal/final_total*100:.1f}%)")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"清洗过程出错: {e}")
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    clean_nutrition_data()