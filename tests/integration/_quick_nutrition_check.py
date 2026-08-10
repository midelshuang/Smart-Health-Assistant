import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.repositories.session import SessionLocal
from src.models.db_models import NutritionFact
from sqlalchemy import func

db = SessionLocal()

try:
    total = db.query(func.count(NutritionFact.id)).scalar()
    categories_count = db.query(func.count(NutritionFact.category.distinct())).scalar()
    
    print(f"✅ 营养成分表已存在")
    print(f"   总记录数: {total} 条")
    print(f"   食物分类: {categories_count} 个")
    
    # 显示前10个分类
    categories = db.query(NutritionFact.category, func.count(NutritionFact.id).label('count')) \
        .group_by(NutritionFact.category) \
        .order_by(func.count(NutritionFact.id).desc()) \
        .limit(10) \
        .all()
    
    print("\n   前10大分类:")
    for cat, count in categories:
        if cat:
            print(f"   - {cat}: {count} 条")
    
finally:
    db.close()