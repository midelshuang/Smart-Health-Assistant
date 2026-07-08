import os
import dotenv

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# print(base_dir)

# 规定文件路径（营养成分表）
Nutrition_file_path = os.path.join(base_dir, 'raw_data', 'china-food-composition-data', 'json_data')
# print(Nutrition_file_path)
# 指定GI文件路径
GI_file_path = os.path.join(base_dir, 'raw_data','china-food-composition-data', 'json_gi_of_foods','glycemic_index_of_foods.json')
# print(GI_file_path)

# 数据库连接字符串
dotenv.load_dotenv()
DB_CONNECTION_STRING = os.getenv("DATABASE_URL")

# 规定文件路径（原始数据）
Authoritative_file_path = os.path.join(base_dir, 'raw_data', 'raw')
# print(Authoritative_file_path)
