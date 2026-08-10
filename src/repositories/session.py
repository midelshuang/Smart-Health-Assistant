# database/session.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
import src.config.settings as ST  # 导入你的数据库配置

# 创建数据库引擎
engine = create_engine(ST.DB_CONNECTION_STRING)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """获取数据库会话的依赖函数"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()