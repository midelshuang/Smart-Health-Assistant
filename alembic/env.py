"""
Alembic 迁移环境脚本

职责：
1. 从 .env / 环境变量加载数据库 URL（不硬编码）
2. 把项目 SQLAlchemy 模型元数据注入 Alembic，支持 --autogenerate
3. 区分 online / offline 两种运行模式

offline：仅打印 SQL，不连库
online：实际连接数据库执行迁移
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# 让 alembic 能 import 项目模块（不依赖 PYTHONPATH）
# 假设 alembic/ 目录在项目根下，需要把项目根加入 sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 加载 .env（dotenv 已在项目其他地方使用）
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
except ImportError:
    pass

# 导入项目 SQLAlchemy 模型元数据（autogenerate 依赖此对象）
from src.models.db_models import Base  # noqa: E402
target_metadata = Base.metadata

# Alembic 配置
config = context.config

# 从环境变量读取 DATABASE_URL 注入 Alembic
# 优先级：环境变量 > .env 文件 > alembic.ini
db_url = os.getenv("DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)
else:
    raise RuntimeError(
        "DATABASE_URL 未设置，无法运行 Alembic 迁移。"
        "请在 .env 或环境变量中配置 DATABASE_URL"
    )

# 日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """offline 模式：仅打印 SQL，不连接数据库

    用法：alembic upgrade head --sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """online 模式：连接数据库执行迁移

    用法：alembic upgrade head
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
