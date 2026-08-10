"""
知识库种子数据导入脚本 - ChromaDB 兼容版

用 monkey-patch 绕过 chromadb 1.5.x HttpClient 的 auth 检查
"""
import os
import sys
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from src.config import settings
from src.utils.logger import setup_logger

logger = setup_logger(name="SeedKnowledgeBase")

# ============================================================
# Monkey-patch: 绕过 chromadb 1.5.x 的 auth identity 检查
# ============================================================
import chromadb.api.client as _client_module
from chromadb.api.client import UserIdentity

_orig_get_user_identity = _client_module.Client.get_user_identity

def _safe_get_user_identity(self):
    try:
        return _orig_get_user_identity(self)
    except Exception as e:
        logger.warning(f"chromadb auth 检查失败 (服务器未启用认证)，使用匿名身份: {e}")
        return UserIdentity(subject=None, tenants=[], databases=[])

_client_module.Client.get_user_identity = _safe_get_user_identity
logger.info("已 patch chromadb Client.get_user_identity")

# ============================================================
# 知识库种子数据
# ============================================================
SEED_DOCUMENTS = [
    {
        "title": "中国居民膳食指南（2022）核心要点",
        "source_type": "guideline",
        "media_type": "text",
        "summary": "中国营养学会发布的权威膳食指南，涵盖平衡膳食八条准则",
        "content": """中国居民膳食指南（2022）核心准则：
一、食物多样，合理搭配。每天膳食应包括谷薯类、蔬菜水果、畜禽鱼蛋奶和豆类食物。平均每天摄入12种以上食物，每周25种以上。
二、吃动平衡，健康体重。每周至少进行5天中等强度身体活动，累计150分钟以上。每天步行6000步以上。
三、多吃蔬果、奶类、全谷、大豆。餐餐有蔬菜，保证每天摄入不少于300克新鲜蔬菜，深色蔬菜应占一半。天天吃水果，保证每天摄入200-350克新鲜水果。
四、适量吃鱼、禽、蛋、瘦肉。每周最好吃鱼2次或300-500克。蛋类40-50克/天。畜禽肉300-500克/周。
五、少盐少油，控糖限酒。成人每天食盐摄入量不超过5克。烹调油25-30克。添加糖不超过50克，最好控制在25克以下。
六、规律进餐，足量饮水。合理安排一日三餐，定时定量。每天饮水1500-1700毫升（7-8杯）。
七、会烹会选，会看标签。学会阅读食品标签，选择健康食品。
八、公筷分餐，杜绝浪费。讲究卫生，分餐有益预防疾病传播。""",
    },
    {
        "title": "成人肥胖食养指南（2024年版）",
        "source_type": "guideline",
        "media_type": "text",
        "summary": "国家卫健委发布的肥胖食养指南，提供减重膳食原则和食谱示例",
        "content": """成人肥胖食养指南（2024年版）要点：
1. 控制总能量摄入。建议每日能量摄入男性1200-1500 kcal，女性1000-1200 kcal，或在目前能量摄入基础上减少500-700 kcal。
2. 调整宏量营养素比例。蛋白质供能比15-20%，脂肪供能比20-30%，碳水化合物供能比50-60%。
3. 增加全谷物摄入。主食中全谷物至少占一半，用糙米、燕麦、荞麦等替代精白米面。
4. 保证蔬菜水果摄入。每天蔬菜300-500克，水果200-350克，优先选择低GI品种。
5. 适量优质蛋白。选择鱼虾、去皮禽肉、瘦肉、蛋类、奶类和豆制品。
6. 限制高能量食物。避免油炸食品、含糖饮料、甜食、高油高盐零食。
7. 饮食规律。三餐定时定量，不暴饮暴食，晚餐不宜过晚。
8. 食养方案示例（成人减重期）：
   早餐：全麦面包1片+鸡蛋1个+牛奶250ml+小番茄5个
   午餐：杂粮饭1小碗+清蒸鱼100g+西兰花200g+凉拌豆腐100g
   晚餐：燕麦粥1碗+鸡胸肉80g+混合蔬菜沙拉200g""",
    },
    {
        "title": "糖尿病医学营养治疗指南（2022）",
        "source_type": "guideline",
        "media_type": "text",
        "summary": "中华医学会糖尿病学分会发布的糖尿病营养治疗指南",
        "content": """糖尿病医学营养治疗指南要点：
1. 能量摄入。维持理想体重，超重/肥胖患者减少能量摄入300-500 kcal/天。
2. 碳水化合物。占总能量45-60%，选择低GI食物（GI<55），如全谷物、豆类、非淀粉类蔬菜。
3. 蛋白质。占总能量15-20%，约1.0 g/kg/d。糖尿病肾病者适当限制至0.8 g/kg/d。
4. 脂肪。占总能量20-30%，饱和脂肪<7%，减少反式脂肪摄入。适量摄入n-3多不饱和脂肪酸。
5. 膳食纤维。每日25-30克，多吃蔬菜、全谷物和豆类。
6. 餐次安排。定时定量进餐，少食多餐有利于血糖控制。早餐、午餐、晚餐能量比建议3:4:3。
7. 低GI食物推荐：燕麦(GI=55)、糙米(GI=50)、扁豆(GI=29)、苹果(GI=36)、牛奶(GI=27)。
8. 高GI食物限制：白米饭(GI=83)、白面包(GI=75)、西瓜(GI=72)、土豆泥(GI=87)。
9. 饮食禁忌：避免含糖饮料、精制糖、糕点。限制饮酒。""",
    },
    {
        "title": "高血压患者膳食指导（WS/T 430-2013）",
        "source_type": "guideline",
        "media_type": "text",
        "summary": "国家卫生标准中高血压患者膳食指导原则",
        "content": """高血压患者膳食指导原则：
1. 限盐。每日食盐摄入量<5克，减少酱油、味精、咸菜等隐性盐摄入。
2. 增加钾摄入。多吃富含钾的食物：菠菜、土豆、香蕉、橙子、豆类。每日钾摄入量3500mg以上。
3. 限制脂肪。总脂肪供能比<30%，饱和脂肪<7%。选择植物油，少吃动物脂肪。
4. 适量蛋白质。蛋白质供能比15-20%，优先选择鱼类、禽肉、豆制品。
5. 多吃蔬菜水果。每日蔬菜500克以上，水果200-400克。
6. 全谷物。主食中全谷物占1/3以上。
7. DASH饮食模式推荐：富含水果、蔬菜、全谷物、低脂奶制品、鱼、禽、豆类和坚果。减少红肉、甜食和含糖饮料。
8. 限制饮酒。男性每日酒精<25克，女性<15克。""",
    },
    {
        "title": "减重饮食的常见误区与科学方法",
        "source_type": "expert_media",
        "media_type": "text",
        "summary": "营养科专家总结的减重饮食常见误区及正确做法",
        "content": """减重饮食常见误区与科学方法：
误区一：不吃主食可以减肥。
真相：长期不吃主食会导致酮症、肌肉流失、代谢下降。正确做法是减少精制碳水，改为全谷物，控制总量。
误区二：只吃水果可以减肥。
真相：水果含果糖，过量摄入同样转化为脂肪。且蛋白质摄入不足会导致肌肉流失。每天水果200-350克为宜。
误区三：节食越狠减肥越快。
真相：过度节食使基础代谢下降，恢复正常饮食后容易反弹。建议每日能量赤字500 kcal左右，每周减重0.5-1公斤。
误区四：高蛋白饮食伤肾。
真相：对健康成年人，每日1.2-1.6g/kg蛋白质摄入是安全的。有肾病基础疾病者需遵医嘱。
科学方法：
1. 阶梯式减少能量摄入，不急于求成。
2. 提高蛋白质比例至20-25%，增加饱腹感，保护肌肉。
3. 选择低GI主食，稳定血糖，减少饥饿感。
4. 每餐先吃蔬菜和蛋白质，最后吃主食。
5. 每周3-5次有氧运动+2-3次抗阻训练。
6. 保证睡眠7-8小时，睡眠不足会增加食欲激素（胃饥饿素）分泌。""",
    },
    {
        "title": "运动营养补充指南",
        "source_type": "expert_media",
        "media_type": "text",
        "summary": "运动营养专家关于运动前后饮食补充的建议",
        "content": """运动营养补充指南：
1. 运动前（1-2小时）：
   - 补充碳水50-100克，如全麦面包、香蕉、燕麦。
   - 适量蛋白质20-30克，如鸡蛋、牛奶。
   - 避免高脂肪、高纤维食物，防止肠胃不适。
2. 运动中：
   - 有氧运动超过60分钟，每15-20分钟补充150-200ml水或运动饮料。
   - 长时间运动需补充电解质（钠、钾）。
3. 运动后（30分钟内）：
   - 碳水蛋白比例3:1-4:1，如牛奶+香蕉、蛋白粉+燕麦。
   - 蛋白质20-40克，促进肌肉修复。
   - 碳水化合物1-1.2g/kg体重，补充糖原储备。
4. 力量训练营养：
   - 训练前补充5g肌酸（可选），提升力量表现。
   - 训练日蛋白质总摄入1.6-2.0 g/kg。
   - 训练后24小时内分次补充蛋白质，每餐20-40克。
5. 有氧运动营养：
   - 中低强度有氧30-60分钟，空腹有氧可促进脂肪氧化，但不宜时间过长。
   - 高强度间歇训练（HIIT）前需充分补充碳水。""",
    },
    {
        "title": "蛋白质的吸收与利用",
        "source_type": "expert_media",
        "media_type": "text",
        "summary": "关于蛋白质消化吸收率、生物价和日常蛋白质来源的科普",
        "content": """蛋白质的吸收与利用：
1. 优质蛋白质来源及生物价（BV）：
   - 鸡蛋（BV=100）：最优质的蛋白质，氨基酸组成最接近人体需要。
   - 牛奶（BV=91）：含酪蛋白和乳清蛋白，吸收率高。
   - 鱼肉（BV=83）：蛋白质含量15-25%，富含n-3脂肪酸。
   - 鸡胸肉（BV=79）：蛋白质含量23%，脂肪低，健身首选。
   - 牛肉（BV=80）：富含肌酸和铁元素。
   - 大豆（BV=74）：唯一优质的植物蛋白，含异黄酮。
2. 蛋白质消化吸收率：
   - 动物蛋白平均吸收率90%以上。
   - 植物蛋白平均吸收率60-80%（含抗营养因子）。
   - 加工（如发酵、研磨）可提高植物蛋白吸收率。
3. 日常蛋白质需求：
   - 普通成人：0.8-1.0 g/kg/天。
   - 运动人群：1.2-2.0 g/kg/天。
   - 减脂期：1.5-2.0 g/kg/天（防止肌肉流失）。
4. 蛋白质互补：
   - 谷类（缺赖氨酸）+ 豆类（富含赖氨酸）可提高蛋白质利用率。
   - 混合食用多种蛋白质来源，氨基酸互补效果好。""",
    },
    {
        "title": "膳食纤维与健康",
        "source_type": "expert_media",
        "media_type": "text",
        "summary": "膳食纤维的分类、生理功能、推荐摄入量和食物来源",
        "content": """膳食纤维与健康：
1. 分类：
   - 可溶性纤维：果胶、树胶、β-葡聚糖。溶于水形成凝胶，降低血糖和胆固醇。
   - 不可溶性纤维：纤维素、木质素。增加粪便体积，促进肠道蠕动。
2. 生理功能：
   - 降低餐后血糖峰值（延缓糖吸收）。
   - 降低血清胆固醇（结合胆汁酸排出）。
   - 增加饱腹感，有助于控制体重。
   - 促进肠道有益菌生长（益生元作用）。
   - 预防便秘和结肠癌。
3. 推荐摄入量：成人25-30克/天。
4. 富含膳食纤维的食物（每100克）：
   - 燕麦片：10克
   - 红薯：3克
   - 西兰花：3克
   - 苹果（带皮）：2.4克
   - 全麦面包：7克
   - 黑豆：16克
   - 亚麻籽：28克
5. 增加纤维摄入的实用建议：
   - 主食中增加全谷物比例。
   - 每天至少500克蔬菜。
   - 水果带皮吃。
   - 逐渐增加纤维摄入量，同时增加饮水量。""",
    },
    {
        "title": "儿童青少年肥胖食养指南（2025年版）",
        "source_type": "guideline",
        "media_type": "text",
        "summary": "国家卫健委发布的儿童青少年肥胖食养指南，针对2-18岁儿童青少年",
        "content": """儿童青少年肥胖食养指南（2025年版）核心要点：
一、能量与营养目标
1. 2-5岁儿童：每日能量摄入按年龄推荐量，肥胖儿童减少100-200 kcal/天。
2. 6-12岁儿童：每日能量摄入减少200-300 kcal/天。
3. 13-18岁青少年：每日能量摄入减少300-500 kcal/天。
4. 蛋白质供能比15-20%，脂肪供能比20-30%，碳水化合物供能比50-60%。
5. 保证钙、铁、锌、维生素D等微量营养素充足摄入。

二、膳食原则
1. 食物多样，合理搭配。每天摄入12种以上食物，每周25种以上。
2. 规律三餐，不省略早餐。早餐占全天能量25-30%。
3. 增加全谷物和杂豆。主食中全谷物至少占1/3，如燕麦、糙米、玉米。
4. 每天吃蔬菜300-500克，其中深色蔬菜占一半以上。
5. 每天吃水果200-350克，选择低GI水果（苹果、梨、橙子、猕猴桃）。
6. 适量优质蛋白：鱼虾类每周2-3次，禽肉每天50-75g，蛋类每天1个，奶类每天300-500ml。
7. 限制高糖、高油、高盐食物。不喝或少喝含糖饮料，不吃油炸食品。
8. 培养健康饮食习惯：不偏食、不挑食、不暴饮暴食，学会看食品营养标签。

三、分年龄段食谱示例
【6-12岁儿童减重食谱】
早餐：燕麦粥1碗+煮鸡蛋1个+牛奶250ml+凉拌黄瓜50g
午餐：糙米饭1小碗+清蒸鲈鱼80g+蒜蓉西兰花150g+番茄豆腐汤1碗
晚餐：杂粮馒头1个+鸡胸肉丁炒蔬菜（100g肉+150g蔬菜）+小米粥1碗
加餐：苹果1个 或 无糖酸奶100ml

【13-18岁青少年减重食谱】
早餐：全麦面包2片+煎鸡蛋1个+无糖豆浆300ml+凉拌生菜100g
午餐：杂粮饭1小碗+青椒牛柳（80g牛肉+100g青椒）+蒜蓉油麦菜200g+紫菜蛋花汤1碗
晚餐：荞麦面1碗+番茄鸡胸肉（100g鸡胸+150g番茄）+凉拌菠菜100g
加餐：猕猴桃1个 或 原味坚果15g

四、行为干预要点
1. 减少久坐时间：每坐1小时起身活动5-10分钟。
2. 屏幕时间限制：每天不超过2小时。
3. 每天运动60分钟以上：快走、慢跑、游泳、跳绳、球类运动。
4. 家庭共餐：营造健康饮食氛围，家长以身作则。
5. 不使用食物作为奖励或惩罚。""",
    },
    {
        "title": "儿童青少年膳食指南（2022）",
        "source_type": "guideline",
        "media_type": "text",
        "summary": "中国营养学会发布的儿童青少年膳食指南，指导2-18岁儿童科学饮食",
        "content": """儿童青少年膳食指南（2022）核心建议：
一、2-5岁学龄前儿童
1. 食物多样，合理搭配。每天摄入10种以上食物。
2. 单独制作，注重色香味形。食物细软易消化，避免过咸过甜。
3. 规律进餐，培养好习惯。三餐两点，定时定量。
4. 每天饮奶，足量饮水。每日奶及奶制品350-500ml。
5. 正确选择零食。选择新鲜、天然、易消化的食物作为零食。
6. 少喝含糖饮料，不饮酒。

二、6-10岁学龄儿童
1. 食物多样，保证营养。每天摄入12种以上食物，每周25种以上。
2. 多吃蔬果、奶类、全谷、大豆。蔬菜300-400g/天，水果200-300g/天。
3. 适量吃鱼、禽、蛋、瘦肉。每周吃鱼2次以上。
4. 少盐少油，控糖限酒。每日食盐不超过5g。
5. 规律进餐，足量饮水。每天饮水800-1200ml。
6. 会烹会选，会看标签。开始学习阅读食品标签。

三、11-18岁青少年
1. 食物多样，合理搭配。每天摄入12种以上食物，每周25种以上。
2. 多吃蔬果、奶类、全谷、大豆。蔬菜400-500g/天，水果300-350g/天。
3. 适量吃鱼、禽、蛋、瘦肉。每日畜禽肉50-75g，水产品40-75g。
4. 少盐少油，控糖限酒。每日食盐不超过5g，烹调油25-30g。
5. 规律进餐，足量饮水。三餐定时定量，每天饮水1500-1700ml。
6. 会烹会选，会看标签。学会自己选购和制作食物。
7. 不饮酒，避免咖啡因摄入过多。

四、儿童青少年常见营养问题
1. 钙缺乏：影响骨骼发育。来源：牛奶、酸奶、豆腐、虾皮、绿叶菜。
2. 铁缺乏：导致贫血，影响学习能力。来源：红肉、动物肝脏、蛋黄。
3. 维生素D缺乏：影响钙吸收。来源：日晒、强化乳制品。
4. 膳食纤维不足：影响肠道健康。来源：全谷物、蔬菜、水果。""",
    },
    {
        "title": "孕期和哺乳期妇女膳食指南",
        "source_type": "guideline",
        "media_type": "text",
        "summary": "中国营养学会发布的孕期及哺乳期妇女膳食指南",
        "content": """孕期和哺乳期妇女膳食指南要点：
一、孕期膳食原则
1. 补充叶酸，常吃含铁丰富的食物，选用碘盐。
   - 孕前3个月至孕早期3个月，每天补充400μg叶酸。
   - 每周至少吃1-2次动物肝脏。
   - 使用碘盐，每周至少吃1次海产品。
2. 孕中晚期适量增加奶、鱼、禽、蛋、瘦肉的摄入。
   - 孕中期：每天增加200kcal，奶量500ml。
   - 孕晚期：每天增加450kcal，奶量500ml。
   - 每周至少吃鱼2次。
3. 适量身体活动，维持孕期适宜增重。
   - 每周至少150分钟中等强度有氧活动。
   - 体重增长：11-16kg（BMI正常）。
4. 烟酒和成瘾药物对胎儿有害，孕期应避免。

二、哺乳期膳食原则
1. 增加优质蛋白和钙的摄入。
   - 每天蛋白质摄入量增加20g。
   - 每天钙摄入量1200mg，奶类500ml。
2. 保证充足水分摄入。
   - 每天饮水2000-2500ml。
   - 每餐保证有汤，但不要太油太咸。
3. 食物多样，不过度节食。
   - 每天摄入12种以上食物，每周25种以上。
   - 为保证母乳质量，不建议节食减肥。
4. 继续补充叶酸、钙、铁、碘等营养素。
5. 避免吸烟、饮酒，限制咖啡因摄入。

三、孕期关键营养
1. DHA：每周至少吃2次深海鱼。
2. 膳食纤维：预防便秘。来源：全谷物、蔬菜、水果。
3. 控盐：每日食盐不超过5g，预防妊娠高血压。""",
    },
    {
        "title": "老年人膳食指南（2022）",
        "source_type": "guideline",
        "media_type": "text",
        "summary": "中国营养学会发布的老年人膳食指南，指导65岁以上老人科学饮食",
        "content": """老年人膳食指南（2022）核心要点：
一、食物多样，合理搭配
1. 每天摄入食物不少于12种，每周不少于25种。
2. 多吃蔬果、奶类、全谷、大豆。
   - 蔬菜400-500g/天，其中深色蔬菜占一半。
   - 水果200-350g/天。
   - 奶及奶制品350-400ml/天。
3. 适量吃鱼、禽、蛋、瘦肉。
   - 每天畜禽肉40-75g，水产品40-75g，蛋类40-50g。
   - 优先选择鱼虾、去皮禽肉、瘦牛羊肉。

二、重点关注
1. 补充优质蛋白：老年人蛋白质合成能力下降，需要更多优质蛋白。
   - 每天蛋白质摄入量1.0-1.2g/kg体重。
   - 每餐均匀分配蛋白质（每餐20-30g），有利于吸收。
2. 预防肌少症：
   - 足量蛋白质+抗阻运动（每周2-3次）。
   - 维生素D辅助钙吸收。
3. 控盐控油：
   - 每日食盐不超过5g。
   - 烹调油25-30g，多选植物油。
4. 少食多餐，适量加餐：
   - 三餐之外可加2-3次小餐。
   - 加餐选择：牛奶、酸奶、坚果、水果。
5. 充足饮水：
   - 每天饮水1500-1700ml。
   - 主动饮水，不要等到口渴。

三、老年人推荐食物
1. 优质蛋白来源：鸡蛋、牛奶、鱼肉、鸡肉、瘦牛肉、豆腐。
2. 钙来源：牛奶、酸奶、豆腐、虾皮、绿叶蔬菜。
3. 维生素D来源：蛋黄、强化乳制品、日晒。
4. 膳食纤维来源：燕麦、糙米、红薯、西兰花、苹果。""",
    },
    {
        "title": "江南地区（南京）四季饮食养生指南",
        "source_type": "expert_media",
        "media_type": "text",
        "summary": "针对南京地区气候特点的四季饮食养生建议，结合当地食材",
        "content": """江南地区（南京）四季饮食养生指南：
南京地处长江下游，属亚热带季风气候，四季分明，夏季炎热潮湿，冬季湿冷。饮食应顺应时令，选用当地食材。

一、春季（3-5月）
特点：春湿较重，宜健脾祛湿
1. 推荐食材：
   - 春菜：马兰头、荠菜、香椿头、蚕豆、春笋、蒜苗
   - 河鲜：刀鱼、鲥鱼、河虾、螺蛳
   - 祛湿食材：薏米、赤小豆、芡实、山药
2. 推荐做法：
   - 马兰头拌香干、荠菜馄饨、香椿炒鸡蛋
   - 春笋烧肉、蚕豆烧米、蒜苗炒肉丝
   - 薏米赤小豆粥、山药排骨汤
3. 养生要点：少酸多甘，养肝健脾，防春困

二、夏季（6-8月）
特点：炎热潮湿，宜清热解暑
1. 推荐食材：
   - 夏令蔬菜：冬瓜、丝瓜、苦瓜、黄瓜、番茄、空心菜
   - 水产：小龙虾、螃蟹、鲈鱼、鳝鱼
   - 解暑：绿豆、莲子、百合、荷叶
2. 推荐做法：
   - 冬瓜排骨汤、丝瓜尖拌菜、苦瓜酿肉
   - 绿豆汤、莲子百合粥、荷叶饭
   - 盐水鸭、水晶肴肉（南京传统美食）
3. 养生要点：清淡少油，多汤粥，注意食品卫生

三、秋季（9-11月）
特点：秋燥明显，宜润肺养阴
1. 推荐食材：
   - 秋菜：莲藕、南瓜、板栗、白果、百合
   - 水产：大闸蟹、桂花鱼、甲鱼
   - 润肺：梨、银耳、蜂蜜、柚子
2. 推荐做法：
   - 莲藕排骨汤、南瓜蒸蛋、板栗烧鸡
   - 银耳莲子羹、百合粥、雪梨汤
   - 桂花糖芋苗、盐水鸭（秋季桂花鸭）
3. 养生要点：少辛多酸，润肺防燥，贴秋膘要适量

四、冬季（12-2月）
特点：湿冷严寒，宜温补暖胃
1. 推荐食材：
   - 冬菜：大白菜、萝卜、菠菜、冬笋
   - 温补：羊肉、牛肉、桂圆、红枣、枸杞
   - 南京特产：板鸭、香肠
2. 推荐做法：
   - 萝卜烧肉、大白菜炖豆腐、菠菜蛋花汤
   - 羊肉萝卜汤、红枣桂圆粥
   - 南京板鸭、香肠蒸饭
3. 养生要点：温热进补，少量多餐，适当运动

五、南京特色饮食文化
1. 南京菜以清淡、鲜香、注重原汁原味著称。
2. 传统名菜：盐水鸭、金陵丸子、松鼠桂鱼、蛋饺。
3. 小吃：鸭血粉丝汤、尹氏鸡汤馄饨、赤豆元宵、牛肉锅贴。
4. 肥胖人群选择建议：
   - 盐水鸭去皮食用，控制分量（50-75g）。
   - 鸭血粉丝汤少加油条和鸭肝，多吃鸭血和蔬菜。
   - 牛肉锅贴可以吃，但要控制数量（3-4个）。""",
    },
]


