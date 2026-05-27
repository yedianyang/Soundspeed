"""应用生命周期钩子。

此模块提供服务启动/关闭时对 volatile 状态的显式清理操作。
DAL 构造本身不调用这里的函数——只有 FastAPI startup hook 在服务启动时显式调用一次。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def purge_volatile_tables(db_path: Path) -> None:
    """清空跨重启不保留的 volatile 表。

    当前清空 active_observers（WebSocket 连接状态，重启后全部失效）。
    未来如有其他 volatile 表，在此函数中追加。

    此函数由 FastAPI startup hook 显式调用一次，DAL 构造不调。
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("DELETE FROM active_observers;")
        conn.commit()
    finally:
        conn.close()
