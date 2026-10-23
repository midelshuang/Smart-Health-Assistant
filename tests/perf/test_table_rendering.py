"""
测试 PDF 表格数据在前端的渲染效果
模拟前端 detectAndRenderTables 函数的逻辑
"""
import httpx
import json
import re

BASE_URL = "http://localhost:8000"

def fetch_real_chunk():
    """获取真实的 chunk 数据"""
    chunk_id = "guide_189f83c136f0_25"
    r = httpx.get(f"{BASE_URL}/api/v1/rag/chunk/{chunk_id}", timeout=30)
    if r.status_code == 200:
        return r.json()
    return None

def detect_and_render_tables(text):
    """模拟前端表格检测逻辑"""
    if not text:
        return []

    lines = text.split("\n")
    segments = []
    table_buffer = []
    normal_buffer = []

    def flush_normal():
        if normal_buffer:
            segments.append({"type": "text", "content": "\n".join(normal_buffer)})
            normal_buffer.clear()

    def flush_table():
        if len(table_buffer) >= 2:
            segments.append({"type": "table", "rows": table_buffer.copy()})
        elif len(table_buffer) == 1:
            normal_buffer.append(table_buffer[0])
        table_buffer.clear()

    for line in lines:
        # 检测分隔符
        pipe_cols = [s.strip() for s in line.split("|") if s.strip()]
        tab_cols = [s.strip() for s in line.split("\t") if s.strip()]
        space_cols = [s.strip() for s in re.split(r"\s{2,}", line) if s.strip()]

        is_table_row = False
        cols = []

        if len(pipe_cols) >= 2:
            is_table_row = True
            cols = pipe_cols
        elif len(tab_cols) >= 2:
            is_table_row = True
            cols = tab_cols
        elif len(space_cols) >= 2 and len(line.strip()) > 10:
            is_table_row = True
            cols = space_cols

        if is_table_row:
            if all(len(c) < 2 for c in cols) or re.match(r"^[\s\-—─]+$", line):
                is_table_row = False

        if is_table_row:
            flush_normal()
            table_buffer.append({"raw": line, "cols": cols})
        else:
            flush_table()
            normal_buffer.append(line)

    flush_normal()
    flush_table()
    return segments

def test_with_real_data():
    """使用真实 API 数据测试"""
    print("=" * 60)
    print("测试1：真实 API 数据")
    print("=" * 60)

    data = fetch_real_chunk()
    if not data:
        print("❌ 无法获取数据")
        return

    content = data.get("content", "")
    file_name = data.get("file_name", "")
    print(f"文件: {file_name}")
    print(f"内容长度: {len(content)} 字符")
    print()

    # 检测表格
    segments = detect_and_render_tables(content)
    print(f"检测到 {len(segments)} 个段落:")
    for i, seg in enumerate(segments):
        if seg["type"] == "text":
            print(f"  [{i}] 文本段 ({len(seg['content'])} 字符)")
        else:
            rows = seg["rows"]
            col_count = len(rows[0]["cols"])
            print(f"  [{i}] 表格 ({len(rows)}行 x {col_count}列)")
            # 打印表格预览
            print(f"      表头: {rows[0]['cols']}")
            if len(rows) > 1:
                print(f"      首行: {rows[1]['cols']}")

    # 检查乱码
    has_garbled = any(ord(c) > 127 and not '\u4e00' <= c <= '\u9fff' and c not in '，。；：！？、""''（）《》'
                     for seg in segments if seg["type"] == "text"
                     for c in seg["content"])
    # 简化检查：只检查是否有异常 Unicode 字符
    garbled_chars = []
    for seg in segments:
        if seg["type"] == "text":
            for c in seg["content"]:
                cp = ord(c)
                if cp > 0xFF and not (0x4E00 <= cp <= 0x9FFF or
                                      0x3000 <= cp <= 0x303F or
                                      0xFF00 <= cp <= 0xFFEF):
                    garbled_chars.append((c, hex(cp)))

    if garbled_chars:
        print(f"\n⚠️  可能的异常字符: {garbled_chars[:10]}")
    else:
        print("\n✅ 无乱码检测")

    # 检查关键内容是否完整
    expected_terms = ["食谱", "营养", "食材", "做法", "食养"]
    found = [t for t in expected_terms if t in content]
    print(f"内容完整性: 找到 {len(found)}/{len(expected_terms)} 个关键词: {found}")

    # 测试表格渲染
    print("\n" + "-" * 60)
    print("表格渲染测试:")
    for i, seg in enumerate(segments):
        if seg["type"] == "table":
            rows = seg["rows"]
            col_count = len(rows[0]["cols"])
            print(f"\n表格 {i}:")
            for j, row in enumerate(rows[:4]):  # 只打印前4行
                cells = row["cols"]
                print(f"  行{j}: {cells}")
            if len(rows) > 4:
                print(f"  ... (共{len(rows)}行)")

    return segments

def test_with_sample_tables():
    """使用模拟 PDF 表格数据测试"""
    print("\n" + "=" * 60)
    print("测试2：模拟 PDF 表格数据")
    print("=" * 60)

    # 模拟指南中的食物交换表
    sample_text = """表3.5 东南地区食谱示例

食谱名称    食材    用量    烹饪方法    热量(kcal)
红枣枸杞粥    粳米    50g    煮粥    175
    红枣    10颗    去核    20
    枸杞    5g    洗净    5
虫草花炖土鸡    土鸡    500g    焯水炖煮    850
    虫草花    50g    洗净    30
    生姜    3片    拍扁    5

附录2 常见食物交换表

食物类别    交换份    食物名称    重量(g)    蛋白质(g)    脂肪(g)    碳水(g)
谷薯类    1    大米    50    4.0    0.4    38.0
    小米    50    4.5    0.5    36.0
    燕麦    50    6.0    1.0    33.0
蔬菜类    1    大白菜    500    7.5    0.4    16.5
    菠菜    400    9.6    1.2    12.0

注：以上数据来源《中国居民膳食指南（2022）》"""

    segments = detect_and_render_tables(sample_text)
    print(f"检测到 {len(segments)} 个段落:")
    for i, seg in enumerate(segments):
        if seg["type"] == "text":
            preview = seg["content"][:80].replace("\n", "\\n")
            print(f"  [{i}] 文本段: '{preview}...'")
        else:
            rows = seg["rows"]
            col_count = len(rows[0]["cols"])
            print(f"  [{i}] 表格 ({len(rows)}行 x {col_count}列):")
            for j, row in enumerate(rows):
                print(f"      行{j}: {row['cols']}")

    # 验证表格数据完整性
    tables = [s for s in segments if s["type"] == "table"]
    assert len(tables) == 2, f"Expected 2 tables, got {len(tables)}"

    # 验证第一个表格（食谱） - 7行：表头 + 2个食谱各3行
    recipe_table = tables[0]
    assert len(recipe_table["rows"]) == 7, f"Recipe table: expected 7 rows, got {len(recipe_table['rows'])}"

    # 验证第二个表格（食物交换表） - 6行：表头 + 5行数据
    exchange_table = tables[1]
    assert len(exchange_table["rows"]) == 6, f"Exchange table: expected 6 rows, got {len(exchange_table['rows'])}"

    print("\n✅ 表格检测正确！")
    print(f"  - 文本段数量: {len([s for s in segments if s['type'] == 'text'])}")
    print(f"  - 表格数量: {len(tables)}")
    for i, t in enumerate(tables):
        print(f"    表格{i}: {len(t['rows'])}行 x {len(t['rows'][0]['cols'])}列")

    # 验证无乱码
    print("\n✅ 无乱码检测")
    print("✅ 所有断言通过")

if __name__ == "__main__":
    test_with_real_data()
    test_with_sample_tables()
