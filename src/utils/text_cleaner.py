"""
做成一个纯粹的工具类或函数集合。输入一段脏文本，输出一段干净文本。它应该是无状态的，且方便单独进行单元测试。
  - 清洗：去除多余的空格、换行、页眉页脚、无意义的特殊字符。
  功能：
  - 去除多余空格和换行
  - 去除页眉页脚（常见模式识别）
  - 去除无意义特殊字符
  - 文本规范化
逻辑：
    - 标准化空白字符（多个空格/换行合并为单个）
    - 去除常见页眉页脚模式
    - 清理无意义的特殊字符和符号
    - 保留有意义的标点符号和格式
"""
import re
import logging
from typing import List

# 创建 logger
logger = logging.getLogger(__name__)


class TextCleaner:
    """
    文本清理工具类 - 无状态设计，纯函数式接口
    
    使用示例：
        cleaned = TextCleaner.clean(raw_text)
        # 或使用便捷函数
        from src.utils.text_cleaner import clean_text
        cleaned = clean_text(raw_text)
    """
    
    # 常见页眉模式
    HEADER_PATTERNS = [
        r'^\s*第\s*\d+\s*页\s*$',                    # "第 1 页"
        r'^\s*Page\s+\d+\s*(of\s+\d+)?\s*$',         # "Page 1 of 10"
        r'^\s*-\s*\d+\s*-\s*$',                       # "- 1 -"
        r'^\s*\d+\s*/\s*\d+\s*$',                     # "1 / 10"
        r'^\s*【.*?】\s*$',                           # "【页眉】"
        r'^\s*《.*?》\s*$',                           # "《文档标题》"
    ]

    FOOTER_PATTERNS = [
        r'^\s*版权所有\s*.*?$',                        # "版权所有 © 2024"
        r'^\s*Copyright\s*©?\s*\d{4}.*?$',            # "Copyright © 2024"
        r'^\s*联系电话[:：]?\s*\d{3,4}[-\s]?\d{3,4}[-\s]?\d{3,4}\s*$',  # "联系电话：xxx-xxxx-xxxx"
        r'^\s*邮箱[:：]?\s*\S+@\S+\.\S+\s*$',         # "邮箱：example@mail.com"
        r'^\s*网址[:：]?\s*https?://\S+\s*$',         # "网址：http://example.com"
        r'^\s*www\.\S+\s*$',                          # "www.example.com"
    ]

    # 无意义的特殊字符模式（保留常用标点和文字）
    MEANINGLESS_CHARS_PATTERN = r'[^\w\s\u4e00-\u9fff，。！？、；：""''（）【】《》—…,.!?;:\'\"()\[\]{}\-—…]'

    @classmethod
    def clean(cls, text: str,
              remove_headers: bool = True,
              remove_footers: bool = True,
              normalize_whitespace: bool = True,
              remove_special_chars: bool = True) -> str:
        """
        清洗文本的主入口方法
        
        Args:
            text: 待清洗的原始文本
            remove_headers: 是否去除页眉
            remove_footers: 是否去除页脚
            normalize_whitespace: 是否标准化空白字符
            remove_special_chars: 是否去除无意义特殊字符
            
        Returns:
            清洗后的文本
        """
        # 1. 验证输入
        if not text or not isinstance(text, str):
            logger.warning(f"输入数据无效，期望字符串，实际收到: {type(text)}")
            return text or ""

        logger.debug(f"开始清洗文本，原始文本长度: {len(text)} 字符")
        cleaned = text
        original_length = len(text)

        # 2. 去除页眉页脚
        if remove_headers or remove_footers:
            before_len = len(cleaned)
            cleaned = cls._remove_headers_footers(
                cleaned,
                remove_headers=remove_headers,
                remove_footers=remove_footers
            )
            removed = before_len - len(cleaned)
            logger.debug(f"页眉页脚清洗完成，移除 {removed} 字符")

        # 3. 去除无意义特殊字符
        if remove_special_chars:
            before_len = len(cleaned)
            cleaned = cls._remove_meaningless_chars(cleaned)
            removed = before_len - len(cleaned)
            logger.debug(f"特殊字符清洗完成，移除 {removed} 字符")

        # 4. 标准化空白字符
        if normalize_whitespace:
            before_len = len(cleaned)
            cleaned = cls._normalize_whitespace(cleaned)
            removed = before_len - len(cleaned)
            logger.debug(f"空白字符规范化完成，移除 {removed} 字符")

        total_removed = original_length - len(cleaned)
        reduction_pct = (total_removed / original_length * 100) if original_length > 0 else 0
        logger.info(
            f"文本清洗完成 | "
            f"原始: {original_length} 字符 | "
            f"清洗后: {len(cleaned)} 字符 | "
            f"移除: {total_removed} 字符 ({reduction_pct:.1f}%)"
        )

        return cleaned.strip()

    @classmethod
    def _remove_headers_footers(cls, text: str,
                                 remove_headers: bool = True,
                                 remove_footers: bool = True) -> str:
        """
        去除文档中的页眉页脚（按行处理）
        
        Args:
            text: 输入文本
            remove_headers: 是否去除页眉
            remove_footers: 是否去除页脚
            
        Returns:
            去除页眉页脚后的文本
        """
        lines = text.split('\n')
        kept_lines = []
        removed_count = 0
        removed_examples = []

        for line in lines:
            stripped_line = line.strip()
            
            # 空行直接保留
            if not stripped_line:
                kept_lines.append(line)
                continue

            is_noise = False
            
            # 检查是否为页眉
            if remove_headers:
                for pattern in cls.HEADER_PATTERNS:
                    if re.match(pattern, stripped_line, re.IGNORECASE):
                        is_noise = True
                        removed_examples.append(stripped_line[:50])
                        logger.debug(f"命中页眉模式，已剔除: [{stripped_line}]")
                        break

            # 检查是否为页脚
            if not is_noise and remove_footers:
                for pattern in cls.FOOTER_PATTERNS:
                    if re.match(pattern, stripped_line, re.IGNORECASE):
                        is_noise = True
                        removed_examples.append(stripped_line[:50])
                        logger.debug(f"命中页脚模式，已剔除: [{stripped_line}]")
                        break

            # 保留非噪音行
            if not is_noise:
                kept_lines.append(line)
            else:
                removed_count += 1

        if removed_count > 0:
            logger.info(f"共清理了 {removed_count} 行页眉/页脚噪音")
            if removed_examples:
                logger.debug(f"被剔除的示例: {removed_examples[:5]}")
        else:
            logger.debug("未发现页眉/页脚噪音")
        
        return '\n'.join(kept_lines)

    @classmethod
    def _remove_meaningless_chars(cls, text: str) -> str:
        """
        去除无意义的特殊字符
        
        Args:
            text: 输入文本
            
        Returns:
            清理后的文本
        """
        cleaned = text

        # 去除控制字符（保留换行符 \n 和制表符 \t）
        cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', cleaned)

        # 去除 Unicode 中的不可见字符
        cleaned = re.sub(r'[\u200b\u200c\u200d\ufeff\ufffc]', '', cleaned)

        # 去除重复的标点符号（如："。。。" -> "。"，"!!!" -> "!"）
        cleaned = re.sub(r'([。！？,.!])\1+', r'\1', cleaned)
        cleaned = re.sub(r'([—~])\1+', r'\1', cleaned)

        # 去除孤立的特殊符号（前后都是空格的无意义符号）
        # 注意：这个比较激进，可以根据实际情况调整
        cleaned = re.sub(r'\s+[^\w\s\u4e00-\u9fff，。！？、；：""''（）【】《》—…,.!?;:\'\"()\[\]{}\-—…]+\s+', ' ', cleaned)

        logger.debug("特殊字符清洗完成")
        return cleaned

    @classmethod
    def _normalize_whitespace(cls, text: str) -> str:
        """
        去除多余的空格和换行，标准化空白字符
        
        Args:
            text: 输入文本
            
        Returns:
            标准化后的文本
        """
        cleaned = text

        # 将制表符转换为空格
        cleaned = cleaned.replace('\t', ' ')

        # 去除每行首尾的空白字符
        lines = cleaned.split('\n')
        lines = [line.strip() for line in lines]
        cleaned = '\n'.join(lines)

        # 将多个连续空格替换为单个空格（不包括换行符）
        cleaned = re.sub(r' {2,}', ' ', cleaned)

        # 将3个或更多连续换行替换为2个（最多保留一个空行）
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

        # 去除中文之间的多余空格（仅匹配空格/制表符，避免跨越换行符导致段落被合并）
        # 使用 lookahead 让 re.sub 能正确处理连续多对"中文+空格+中文"
        cleaned = re.sub(r'([\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])', r'\1', cleaned)

        logger.debug("空白字符规范化完成")
        return cleaned

    @classmethod
    def clean_paragraphs(cls, text: str) -> List[str]:
        """
        将文本按段落分割并清洗每个段落
        
        注意：这是辅助方法，用于快速分段，不是智能分块
        智能分块应该在 DocumentProcessor 中实现
        
        Args:
            text: 输入文本
            
        Returns:
            清洗后的段落列表
        """
        logger.debug(f"开始分段清洗，文本长度: {len(text)} 字符")
        cleaned = cls.clean(text)
        paragraphs = [p.strip() for p in cleaned.split('\n\n') if p.strip()]
        logger.info(f"分段完成，共 {len(paragraphs)} 个段落")
        return paragraphs


