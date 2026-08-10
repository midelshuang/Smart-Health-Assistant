import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.repositories.session import SessionLocal
from src.models.db_models import NutritionFact

db = SessionLocal()

try:
    # 查询超高热量记录
    high_cal_records = db.query(NutritionFact).filter(
        NutritionFact.calories_per_100g > 900
    ).all()
    
    print("超高热量记录（>900 kcal/100g）:")
    for i, r in enumerate(high_cal_records, 1):
        print(f"\n{i}. {r.food_name} ({r.category})")
        print(f"   热量: {r.calories_per_100g} kcal/100g")
        print(f"   蛋白质: {r.protein}g")
        print(f"   脂肪: {r.fat}g")
        print(f"   碳水: {r.carbs}g")
        print(f"   详情: {r.details}")
    
    # 判断是否合理（纯脂肪类食物可达900kcal）
    print("\n分析：")
    for r in high_cal_records:
        fat_ratio = float(r.fat or 0) * 9 / float(r.calories_per_100g) * 100
        print(f"  {r.food_name}: 脂肪供能比 {fat_ratio:.1f}%")
        if fat_ratio > 90:
            print(f"    ✅ 合理（纯脂肪类食物）")
        else:
            print(f"    ⚠️  可能异常")
    
finally:
    db.close()