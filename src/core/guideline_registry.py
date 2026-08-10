"""
权威食养指南注册表（两阶段检索的「阶段1：指南路由」数据底座）

职责：
1. GUIDELINE_REGISTRY：把每份食养指南 PDF 映射到结构化标签
   （guideline_title / abstract / audience / disease_tags / region / age_group / file_patterns）
   —— age_group / region 字段名与现有 _rerank_results_by_metadata、seed_knowledge_base 保持一致。
2. 疾病关键词 → disease_tag 归一；disease_tag → 对应食养指南 映射。
3. match_guideline(file_name)：PDF 文件名 → 注册表条目（兼容「附件N.」前缀、「（问答）/(问答)/问答」后缀、Z-Library 后缀）。
4. select_guidelines_by_profile(profile)：阶段1，据用户画像（年龄/疾病/BMI）选出 guideline_title 集合，供阶段2 where 过滤。
5. build_guide_catalog_chunks()：为每份指南生成 1 条 doc_type=guide_catalog 的特殊 chunk（标题+摘要+标签），
   写入主 collection 参与语义匹配（决策A「向量补摘要」）。

元数据字段值约束：ChromaDB metadata 值仅支持 str/int/float/bool，list 一律用 ",".join。
"""

import os
import re
from typing import Dict, List, Any, Optional

from src.utils.logger import setup_logger

logger = setup_logger(name="GuidelineRegistry")


# ============================================================
# 受众枚举
# ============================================================
AUDIENCE_GENERAL = "general"            # 全人群
AUDIENCE_ADULT = "adult"                # 成人
AUDIENCE_CHILD_ADOLESCENT = "child_adolescent"  # 儿童青少年


# ============================================================
# 指南注册表（8 份主体指南；问答版与主体版经 match_guideline 归一到同一条目）
# age_group 取值对齐 nodes.AGE_GROUP_MAPPING 的 key：
#   infant / preschool / child / adolescent / youth / middle_aged / middle_aged_plus / elderly
# ============================================================
GUIDELINE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "中国居民膳食指南（2022）": {
        "guideline_title": "中国居民膳食指南（2022）",
        "abstract": "中国营养学会发布的面向全人群的平衡膳食权威准则，涵盖食物多样、吃动平衡、多吃蔬果奶豆全谷、适量鱼禽蛋瘦肉、少盐少油控糖、规律进餐足量饮水等八条核心推荐，是其他食养指南的基础。",
        "audience": [AUDIENCE_GENERAL],
        "disease_tags": [],
        "region": "全国",
        "age_groups": ["infant", "preschool", "child", "adolescent", "youth",
                       "middle_aged", "middle_aged_plus", "elderly"],
        "file_patterns": ["中国居民膳食指南"],
    },
    "成人肥胖食养指南（2024年版）": {
        "guideline_title": "成人肥胖食养指南（2024年版）",
        "abstract": "国家卫健委针对成人超重/肥胖人群的食养指南，提供能量控制原则、宏量营养素比例、食物选择与禁忌，并含分地区食谱示例。",
        "audience": [AUDIENCE_ADULT],
        "disease_tags": ["肥胖"],
        "region": "全国",
        "age_groups": ["youth", "middle_aged", "middle_aged_plus", "elderly"],
        "file_patterns": ["成人肥胖食养指南"],
    },
    "儿童青少年肥胖食养指南（2024年版）": {
        "guideline_title": "儿童青少年肥胖食养指南（2024年版）",
        "abstract": "针对儿童青少年肥胖的食养指南，提供分年龄段、分地区的食谱与膳食指导，强调培养健康饮食习惯与行为干预。",
        "audience": [AUDIENCE_CHILD_ADOLESCENT],
        "disease_tags": ["肥胖"],
        "region": "全国",
        "age_groups": ["child", "adolescent"],
        "file_patterns": ["儿童青少年肥胖食养指南"],
    },
    "成人慢性肾脏病食养指南（2024年版）": {
        "guideline_title": "成人慢性肾脏病食养指南（2024年版）",
        "abstract": "针对成人慢性肾脏病的食养指南，提供分期蛋白质控制、电解质管理及分地区食谱示例。",
        "audience": [AUDIENCE_ADULT],
        "disease_tags": ["慢性肾脏病"],
        "region": "全国",
        "age_groups": ["youth", "middle_aged", "middle_aged_plus", "elderly"],
        "file_patterns": ["成人慢性肾脏病食养指南"],
    },
    "成人高尿酸血症与痛风食养指南": {
        "guideline_title": "成人高尿酸血症与痛风食养指南",
        "abstract": "针对成人高尿酸血症/痛风的食养指南，提供低嘌呤饮食原则、急性期与缓解期食物选择及分地区食谱示例。",
        "audience": [AUDIENCE_ADULT],
        "disease_tags": ["高尿酸"],
        "region": "全国",
        "age_groups": ["youth", "middle_aged", "middle_aged_plus", "elderly"],
        # 注：原 PDF 文件名写作「通风」（错字），这里保留以匹配真实文件名
        "file_patterns": ["成人高尿酸血症与通风食养指南", "成人高尿酸血症与痛风食养指南"],
    },
    "成人脑卒中食养指南（2026年版）": {
        "guideline_title": "成人脑卒中食养指南（2026年版）",
        "abstract": "针对成人脑卒中患者的食养指南，提供吞咽适应、营养支持及分地区食谱示例。",
        "audience": [AUDIENCE_ADULT],
        "disease_tags": ["脑卒中"],
        "region": "全国",
        "age_groups": ["middle_aged", "middle_aged_plus", "elderly"],
        "file_patterns": ["成人脑卒中食养指南"],
    },
    "成人骨质疏松症食养指南（2026年版）": {
        "guideline_title": "成人骨质疏松症食养指南（2026年版）",
        "abstract": "针对成人骨质疏松症的食养指南，提供钙/维生素D营养、防跌倒及分地区食谱示例。",
        "audience": [AUDIENCE_ADULT],
        "disease_tags": ["骨质疏松"],
        "region": "全国",
        "age_groups": ["middle_aged", "middle_aged_plus", "elderly"],
        "file_patterns": ["成人骨质疏松症食养指南"],
    },
    "成人肌少症食养指南（2026年版）": {
        "guideline_title": "成人肌少症食养指南（2026年版）",
        "abstract": "针对成人肌少症的食养指南，提供优质蛋白、抗阻营养支持及分地区食谱示例。",
        "audience": [AUDIENCE_ADULT],
        "disease_tags": ["肌少症"],
        "region": "全国",
        "age_groups": ["middle_aged", "middle_aged_plus", "elderly"],
        "file_patterns": ["成人肌少症食养指南"],
    },
}


