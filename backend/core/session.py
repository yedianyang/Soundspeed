"""SessionState：运行时会话状态缓存。

记录当前 take、场次、连接等运行时状态。
不持有 DAL 引用，所有 DAL 写由 Orchestrator handler 负责。
MVP 假设 publish 串行，无并发写风险，不加线程锁。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionState:
    """运行时会话状态。字段含义见 system-architecture v0.1 §4（ch2_buffer 已删，v0.3 修订）。"""

    scene_id: int | None = None
    shot: str | None = None
    take_id: int | None = None
    take_number: int = 0
    take_active: bool = False
    take_start_ts: float | None = None
    script_loaded: bool = False
    active_connections: set[str] = field(default_factory=set)

    def take_start(
        self,
        take_id: int,
        take_number: int,
        start_ts: float,
        shot: str | None,
    ) -> None:
        """记录 take 开始，更新 take_id / take_number / take_start_ts / shot / take_active。"""
        raise NotImplementedError("1.E green")

    def take_end(self) -> None:
        """记录 take 结束：take_active=False，take_id 保留（1.H 写 takes.end_ts 还要用）。"""
        raise NotImplementedError("1.E green")

    def activate_scene(self, scene_id: int) -> None:
        """设置当前活跃 scene_id（不写 DAL）。"""
        raise NotImplementedError("1.E green")

    def load_script(self) -> None:
        """标记剧本已加载。"""
        raise NotImplementedError("1.E green")

    def register_observer(self, connection_id: str) -> None:
        """注册观察者连接 ID 到 active_connections。"""
        raise NotImplementedError("1.E green")

    def unregister_observer(self, connection_id: str) -> None:
        """从 active_connections 中移除观察者连接 ID。"""
        raise NotImplementedError("1.E green")
