"""迁移执行器。

使用 PRAGMA user_version 跟踪已应用版本，幂等执行。
per-connection PRAGMA（foreign_keys / journal_mode / busy_timeout）
在 runner 自己的连接上各自初始化，不写入 .sql 文件。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent

# 每个版本对应一个 .sql 文件
MIGRATION_FILES: dict[int, str] = {
    1: "v1_init.sql",
}


def apply_migrations(db_path: Path) -> None:
    """
    将数据库升级到最新 schema 版本。
    使用 PRAGMA user_version 记录已应用版本号。
    幂等：已应用的迁移不会重复执行。

    Args:
        db_path: SQLite 数据库文件路径。文件不存在时自动创建。
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")

        current_version: int = conn.execute("PRAGMA user_version;").fetchone()[0]

        for version in sorted(MIGRATION_FILES.keys()):
            if version <= current_version:
                continue  # 已应用，跳过
            sql_file = MIGRATIONS_DIR / MIGRATION_FILES[version]
            sql = sql_file.read_text(encoding="utf-8")
            conn.executescript(sql)  # executescript 自动 commit，且会设置 user_version

        # 服务启动时清空 active_observers（易失数据，不保留跨重启）
        conn.execute("DELETE FROM active_observers;")
        conn.commit()
    finally:
        conn.close()