# ============================================================
# 疾病关键词 → disease_tag 归一
# 注：糖尿病/高血压 无对应食养 PDF，仅作标签 + 软提示，不在 DISEASE_TAG_TO_GUIDELINES 映射
# ============================================================
DISEASE_KEYWORD_TO_TAG: Dict[str, List[str]] = {
    "高尿酸": ["高尿酸", "尿酸高", "痛风", "嘌呤"],
    "肥胖": ["肥胖", "超重", "减重", "减脂", "减肥"],
    "慢性肾脏病": ["肾病", "肾脏病", "慢性肾", "肾衰", "尿毒症", "肾功能不全"],
    "脑卒中": ["脑卒中", "中风", "脑血管意外", "脑梗"],
    "骨质疏松": ["骨质疏松", "骨量减少"],
    "肌少症": ["肌少症", "肌肉减少", "少肌症", "肌肉流失"],
    "糖尿病": ["糖尿病", "血糖高", "高血糖"],
    "高血压": ["高血压", "血压高"],
}

# disease_tag → 对应食养指南标题（仅含「有 PDF」的疾病）
DISEASE_TAG_TO_GUIDELINES: Dict[str, List[str]] = {
    "高尿酸": ["成人高尿酸血症与痛风食养指南"],
    "肥胖": ["成人肥胖食养指南（2024年版）", "儿童青少年肥胖食养指南（2024年版）"],
    "慢性肾脏病": ["成人慢性肾脏病食养指南（2024年版）"],
    "脑卒中": ["成人脑卒中食养指南（2026年版）"],
    "骨质疏松": ["成人骨质疏松症食养指南（2026年版）"],
    "肌少症": ["成人肌少症食养指南（2026年版）"],
}

# 所有「无对应食养 PDF」的疾病标签（用于软提示）
DISEASES_WITHOUT_GUIDELINE_PDF = ["糖尿病", "高血压"]

# 基础指南（始终叠加）
BASELINE_GUIDELINE = "中国居民膳食指南（2022）"


# ============================================================
# 核心函数
# ============================================================

def match_guideline(file_name: str) -> Optional[Dict[str, Any]]:
    """
    根据 PDF 文件名匹配注册表条目。

    兼容：
    - 「附件1.」「附件2.」等数字前缀
    - 「（问答）」「(问答)」「问答」后缀
    - 「 (中国营养学会) (Z-Library)」等出版信息后缀

    Returns:
        匹配到的注册表条目（dict），未匹配返回 None。
    """
    base = os.path.splitext(os.path.basename(file_name))[0]
    # 剥离「附件N.」前缀
    base = re.sub(r'^附件\d+\.', '', base)
    # 剥离问答后缀
    for suf in ["（问答）", "(问答)", "问答"]:
        if base.endswith(suf):
            base = base[:-len(suf)]
            break
    base = base.strip()

    for entry in GUIDELINE_REGISTRY.values():
        for pat in entry["file_patterns"]:
            # 双向子串匹配，兼容出版信息后缀
            if pat in base or base in pat:
                return entry
    return None


def extract_disease_tags(text: str) -> List[str]:
    """从自然语言文本中提取 disease_tag 列表（去重，保序）。"""
    if not text:
        return []
    tags: List[str] = []
    for tag, keywords in DISEASE_KEYWORD_TO_TAG.items():
        if any(kw in text for kw in keywords):
            if tag not in tags:
                tags.append(tag)
    return tags


