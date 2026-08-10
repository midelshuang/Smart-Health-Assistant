"""
测试营养成分检索功能
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.repositories.session import SessionLocal
from src.models.db_models import NutritionFact
from sqlalchemy import or_

db = SessionLocal()

try:
    print("=" * 60)
    print("营养成分检索测试")
    print("=" * 60)
    
    # 测试1: 按食物名称搜索
    test_cases = ["鸡蛋", "牛奶", "苹果", "米饭", "猪肉"]
    
    for keyword in test_cases:
        results = db.query(NutritionFact).filter(
            NutritionFact.food_name.like(f"%{keyword}%")
        ).limit(3).all()
        
        print(f"\n🔍 搜索 '{keyword}' (找到 {len(results)} 条):")
        for r in results:
            print(f"  - {r.food_name}")
            print(f"    热量: {r.calories_per_100g} kcal/100g")
            print(f"    蛋白质: {r.protein}g, 脂肪: {r.fat}g, 碳水: {r.carbs}g")
    
    # 测试2: 按分类搜索
    print("\n" + "=" * 60)
    print("按分类搜索测试")
    print("=" * 60)
    
    categories = ["鱼虾蟹贝类-鱼", "蔬菜类及其制品-嫩茎叶花菜类"]
    for cat in categories:
        count = db.query(NutritionFact).filter(
            NutritionFact.category == cat
        ).count()
        
        print(f"\n📁 分类 '{cat}': {count} 条")
        
        # 显示前3条
        samples = db.query(NutritionFact).filter(
            NutritionFact.category == cat
        ).limit(3).all()
        
        for r in samples:
            print(f"  - {r.food_name}: {r.calories_per_100g} kcal")
    
    # 测试3: 低热量食物
    print("\n" + "=" * 60)
    print("低热量食物（<50 kcal/100g）")
    print("=" * 60)
    
    low_cal = db.query(NutritionFact).filter(
        NutritionFact.calories_per_100g < 50,
        NutritionFact.calories_per_100g > 0
    ).limit(5).all()
    
    for r in low_cal:
        print(f"  - {r.food_name}: {r.calories_per_100g} kcal/100g")
    
    print("\n✅ 营养成分检索功能正常！")
    
finally:
    db.close()