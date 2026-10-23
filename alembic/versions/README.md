# Alembic 迁移版本目录

每个迁移文件由 `alembic revision --autogenerate -m "msg"` 生成。

## 首次生成 baseline

由于现有数据库表已存在，请按以下步骤生成 baseline：

```bash
# 1. 确保 .env 中 DATABASE_URL 指向目标数据库
# 2. 进入项目根目录，激活 conda 环境
conda activate health_ai_env

# 3. 生成第一个 baseline（基于现有 SQLAlchemy 模型与 DB 的 diff）
alembic revision --autogenerate -m "baseline snapshot"

# 4. 检查生成的迁移脚本（alembic/versions/xxxx_baseline_snapshot.py）
#    如果是空 upgrade/downgrade，说明 DB 与模型一致
#    如果有差异，说明 DB 与模型不一致，需要人工 review

# 5. 标记当前 DB 已处于 baseline 版本（不实际执行 SQL，只写版本号）
alembic stamp head

# 6. 后续模型变更流程
#    a. 修改 src/models/db_models.py
#    b. 生成迁移：alembic revision --autogenerate -m "add xxx column"
#    c. 检查脚本
#    d. 应用：alembic upgrade head
#    e. 回滚：alembic downgrade -1
```
