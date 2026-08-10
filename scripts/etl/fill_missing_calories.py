"""
补充剩余缺失的热量数据
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.repositories.session import SessionLocal
from src.models.db_models import NutritionFact
from sqlalchemy import func
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

db = SessionLocal()

try:
    # 查找所有缺失热量的记录
    missing_cal_records = db.query(NutritionFact).filter(
        NutritionFact.calories_per_100g.is_(None)
    ).all()
    
    logger.info(f"找到 {len(missing_cal_records)} 条缺失热量的记录")
    
    updated = 0
    for record in missing_cal_records:
        # 根据宏量营养素计算热量
        # 公式: 4*蛋白质 + 9*脂肪 + 4*碳水
        if record.protein is not None or record.fat is not None or record.carbs is not None:
            p = float(record.protein or 0)
            f = float(record.fat or 0)
            c = float(record.carbs or 0)
            
            estimated_cal = 4 * p + 9 * f + 4 * c
            record.calories_per_100g = round(estimated_cal, 2)
            updated += 1
            logger.debug(f"{record.food_name}: {estimated_cal} kcal (P={p}, F={f}, C={c})")
    
    db.commit()
    logger.info(f"✅ 已补充 {updated} 条热量数据")
    
    # 验证
    final_missing = db.query(func.count(NutritionFact.id)).filter(
        NutritionFact.calories_per_100g.is_(None)
    ).scalar()
    
    total = db.query(func.count(NutritionFact.id)).scalar()
    
    logger.info("=" * 60)
    logger.info("数据补充完成")
    logger.info(f"总记录数: {total}")
    logger.info(f"仍缺失热量: {final_missing} 条")
    logger.info(f"数据完整率: {(total-final_missing)/total*100:.1f}%")
    logger.info("=" * 60)
    
except Exception as e:
    logger.error(f"补充过程出错: {e}")
    db.rollback()
    raise
finally:
    db.close()