# 便捷函数（函数式接口）
def clean_text(text: str, **kwargs) -> str:
    """
    清洗文本的便捷函数
    
    Args:
        text: 待清洗的文本
        **kwargs: 传递给 TextCleaner.clean() 的参数
        
    Returns:
        清洗后的文本
    """
    return TextCleaner.clean(text, **kwargs)


def clean_document(text: str) -> str:
    """
    清洗完整文档的便捷函数（使用默认配置）
    
    Args:
        text: 待清洗的文档文本
        
    Returns:
        清洗后的文档文本
    """
    return TextCleaner.clean(text)


# 测试示例
if __name__ == "__main__":
    # 配置日志输出到控制台
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("=== 开始测试文本清洗器 ===\n")
    
    # 测试文本
    test_text = """
        第 1 页


    蛋白质是人体必需的营养素。。。   它由氨基酸组成！！！
    碳水化合物也是重要的能量来源。


        - 2 -

    维生素C可以增强免疫力，   广泛存在于水果中。


    版权所有 © 2024 某某公司
    联系电话：400-123-4567
    """
    
    print("--- 原始文本 ---")
    print(test_text)
    
    print("\n--- 清洗后文本 ---")
    cleaned = TextCleaner.clean(test_text)
    print(cleaned)
    
    print("\n--- 分段清洗 ---")
    paragraphs = TextCleaner.clean_paragraphs(test_text)
    for i, para in enumerate(paragraphs, 1):
        print(f"段落 {i}: {para}")
    
    print("\n=== 测试完成 ===")


