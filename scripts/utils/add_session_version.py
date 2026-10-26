# -*- coding: utf-8 -*-
"""R09 数据库迁移：users 表新增 session_version 列（幂等，可重复执行）。

用途：改密/重置密码后 session_version 自增，旧 JWT 因 ver 声明不匹配而失效。

用法（health_ai_env）：
    & "D:\\conda-envs\\health_ai_env\\python.exe" scripts/utils/add_session_version.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text

from src.repositories.session import engine


def main():
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE users "
            "ADD COLUMN IF NOT EXISTS session_version "
            "INTEGER NOT NULL DEFAULT 0"
        ))
        conn.commit()

    # 回读确认
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT column_name, column_default FROM information_schema.columns "
            "WHERE table_name = 'users' AND column_name = 'session_version'"
        )).fetchone()
        if row:
            print(f"[OK] users.session_version 已就绪 (default={row[1]})")
        else:
            print("[FAIL] session_version 列未创建")
            sys.exit(1)


if __name__ == "__main__":
    main()
