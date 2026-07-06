"""
自动执行 SQL 的初始化脚本，核心逻辑其实就是一个“安全的文件读取与数据库交互”的过程。
能够实现“开箱即用”
"""
# 第一步：导包
#用于连接 PostgreSQL 数据库的驱动
import psycopg2
# 用于处理文件路径的内置库（如 os），这能防止因为相对路径或绝对路径写错而导致找不到 SQL 文件。
import os
from dotenv import load_dotenv
import sys
import logging

env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path)

# 第六步：创建日志模块
# ================== 日志配置区域 ==================
def setup_logging():
    """配置日志：同时输出到控制台和文件"""
    logger = logging.getLogger("DB_Init")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
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
# 如主机地址、端口、数据库名、用户名和密码
DB_HOST = os.getenv("POSTGRES_HOST") #主机地址
DB_PORT = os.getenv("POSTGRES_PORT") #端口
DB_NAME = os.getenv("POSTGRES_DB") #数据库名
DB_USER = os.getenv("POSTGRES_USER") #用户名
DB_PASS = os.getenv("POSTGRES_PASSWORD") #密码
# print("主机地址："+DB_HOST+" 端口："+DB_PORT+" 数据库名："+DB_NAME+" 用户名："+DB_USER+" 密码："+DB_PASS)
# exit()

# 第三步：编写读取 SQL 文件的函数
def read_sql_file(file_path):
    """
    读取 SQL 文件
    """
    #使用 os.path 动态获取当前脚本所在的目录，拼接出 init.sql 的绝对路径。
    try:
        abs_path = os.path.join(os.path.dirname(__file__), file_path)
        logger.info(f"正在读取 SQL 文件: {abs_path}")  # 替换 print
        # exit()
        #使用 open() 函数打开文件并读取内容,并返回
        with open(file_path, "r", encoding="utf-8") as f:
            sql = f.read()
            return sql
    except FileNotFoundError:
        logger.error(f"文件未找到: {abs_path}")  # 替换 print
        return None
    except Exception as e:
        logger.exception(f"读取文件发生未知错误: {e}")
        return None


# 第四步：编写执行 SQL 的核心函数
def execute_sql(sql):
    """
    执行 SQL，这是脚本的心脏，负责把读到的 SQL 发给数据库。
    """
    connection = None
    cursor = None
    try:
        logger.info("🔌 正在连接数据库...") # 替换 print
        # 建立数据库连接（connect）。
        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS
        )
        # 创建一个游标对象（cursor），它是执行 SQL 的载体
        cursor = connection.cursor()
        logger.info("⚙️ 正在执行 SQL 语句...")

        # 使用 execute() 方法执行 SQL
        cursor.execute(sql)
        # 提交事务
        connection.commit()
        logger.info("✅ SQL 执行成功，事务已提交") # 替换 print
        return True

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(f"❌ SQL 执行失败: {e}")  # 使用 logger 记录错误
        raise e  # 【关键】把错误抛出去，让主程序知道出事了，终止运行
    finally:
        # 关闭游标和数据库连接
        # 修复：关闭前检查对象是否存在，防止二次报错
        if cursor:
            cursor.close()
        if connection:
            connection.close()
        logger.debug("数据库连接已关闭")

# 第五步：程序的入口点
if __name__ == "__main__":
    logger.info("🚀 === 开始初始化数据库流程 ===")
    try:
        # 1. 读取 SQL
        sql_content = read_sql_file("init.sql")

        # 2. 判断文件是否读取成功
        if sql_content is None:
            logger.critical("❌ 数据库初始化失败: SQL 文件读取异常")  # 替换 print
            sys.exit(1)

        logger.info("📄 SQL 文件已读取，准备执行...")  # 替换 print

        # 3. 执行 SQL
        success = execute_sql(sql_content)

        # 4. 根据返回值判断结果
        if success:
            logger.info("🎉 === 数据库初始化成功！！ ===")  # 替换 print

    except Exception as e:
        logger.critical(f"❌ === 数据库初始化失败: {e} ===")  # 替换 print
        sys.exit(1)  # 失败时返回非0退出码