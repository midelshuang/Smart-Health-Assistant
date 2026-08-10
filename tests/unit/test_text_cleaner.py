"""
utils/text_cleaner.py 单元测试

覆盖：
- 主入口 clean_text / TextCleaner.clean
- 页眉页脚移除
- 无意义特殊字符清理
- 空白字符归一化
- 段落分割 clean_paragraphs
- 边界情况（空字符串/非字符串）
"""
import pytest
from src.utils.text_cleaner import (
    TextCleaner,
    clean_text,
    clean_document,
)


class TestCleanTextBasic:
    """主入口 clean_text 基础功能"""

    def test_empty_string_returns_empty(self):
        assert clean_text("") == ""

    def test_none_returns_empty(self):
        # 源码：return text or ""
        assert clean_text(None) == ""  # type: ignore[arg-type]

    def test_pure_text_unchanged(self):
        assert clean_text("hello world") == "hello world"

    def test_chinese_text_unchanged(self):
        text = "蛋白质是人体必需的营养素"
        assert clean_text(text) == text

    def test_multiple_spaces_collapsed(self):
        assert clean_text("hello   world") == "hello world"

    def test_leading_trailing_whitespace_stripped(self):
        assert clean_text("  hello  ") == "hello"

    def test_multiple_newlines_collapsed(self):
        # 3+ 连续换行 → 2 个换行（保留一个空行）
        text = "line1\n\n\n\nline2"
        result = clean_text(text)
        assert "\n\n" in result
        assert "\n\n\n" not in result

    def test_tab_replaced_with_space(self):
        result = clean_text("hello\tworld")
        assert "\t" not in result

    def test_chinese_extra_space_removed(self):
        # 中文字符之间的空格被去掉
        text = "蛋白质 是 营养素"
        result = clean_text(text)
        assert " " not in result or result == "蛋白质是营养素"


class TestRemoveHeadersFooters:
    """页眉页脚清理"""

    def test_page_number_header_removed(self):
        text = "第 1 页\n正文内容"
        result = clean_text(text)
        assert "第 1 页" not in result
        assert "正文内容" in result

    def test_english_page_header_removed(self):
        text = "Page 1 of 10\nmain content"
        result = clean_text(text)
        assert "Page 1 of 10" not in result
        assert "main content" in result

    def test_copyright_footer_removed(self):
        text = "main content\n版权所有 © 2024 某某公司"
        result = clean_text(text)
        assert "版权所有" not in result
        assert "main content" in result

    def test_dashed_page_number_removed(self):
        text = "content\n- 1 -\nmore content"
        result = clean_text(text)
        assert "- 1 -" not in result

    def test_headers_disabled(self):
        """关闭页眉清理应保留页眉"""
        text = "第 1 页\n正文"
        result = clean_text(text, remove_headers=False)
        # 关闭后页眉应保留（仅做空白归一）
        assert "第 1 页" in result

    def test_footers_disabled(self):
        text = "正文\n版权所有 © 2024"
        result = clean_text(text, remove_footers=False)
        assert "版权所有" in result


class TestRemoveMeaninglessChars:
    """特殊字符清理"""

    def test_control_chars_removed(self):
        # \x00 等控制字符被去掉
        text = "hello\x00world"
        result = clean_text(text)
        assert "\x00" not in result

    def test_invisible_unicode_removed(self):
        # 零宽空格被去掉
        text = "hello\u200bworld"
        result = clean_text(text)
        assert "\u200b" not in result

    def test_repeated_punctuation_collapsed(self):
        text = "蛋白质。。。"
        result = clean_text(text)
        assert result == "蛋白质。"

    def test_repeated_exclamation_collapsed(self):
        text = "好!!!"
        result = clean_text(text)
        assert result == "好!"

    def test_special_chars_disabled(self):
        text = "蛋白质。。。"
        result = clean_text(text, remove_special_chars=False)
        assert "。。。" in result


class TestNormalizeWhitespace:
    """空白归一化"""

    def test_normalize_disabled(self):
        text = "hello   world"
        result = clean_text(text, normalize_whitespace=False)
        assert "   " in result

    def test_lines_trimmed(self):
        text = "  line1  \n  line2  "
        result = clean_text(text)
        assert "  line1" not in result


class TestCleanParagraphs:
    """段落分割"""

    def test_split_by_blank_lines(self):
        text = "段落一\n\n段落二\n\n段落三"
        paragraphs = TextCleaner.clean_paragraphs(text)
        assert len(paragraphs) == 3
        assert "段落一" in paragraphs[0]
        assert "段落二" in paragraphs[1]
        assert "段落三" in paragraphs[2]

    def test_empty_paragraphs_filtered(self):
        text = "段落一\n\n\n\n段落二"
        paragraphs = TextCleaner.clean_paragraphs(text)
        assert all(p for p in paragraphs)

    def test_headers_filtered_in_paragraphs(self):
        text = "第 1 页\n\n正文段落"
        paragraphs = TextCleaner.clean_paragraphs(text)
        assert all("第 1 页" not in p for p in paragraphs)


class TestCleanDocument:
    """clean_document 便捷函数"""

    def test_returns_cleaned_text(self):
        text = "hello   world"
        result = clean_document(text)
        assert result == "hello world"


class TestTextCleanerClassInterface:
    """TextCleaner 类接口"""

    def test_clean_classmethod(self):
        text = "<p>hello   world</p>"
        result = TextCleaner.clean(text)
        assert "hello" in result
        assert "world" in result

    def test_clean_with_all_flags_off(self):
        text = "  hello   world  "
        result = TextCleaner.clean(
            text,
            remove_headers=False,
            remove_footers=False,
            normalize_whitespace=False,
            remove_special_chars=False,
        )
        # 关闭所有清理后，结果应几乎等于原文本
        assert "hello" in result
