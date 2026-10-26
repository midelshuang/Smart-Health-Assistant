"""R10：既有数据库基线迁移（schema 由 database/init.sql 建立）。

对已经存在、schema 完整的数据库，执行一次：

    alembic stamp head

即可把当前库标记为基线版本（0001_baseline），本迁移不创建/修改任何对象。

此后的 schema 变更流程：
    1. 修改 src/models/db_models.py 中的模型
    2. alembic revision --autogenerate -m "描述变更"
    3. 检查生成的迁移脚本，alembic upgrade head 应用

全新部署仍以 database/init.sql 建库，然后 alembic stamp head。

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-26
"""
from alembic import op  # noqa: F401  (op 供后续迁移使用，基线保持空操作)
import sqlalchemy as sa  # noqa: F401

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 基线迁移：现有 schema 由 database/init.sql 负责，这里不做任何事
    pass


def downgrade() -> None:
    # 基线无前序版本，无法回退
    pass
