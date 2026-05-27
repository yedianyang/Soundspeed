"""数据访问层骨架（TDD 红阶段，方法体尚未实现）。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ── 数据类（read 方法的返回类型）────────────────────────────────────────────


@dataclass
class Take:
    take_id: int
    scene_id: int
    take_number: int
    shot: str | None
    start_ts: float
    end_ts: float | None
    status: str  # 'keeper' | 'ng' | 'hold' | 'tbd'
    performer_issues: dict | None  # NP 解析输出，DAL 负责 json.loads；写入时也传 dict
    audio_quality: str | None
    script_diff: dict | None  # L2 输出，DAL 负责 json.loads；写入时也传 dict
    notes: str | None
    created_at: float
    updated_at: float


@dataclass
class TranscriptSegment:
    segment_id: int
    take_id: int
    ch: int  # 1 或 2
    speaker: str | None
    text: str
    start_frame: int
    end_frame: int
    created_at: float


@dataclass
class ScriptLine:
    line_id: int
    script_id: int
    line_no: int
    character: str | None
    text: str
    created_at: float


@dataclass
class TakeEvent:
    event_id: int
    take_id: int
    event_type: str
    ts: float
    payload: dict[str, Any]
    created_at: float


# ── DAL 类 ──────────────────────────────────────────────────────────────────


class DAL:
    """
    数据访问层。所有数据库读写必须通过此类，不允许外部拼接 SQL。
    构造时传入数据库文件路径，自动应用迁移（调用 apply_migrations）。
    """

    def __init__(self, db_path: Path) -> None:
        """
        初始化 DAL，自动调用 apply_migrations 确保 schema 最新。
        每次 sqlite3.connect() 后必须立即执行以下 per-connection PRAGMA，
        否则外键约束不生效、WAL busy_timeout 不起效：
            PRAGMA foreign_keys = ON;
            PRAGMA journal_mode = WAL;
            PRAGMA busy_timeout = 5000;
        """
        raise NotImplementedError

    # ── scenes ──────────────────────────────────────────────────────────────

    def create_scene(
        self,
        scene_code: str,
        description: str | None = None,
        shoot_date: str | None = None,
    ) -> int:
        """创建场次，返回 scene_id。"""
        raise NotImplementedError

    def set_active_scene(self, scene_id: int) -> None:
        """将指定 scene_id 设为活跃场次，清除其他场次的 is_active。"""
        raise NotImplementedError

    def get_active_scene_id(self) -> int | None:
        """返回当前活跃场次 ID，无则返回 None。"""
        raise NotImplementedError

    def list_scenes(self) -> list[dict]:
        """返回所有场次的基本信息列表。"""
        raise NotImplementedError

    # ── takes ────────────────────────────────────────────────────────────────

    def start_take(
        self,
        scene_id: int,
        take_number: int,
        start_ts: float,
        shot: str | None = None,
    ) -> int:
        """新建 take 行，返回 take_id。"""
        raise NotImplementedError

    def end_take(
        self,
        take_id: int,
        end_ts: float,
        status: str,
        script_diff: dict | None = None,
        notes: str | None = None,
    ) -> None:
        """
        更新 take 结束时间、状态、L2 输出。
        script_diff 传 dict，DAL 内部 json.dumps 后存库；读取时（get_take / list_takes）
        DAL 内部 json.loads 还原为 dict，保持调用方口径一致。
        """
        raise NotImplementedError

    def update_take_np_output(
        self,
        take_id: int,
        performer_issues: str | None,
        audio_quality: str | None,
        status: str | None,
    ) -> None:
        """NP Pipeline 写入结构化字段，不覆盖 end_ts。"""
        raise NotImplementedError

    def get_take(self, take_id: int) -> Take | None:
        """按 take_id 获取单条 take，不存在返回 None。"""
        raise NotImplementedError

    def list_takes(self, scene_id: int | None = None) -> list[Take]:
        """返回 take 列表，可按 scene_id 过滤，按 take_number 升序。"""
        raise NotImplementedError

    # ── take_events ──────────────────────────────────────────────────────────

    def insert_take_event(
        self,
        take_id: int,
        event_type: str,
        payload: dict,
        ts: float,
    ) -> int:
        """写入 take 事件行，返回 event_id。"""
        raise NotImplementedError

    def list_take_events(
        self,
        take_id: int,
        event_type: str | None = None,
    ) -> list[TakeEvent]:
        """返回某 take 的事件列表，可按 event_type 过滤，按 ts 升序。"""
        raise NotImplementedError

    # ── transcript_segments ──────────────────────────────────────────────────

    def insert_segment(
        self,
        take_id: int,
        ch: int,
        speaker: str | None,
        text: str,
        start_frame: int,
        end_frame: int,
    ) -> int:
        """写入一条转录片段，返回 segment_id。ch 必须为 1 或 2。"""
        raise NotImplementedError

    def list_segments(
        self,
        take_id: int,
        ch: int | None = None,
        speaker: str | None = None,
    ) -> list[TranscriptSegment]:
        """
        返回某 take 的转录片段列表。
        ch=None 表示不过滤声道。
        """
        raise NotImplementedError

    # ── scripts ──────────────────────────────────────────────────────────────

    def insert_script(
        self,
        scene_id: int,
        raw_text: str,
        version: int | None = None,
    ) -> int:
        """
        插入剧本原文，返回 script_id。
        version=None 时自动取该场次最大版本 +1。
        """
        raise NotImplementedError

    def get_latest_script(self, scene_id: int) -> dict | None:
        """返回场次最新版本剧本（script_id + raw_text），无则返回 None。"""
        raise NotImplementedError

    # ── script_lines ─────────────────────────────────────────────────────────

    def insert_script_line(
        self,
        script_id: int,
        line_no: int,
        character: str | None,
        text: str,
    ) -> int:
        """插入一行台词，返回 line_id。FTS5 触发器自动同步。"""
        raise NotImplementedError

    def match_script_line(
        self,
        query: str,
        scene_id: int | None = None,
    ) -> list[ScriptLine]:
        """
        用 FTS5 MATCH 检索台词，返回匹配行列表（按 BM25 排序）。
        scene_id 不为 None 时限制在该场次剧本内。
        """
        raise NotImplementedError

    # ── take_line_matches ────────────────────────────────────────────────────

    def insert_take_line_match(
        self,
        take_id: int,
        line_id: int,
        diff_type: str,
        payload: dict,
    ) -> int:
        """写入 take-剧本行比对结果，返回 match_id。"""
        raise NotImplementedError

    def list_take_line_matches(self, take_id: int) -> list[dict]:
        """返回某 take 的所有偏差记录，含 line_id + diff_type + payload。"""
        raise NotImplementedError

    # ── active_observers ─────────────────────────────────────────────────────

    def upsert_observer(self, connection_id: str, name: str) -> None:
        """插入或更新观察者记录（INSERT OR REPLACE）。"""
        raise NotImplementedError

    def remove_observer(self, connection_id: str) -> None:
        """删除观察者记录。"""
        raise NotImplementedError

    def list_observers(self) -> list[dict]:
        """返回当前所有在线观察者列表。"""
        raise NotImplementedError

    # ── audit_log ─────────────────────────────────────────────────────────────

    def append_audit(
        self,
        actor: str,
        action: str,
        payload: dict,
    ) -> int:
        """追加一条审计日志，返回 log_id。"""
        raise NotImplementedError