def seed_postgresql():
    """向 PostgreSQL authoritative_sources 表写入数据"""
    import psycopg2

    db_host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    db_port = os.getenv("POSTGRES_PORT", "5432")
    db_name = os.getenv("POSTGRES_DB", "healthai_db")
    db_user = os.getenv("POSTGRES_USER", "postgres")
    db_pass = os.getenv("POSTGRES_PASSWORD", "postgres")

    conn = psycopg2.connect(host=db_host, port=db_port, dbname=db_name, user=db_user, password=db_pass)
    cur = conn.cursor()

    cur.execute("DELETE FROM authoritative_sources")
    logger.info("已清空 authoritative_sources 旧数据")

    for doc in SEED_DOCUMENTS:
        cur.execute(
            """INSERT INTO authoritative_sources (title, source_type, media_type, summary, transcript)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (doc["title"], doc["source_type"], doc["media_type"], doc["summary"], doc["content"]),
        )
        doc_id = cur.fetchone()[0]
        doc["db_id"] = doc_id
        logger.info(f"  写入: [{doc_id}] {doc['title']} ({doc['source_type']})")

    conn.commit()
    conn.close()
    logger.info(f"PostgreSQL 写入完成，共 {len(SEED_DOCUMENTS)} 条")


# ============================================================
# 文档元数据映射（用于 age_group 和 region 标注）
# ============================================================
GUIDE_METADATA = {
    "中国居民膳食指南": {"age_group": "youth,middle_aged,elderly", "region": "全国"},
    "成人肥胖食养指南": {"age_group": "youth,middle_aged", "region": "全国"},
    "糖尿病医学营养治疗指南": {"age_group": "middle_aged,elderly", "region": "全国"},
    "高血压患者膳食指导": {"age_group": "middle_aged,elderly", "region": "全国"},
    "减重饮食的常见误区与科学方法": {"age_group": "youth,middle_aged", "region": "全国"},
    "运动营养补充指南": {"age_group": "youth,adolescent", "region": "全国"},
    "蛋白质的吸收与利用": {"age_group": "youth,middle_aged", "region": "全国"},
    "膳食纤维与健康": {"age_group": "youth,middle_aged,elderly", "region": "全国"},
    "儿童青少年肥胖食养指南": {"age_group": "adolescent,child", "region": "全国"},
    "儿童青少年膳食指南": {"age_group": "adolescent,child,preschool", "region": "全国"},
    "孕期和哺乳期妇女膳食指南": {"age_group": "youth", "region": "全国"},
    "老年人膳食指南": {"age_group": "elderly,middle_aged_plus", "region": "全国"},
    "江南地区": {"age_group": "youth,middle_aged,elderly", "region": "华东"},
}


def _infer_metadata(title: str) -> dict:
    """根据标题推断 age_group 和 region"""
    age_groups = set()
    regions = set()

    for key, meta in GUIDE_METADATA.items():
        if key in title:
            for ag in meta["age_group"].split(","):
                age_groups.add(ag.strip())
            regions.add(meta["region"])

    if not age_groups:
        age_groups.add("youth,middle_aged")
    if not regions:
        regions.add("全国")

    return {
        "age_group": ",".join(sorted(age_groups)),
        "region": ",".join(sorted(regions)),
    }


def seed_chromadb():
    """向 ChromaDB 向量库写入 embedding 数据"""
    import chromadb
    from chromadb.config import Settings
    from src.core.embedding import get_embedding_function

    chroma_host = os.getenv("CHROMA_HOST", "localhost")
    chroma_port = int(os.getenv("CHROMA_PORT", "8001"))
    collection_name = os.getenv("CHROMA_COLLECTION_NAME", "authoritative_sources")

    logger.info(f"连接 ChromaDB: {chroma_host}:{chroma_port}")
    client = chromadb.HttpClient(
        host=chroma_host,
        port=chroma_port,
        settings=Settings(anonymized_telemetry=False),
    )

    # 删除旧 collection
    try:
        client.delete_collection(collection_name)
        logger.info(f"已删除旧 Collection: {collection_name}")
    except:
        pass

    embedding_fn = get_embedding_function()

    collection = client.create_collection(
        name=collection_name,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )
    logger.info(f"已创建 Collection: {collection_name}")

    for doc in SEED_DOCUMENTS:
        doc_id = str(doc.get("db_id", uuid.uuid4().hex))
        content = doc["content"]
        title = doc["title"]
        inferred = _infer_metadata(title)
        # 按段落分块，每块不超过500字
        paragraphs = [p.strip() for p in content.split("\n") if p.strip()]

        chunks = []
        current_chunk = ""
        for para in paragraphs:
            if len(current_chunk) + len(para) > 500 and current_chunk:
                chunks.append(current_chunk)
                current_chunk = para
            else:
                current_chunk = current_chunk + "\n" + para if current_chunk else para
        if current_chunk:
            chunks.append(current_chunk)

        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}_{i}"
            metadata = {
                "file_name": title,
                "source_type": doc["source_type"],
                "media_type": doc["media_type"],
                "chunk_index": i,
                "total_chunks": len(chunks),
                "doc_id": doc_id,
                "age_group": inferred["age_group"],
                "region": inferred["region"],
            }
            collection.add(
                ids=[chunk_id],
                documents=[chunk],
                metadatas=[metadata],
            )

        logger.info(f"  ChromaDB 写入: {doc['title']} -> {len(chunks)} 个分块")

    total = collection.count()
    logger.info(f"ChromaDB 写入完成，共 {total} 条向量")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    logger.info("=" * 50)
    logger.info("知识库种子数据导入开始")
    logger.info("=" * 50)

    try:
        seed_postgresql()
        logger.info("")
        seed_chromadb()
        logger.info("")
        logger.info("=" * 50)
        logger.info("知识库种子数据导入完成！")
        logger.info("=" * 50)
    except Exception as e:
        logger.error(f"导入失败: {e}")
        import traceback
        traceback.print_exc()
