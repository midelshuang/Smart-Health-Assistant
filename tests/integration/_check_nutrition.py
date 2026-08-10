import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.repositories.session import SessionLocal
from src.models.db_models import NutritionFact, GlycemicIndex
from sqlalchemy import func

db = SessionLocal()

try:
    # 统计营养成分表
    total_facts = db.query(func.count(NutritionFact.id)).scalar()
    categories = db.query(NutritionFact.category, func.count(NutritionFact.id)).group_by(NutritionFact.category).all()
    
    print("=" * 60)
    print("营养成分表统计")
    print("=" * 60)
    print(f"总记录数: {total_facts}")
    print(f"\n分类统计:")
    for cat, count in categories:
        print(f"  - {cat}: {count} 条")
    
    # 查看样本数据
    print("\n样本数据 (前5条):")
    samples = db.query(NutritionFact).limit(5).all()
    for s in samples:
        print(f"  - {s.food_name} ({s.category}): {s.calories_per_100g} kcal, 蛋白质 {s.protein}g")
    
    # 统计GI表
    gi_total = db.query(func.count(GlycemicIndex.id)).scalar()
    print(f"\n升糖指数表: {gi_total} 条")
    
finally:
    db.close()