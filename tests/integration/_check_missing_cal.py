import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.repositories.session import SessionLocal
from src.models.db_models import NutritionFact
from sqlalchemy import func

db = SessionLocal()

try:
    # 查看缺失热量的记录
    missing_cal_records = db.query(NutritionFact).filter(
        NutritionFact.calories_per_100g.is_(None)
    ).limit(10).all()
    
    print("缺失热量的记录样本（前10条）:")
    for i, r in enumerate(missing_cal_records, 1):
        print(f"{i}. {r.food_name} ({r.category})")
        print(f"   P:{r.protein}, F:{r.fat}, C:{r.carbs}")
        print(f"   Details: {r.details}")
        print()
    
    # 检查这些记录是否有details字段包含热量信息
    print("\n检查details字段...")
    has_details = db.query(func.count(NutritionFact.id)).filter(
        NutritionFact.calories_per_100g.is_(None),
        NutritionFact.details.isnot(None)
    ).scalar()
    
    print(f"有details字段的缺失记录: {has_details} 条")
    
finally:
    db.close()