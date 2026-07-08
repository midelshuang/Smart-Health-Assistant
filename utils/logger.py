# utils/logger.py
import logging
import os

def setup_logger(name="HealthAI_ETL", log_file="logs/etl_run.log"):
    """
    配置并返回一个全局通用的 Logger。
    包含控制台输出和文件记录功能。
    """
    # 1. 确保日志目录存在
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logger = logging.getLogger(name)
    
    # 防止重复添加 Handler (非常重要！)
    if logger.handlers:
        return logger
    
    logger.setLevel(logging.INFO)
    
    # 2. 定义格式
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 3. 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 4. 文件处理器
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger