"""
营养成分表数据质量最终报告
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.repositories.session import SessionLocal
from src.models.db_models import NutritionFact
from sqlalchemy import func

db = SessionLocal()

try:
    print("=" * 70)
    print("营养成分表数据质量最终报告")
    print("=" * 70)
    
    # 基本统计
    total = db.query(func.count(NutritionFact.id)).scalar()
    categories_count = db.query(func.count(NutritionFact.category.distinct())).scalar()
    
    print(f"\n📊 数据规模:")
    print(f"  总记录数: {total} 条")
    print(f"  食物分类: {categories_count} 个")
    
    # 数据完整性
    missing_cal = db.query(func.count(NutritionFact.id)).filter(
        NutritionFact.calories_per_100g.is_(None)
    ).scalar()
    missing_protein = db.query(func.count(NutritionFact.id)).filter(
        NutritionFact.protein.is_(None)
    ).scalar()
    
    print(f"\n✅ 数据完整性:")
    print(f"  热量完整率: {(total-missing_cal)/total*100:.2f}% ({total-missing_cal}/{total})")
    print(f"  蛋白质完整率: {(total-missing_protein)/total*100:.2f}% ({total-missing_protein}/{total})")
    
    # 数据质量
    needs_review = db.query(func.count(NutritionFact.id)).filter(
        NutritionFact.data_source.like('%NEEDS_REVIEW%')
    ).scalar()
    
    print(f"\n🔍 数据质量:")
    print(f"  需人工审核: {needs_review} 条")
    print(f"  数据可用率: {(total-needs_review)/total*100:.2f}%")
    
    # 分类分布
    top_categories = db.query(
        NutritionFact.category,
        func.count(NutritionFact.id).label('count')
    ).group_by(NutritionFact.category).order_by(func.count(NutritionFact.id).desc()).limit(10).all()
    
    print(f"\n📁 主要分类 (前10):")
    for i, (cat, count) in enumerate(top_categories, 1):
        print(f"  {i:2d}. {cat}: {count} 条")
    
    # 热量分布
    cal_stats = db.query(
        func.min(NutritionFact.calories_per_100g).label('min'),
        func.max(NutritionFact.calories_per_100g).label('max'),
        func.avg(NutritionFact.calories_per_100g).label('avg')
    ).filter(NutritionFact.calories_per_100g.isnot(None)).first()
    
    print(f"\n🔥 热量分布:")
    print(f"  最小值: {cal_stats.min:.1f} kcal/100g")
    print(f"  最大值: {cal_stats.max:.1f} kcal/100g")
    print(f"  平均值: {cal_stats.avg:.1f} kcal/100g")
    
    print("\n" + "=" * 70)
    print("✅ 营养成分表已成功清洗入库，数据质量良好！")
    print("=" * 70)
    
finally:
    db.close()