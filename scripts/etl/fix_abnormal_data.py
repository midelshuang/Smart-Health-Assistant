"""
修正异常的营养成分数据
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.repositories.session import SessionLocal
from src.models.db_models import NutritionFact
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

db = SessionLocal()

try:
    # 查询异常记录
    abnormal_records = db.query(NutritionFact).filter(
        NutritionFact.calories_per_100g > 900
    ).all()
    
    logger.info(f"发现 {len(abnormal_records)} 条异常高热量记录")
    
    for record in abnormal_records:
        logger.info(f"\n处理: {record.food_name}")
        logger.info(f"  原始值: 热量{record.calories_per_100g}kcal, P{record.protein}g, F{record.fat}g, C{record.carbs}g")
        
        # 判断异常原因并修正
        if record.protein and float(record.protein) > 100:
            # 蛋白质异常高（可能是数据源单位错误）
            logger.info(f"  ⚠️  蛋白质值异常: {record.protein}g/100g")
            # 标记为需要人工审核
            record.data_source = f"{record.data_source}_NEEDS_REVIEW"
            # 将异常值设为null
            record.protein = None
            record.calories_per_100g = None
            logger.info(f"  ✅ 已标记为需要人工审核并清空异常值")
            
        elif record.fat and float(record.fat) > 100:
            # 脂肪异常高（可能是数据源单位错误）
            logger.info(f"  ⚠️  脂肪值异常: {record.fat}g/100g")
            record.data_source = f"{record.data_source}_NEEDS_REVIEW"
            record.fat = None
            record.calories_per_100g = None
            logger.info(f"  ✅ 已标记为需要人工审核并清空异常值")
    
    db.commit()
    
    # 统计最终结果
    from sqlalchemy import func
    total = db.query(func.count(NutritionFact.id)).scalar()
    missing_cal = db.query(func.count(NutritionFact.id)).filter(
        NutritionFact.calories_per_100g.is_(None)
    ).scalar()
    
    logger.info("\n" + "=" * 60)
    logger.info("异常数据修正完成")
    logger.info(f"总记录数: {total}")
    logger.info(f"缺失热量: {missing_cal} 条（包含需要人工审核的记录）")
    logger.info(f"数据完整率: {(total-missing_cal)/total*100:.1f}%")
    logger.info("=" * 60)
    
except Exception as e:
    logger.error(f"修正过程出错: {e}")
    db.rollback()
    raise
finally:
    db.close()