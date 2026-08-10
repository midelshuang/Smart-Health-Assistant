import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.repositories.session import SessionLocal
from src.models.db_models import NutritionFact
from sqlalchemy import func, or_

db = SessionLocal()

try:
    total = db.query(func.count(NutritionFact.id)).scalar()
    
    # 检查数据完整性
    missing_calories = db.query(func.count(NutritionFact.id)).filter(
        NutritionFact.calories_per_100g.is_(None)
    ).scalar()
    
    missing_protein = db.query(func.count(NutritionFact.id)).filter(
        NutritionFact.protein.is_(None)
    ).scalar()
    
    missing_fat = db.query(func.count(NutritionFact.id)).filter(
        NutritionFact.fat.is_(None)
    ).scalar()
    
    missing_carbs = db.query(func.count(NutritionFact.id)).filter(
        NutritionFact.carbs.is_(None)
    ).scalar()
    
    print("=" * 60)
    print("营养成分表数据质量检查")
    print("=" * 60)
    print(f"总记录数: {total}")
    print(f"\n缺失值统计:")
    print(f"  - 热量缺失: {missing_calories} 条 ({missing_calories/total*100:.1f}%)")
    print(f"  - 蛋白质缺失: {missing_protein} 条 ({missing_protein/total*100:.1f}%)")
    print(f"  - 脂肪缺失: {missing_fat} 条 ({missing_fat/total*100:.1f}%)")
    print(f"  - 碳水缺失: {missing_carbs} 条 ({missing_carbs/total*100:.1f}%)")
    
    # 检查异常值
    high_cal = db.query(func.count(NutritionFact.id)).filter(
        NutritionFact.calories_per_100g > 900
    ).scalar()
    
    negative_values = db.query(func.count(NutritionFact.id)).filter(
        or_(
            NutritionFact.calories_per_100g < 0,
            NutritionFact.protein < 0,
            NutritionFact.fat < 0,
            NutritionFact.carbs < 0
        )
    ).scalar()
    
    print(f"\n异常值统计:")
    print(f"  - 超高热量(>900kcal): {high_cal} 条")
    print(f"  - 负值记录: {negative_values} 条")
    
    # 查看样本数据
    print("\n样本数据 (前5条):")
    samples = db.query(NutritionFact).limit(5).all()
    for s in samples:
        print(f"  - {s.food_name}: {s.calories_per_100g}kcal, P{s.protein}g, F{s.fat}g, C{s.carbs}g")
    
    print(f"\n✅ 数据库状态: {'良好' if missing_calories + negative_values == 0 else '需要清洗'}")
    
finally:
    db.close()