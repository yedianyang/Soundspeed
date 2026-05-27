"""迁移执行器骨架（TDD 红阶段，方法体尚未实现）。"""
from __future__ import annotations

from pathlib import Path


def apply_migrations(db_path: Path) -> None:
    """
    将数据库升级到最新 schema 版本。
    使用 PRAGMA user_version 记录已应用版本号。
    幂等：已应用的迁移不会重复执行。

    Args:
        db_path: SQLite 数据库文件路径。文件不存在时自动创建。
    """
    raise NotImplementedError
