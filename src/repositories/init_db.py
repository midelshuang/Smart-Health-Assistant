# 第一步：导包
import psycopg2
import os
from dotenv import load_dotenv
import sys
import logging
import re
from pathlib import Path  # 引入 pathlib

# 获取当前脚本所在的绝对路径，用于正确加载 .env
# 【修复点】使用 Path 对象处理路径，避免类型检查报错
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR.parent / '.env'
SQL_FILE_PATH = BASE_DIR / 'init.sql'
LOG_DIR = BASE_DIR / 'logs'

load_dotenv(ENV_PATH)


# 第六步：创建日志模块
def setup_logging():
    """配置日志：同时输出到控制台和文件"""
    logger = logging.getLogger("DB_Init")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # 1. 控制台 Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # 2. 文件 Handler
    if not LOG_DIR.exists():
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(LOG_DIR / 'init_db.log', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger


logger = setup_logging()

# 第二步：定义数据库配置参数
# 【注意】os.getenv 可能返回 None，建议增加默认值或检查
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "healthai_db")
DB_USER = os.getenv("POSTGRES_USER", "postgres")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "")


# 第三步：读取 SQL 文件
def read_sql_file(file_path: Path):
    """
    读取 SQL 文件内容
    """
    try:
        logger.info(f"正在读取 SQL 文件: {file_path}")
        # Path 对象可以直接传给 open
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"文件未找到: {file_path}")
        return None
    except Exception as e:
        logger.exception(f"读取文件发生未知错误: {e}")
        return None


# 第四步：执行 SQL 的核心函数（终极增强版）
def execute_sql(sql_content):
    """
    执行 SQL，支持多条语句逐条执行，完美处理 DO $$ 块
    """
    connection = None
    cursor = None
    try:
        logger.info("🔌 正在连接数据库...")
        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS
        )
        connection.autocommit = False
        cursor = connection.cursor()

        logger.info("⚙️ 开始解析并执行 SQL 语句...")

        # 【核心优化】使用更健壮的正则来分割 SQL 语句
        # 逻辑：匹配以分号结尾的语句，但忽略美元引用 $$ 内部的分号
        # 这是一个简化的分割器，适用于大多数标准建表脚本
        import re

        # 1. 先移除所有的单行注释 (-- ...)，防止注释里的分号干扰
        clean_sql = re.sub(r'--.*?\n', '\n', sql_content)

        # 2. 使用正则分割：匹配不在 $$ 之间的分号
        # 注意：这个正则在复杂嵌套下可能仍有局限，但对于当前的 init.sql 足够用
        # 我们采用一种更暴力的办法：按 ';\n' 分割，然后手动补全 DO 块

        statements = []
        current_stmt = []
        in_dollar_quote = False

        for line in clean_sql.split('\n'):
            stripped_line = line.strip()

            # 检查是否进入或离开 $$ 块
            if '$$' in stripped_line:
                # 简单计数 $$ 出现的次数，奇数次为进入/离开状态切换
                count = stripped_line.count('$$')
                if count % 2 != 0:
                    in_dollar_quote = not in_dollar_quote

            current_stmt.append(line)

            # 如果当前不在 $$ 块内，且行尾有分号，则认为语句结束
            if not in_dollar_quote and stripped_line.endswith(';'):
                stmt = '\n'.join(current_stmt).strip()
                if stmt:
                    statements.append(stmt)
                current_stmt = []

        # 处理最后可能剩余的内容
        if current_stmt:
            remaining = '\n'.join(current_stmt).strip()
            if remaining:
                statements.append(remaining)

        success_count = 0
        for statement in statements:
            clean_statement = statement.strip()
            if not clean_statement:
                continue

            try:
                cursor.execute(clean_statement)
                success_count += 1
            except Exception as stmt_error:
                logger.error(f"❌ 执行语句失败: {clean_statement[:100]}... 错误: {stmt_error}")
                raise stmt_error

        connection.commit()
        logger.info(f"✅ 成功执行了 {success_count} 条 SQL 语句，事务已提交")
        return True

    except Exception as e:
        if connection:
            connection.rollback()
            logger.error("🔄 事务已回滚")
        logger.error(f"❌ SQL 执行流程中断: {e}")
        return False
    finally:
        if cursor: cursor.close()
        if connection: connection.close()


# 第五步：程序的入口点
if __name__ == "__main__":
    logger.info("🚀 === 开始初始化数据库流程 ===")

    # 1. 读取 SQL
    sql_content = read_sql_file(SQL_FILE_PATH)

    # 2. 判断文件是否读取成功
    if sql_content is None:
        logger.critical("❌ 数据库初始化失败: SQL 文件读取异常")
        sys.exit(1)

    logger.info("📄 SQL 文件读取完成，准备执行...")

    # 3. 执行 SQL
    success = execute_sql(sql_content)

    # 4. 根据返回值判断结果
    if success:
        logger.info("🎉 === 数据库初始化成功！！ ===")
        sys.exit(0)
    else:
        logger.critical("❌ === 数据库初始化失败 ===")
        sys.exit(1)