def normalize_diseases(raw: List[str]) -> List[str]:
    """
    把 DB health_tags / medical_conditions 等杂标签归一为 disease_tag。

    输入可能是中文短语（如「2型糖尿病」「高血压3级」），逐个用关键词匹配到 tag。
    """
    if not raw:
        return []
    tags: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        extracted = extract_disease_tags(item)
        for t in extracted:
            if t not in tags:
                tags.append(t)
    return tags


def age_matches_guideline(age: Optional[int], entry: Dict[str, Any]) -> bool:
    """年龄是否落在条目 age_groups 范围。age 为 None 时不收缩（返回 True）。"""
    if age is None:
        return True
    age_groups = entry.get("age_groups", [])
    if not age_groups:
        return True
    return _age_in_groups(age, age_groups)


def _age_in_groups(age: int, age_groups: List[str]) -> bool:
    """判断年龄落在哪些年龄组 key 内（与 nodes.AGE_GROUP_MAPPING 区间一致）。"""
    # 与 nodes.AGE_GROUP_MAPPING 区间保持一致
    ranges = {
        "infant": (0, 2),
        "preschool": (3, 5),
        "child": (6, 12),
        "adolescent": (13, 17),
        "youth": (18, 35),
        "middle_aged": (36, 59),
        "middle_aged_plus": (60, 64),
        "elderly": (65, 120),
    }
    for g in age_groups:
        lo, hi = ranges.get(g, (None, None))
        if lo is not None and lo <= age <= hi:
            return True
    return False


def select_guidelines_by_profile(profile: Dict[str, Any]) -> List[str]:
    """
    阶段1：根据用户画像选出 guideline_title 集合。

    规则：
    1. 疾病 → DISEASE_TAG_TO_GUIDELINES，并用 age_matches_guideline 二次过滤
       （避免儿童拿到「成人肥胖食养指南」）。
    2. 始终叠加 BASELINE_GUIDELINE（《中国居民膳食指南（2022）》）。
    3. 无疾病 + 成人(age>=18) + BMI>=24 → 追加《成人肥胖食养指南》。
    4. 无疾病 + 儿童青少年 → 仅基础指南（儿童肥胖需用户明确报「肥胖/超重」才走儿童指南）。
    """
    age = profile.get("age")
    bmi = profile.get("bmi")
    diseases = profile.get("diseases") or []
    # 兼容：profile 里可能是 health_tags，统一归一
    if not diseases:
        diseases = normalize_diseases(profile.get("health_tags") or [])

    selected: List[str] = []

    # 1) 疾病匹配（带年龄二次过滤）
    for tag in diseases:
        for title in DISEASE_TAG_TO_GUIDELINES.get(tag, []):
            entry = _find_entry_by_title(title)
            if entry and age_matches_guideline(age, entry) and title not in selected:
                selected.append(title)

    # 2) 始终叠加基础指南
    if BASELINE_GUIDELINE not in selected:
        selected.append(BASELINE_GUIDELINE)

    # 3) 无疾病 + 成人 + BMI 超标 → 追加成人肥胖食养指南
    if not diseases and age is not None and age >= 18 and bmi is not None and bmi >= 24:
        title = "成人肥胖食养指南（2024年版）"
        if title not in selected:
            selected.append(title)

    return selected


def _find_entry_by_title(title: str) -> Optional[Dict[str, Any]]:
    for entry in GUIDELINE_REGISTRY.values():
        if entry["guideline_title"] == title:
            return entry
    return None


def build_guide_catalog_chunks() -> List[Dict[str, Any]]:
    """
    为每份指南生成 1 条 doc_type=guide_catalog 的特殊 chunk，
    内容 = 标题 + 摘要 + 受众 + 疾病标签 + 地区。

    这些 chunk 写入主 collection 后：
    - 参与阶段2 的向量语义检索（决策A「向量补摘要」）
    - 带完整 guideline_title 等元数据，可被 where 过滤命中
    """
    chunks: List[Dict[str, Any]] = []
    for entry in GUIDELINE_REGISTRY.values():
        content = (
            f"【指南标题】{entry['guideline_title']}\n"
            f"【摘要】{entry['abstract']}\n"
            f"【适用人群】{'/'.join(entry['audience'])}\n"
            f"【适用疾病】{'/'.join(entry['disease_tags']) if entry['disease_tags'] else '一般人群'}\n"
            f"【适用地区】{entry['region']}"
        )
        metadata = {
            "source_type": "guideline",
            "guideline_title": entry["guideline_title"],
            "doc_type": "guide_catalog",
            "audience": ",".join(entry["audience"]),
            "disease_tags": ",".join(entry["disease_tags"]),
            "region": entry["region"],
            "age_group": ",".join(entry["age_groups"]),
            "file_name": entry["guideline_title"],
        }
        chunks.append({"content": content, "metadata": metadata})
    return chunks


def get_diseases_without_guideline_pdf(diseases: List[str]) -> List[str]:
    """返回用户疾病中「无对应食养 PDF」的部分，用于软提示。"""
    return [d for d in diseases if d in DISEASES_WITHOUT_GUIDELINE_PDF]
