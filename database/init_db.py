# 第一步：导包
import psycopg2
import os
from dotenv import load_dotenv
import sys
import logging

# 获取当前脚本所在的绝对路径，用于正确加载 .env
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path)

# 第六步：创建日志模块
# ================== 日志配置区域 ==================
def setup_logging():
    """配置日志：同时输出到控制台和文件"""
    logger = logging.getLogger("DB_Init")
    # 避免重复添加 handler（在热重载或多次导入时很有用）
    if logger.handlers:
        return logger
        
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # 1. 控制台 Handler (只显示 INFO 以上)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # 2. 文件 Handler (记录所有细节)
    log_dir = os.path.join(os.path.dirname(__file__), 'logs')
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    file_handler = logging.FileHandler(os.path.join(log_dir, 'init_db.log'), encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger

# 初始化日志
logger = setup_logging()

# 第二步：定义数据库配置参数
DB_HOST = os.getenv("POSTGRES_HOST")
DB_PORT = os.getenv("POSTGRES_PORT")
DB_NAME = os.getenv("POSTGRES_DB")
DB_USER = os.getenv("POSTGRES_USER")
DB_PASS = os.getenv("POSTGRES_PASSWORD")

# 第三步：读取 SQL 文件
def read_sql_file(file_path):
    """
    读取 SQL 文件内容
    """
    try:
        # 使用绝对路径读取，防止运行目录不同导致找不到文件
        abs_path = os.path.join(os.path.dirname(__file__), file_path)
        logger.info(f"正在读取 SQL 文件: {abs_path}")
        
        with open(abs_path, "r", encoding="utf-8") as f:
            sql = f.read()
            return sql
    except FileNotFoundError:
        logger.error(f"文件未找到: {abs_path}")
        return None
    except Exception as e:
        logger.exception(f"读取文件发生未知错误: {e}")
        return None

# 第四步：执行 SQL 的核心函数（优化版）
def execute_sql(sql_content):
    """
    执行 SQL，支持多条语句逐条执行
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
        # 设置为自动提交 False，我们要手动控制事务
        connection.autocommit = False
        cursor = connection.cursor()
        
        logger.info("⚙️ 开始解析并执行 SQL 语句...")
        
        # 【核心修改】简单的 SQL 分割逻辑
        # 注意：这是一个简易分割器，适用于标准的建表语句。
        # 如果 SQL 内容包含复杂的存储过程或美元符号引用，需要更复杂的解析库（如 sqlparse）
        statements = sql_content.split(';')
        
        success_count = 0
        for statement in statements:
            # 去除首尾空白
            clean_statement = statement.strip()
            # 跳过空语句（文件末尾通常会有一个分号，分割后会产生空字符串）
            if not clean_statement:
                continue
                
            try:
                cursor.execute(clean_statement)
                success_count += 1
            except Exception as stmt_error:
                # 如果某一条语句错了，打印出错的语句片段，方便调试
                logger.error(f"❌ 执行语句失败: {clean_statement[:50]}... 错误: {stmt_error}")
                raise stmt_error # 抛出异常，触发回滚

        # 全部执行成功后提交
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
        if cursor:
            cursor.close()
        if connection:
            connection.close()
        logger.debug("数据库连接已关闭")

# 第五步：程序的入口点
if __name__ == "__main__":
    logger.info("🚀 === 开始初始化数据库流程 ===")
    
    # 1. 读取 SQL
    # 注意：这里假设 init.sql 和 init_db.py 在同一级目录，或者根据实际路径调整
    sql_content = read_sql_file("init.sql")

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