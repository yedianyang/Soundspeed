"""数据访问层（DAL）。

所有数据库读写通过此类进行，不允许外部拼接 SQL。
构造时传入数据库文件路径，自动调用 apply_migrations 确保 schema 最新。
写操作使用 BEGIN IMMEDIATE 显式事务，避免 WAL 模式下隐式事务竞争。
JSON 字段在 DAL 内部透明处理：写入时 json.dumps，读取时 json.loads。
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.db.lifecycle import _configure_connection
from backend.db.migrations.runner import apply_migrations

# SQLite 3.39.4 不支持 unixepoch('now', 'subsec')，用 strftime 兼容写法
_NOW_TS_SQL = "CAST(strftime('%s', 'now') AS REAL)"


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
    performer_issues: dict | list | None  # NP 解析输出，DAL 负责 json.loads；写入时也传 dict/list
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
    start_frame: int  # 毫秒（秒 × 1000 取整），字段名沿用历史命名
    end_frame: int    # 毫秒（秒 × 1000 取整），字段名沿用历史命名
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


# ── 自定义异常 ────────────────────────────────────────────────────────────────


class TakeNumberConflictError(Exception):
    """跨场移动时目标 (scene_id, take_number) 已被占用，无法自动解决冲突。

    上层路由应将此异常映射为 HTTP 409。
    """


# ── 内部辅助 ──────────────────────────────────────────────────────────────────


def _row_to_take(row: sqlite3.Row) -> Take:
    performer_issues = row["performer_issues"]
    script_diff = row["script_diff"]
    return Take(
        take_id=row["take_id"],
        scene_id=row["scene_id"],
        take_number=row["take_number"],
        shot=row["shot"],
        start_ts=row["start_ts"],
        end_ts=row["end_ts"],
        status=row["status"],
        performer_issues=json.loads(performer_issues) if performer_issues else None,
        audio_quality=row["audio_quality"],
        script_diff=json.loads(script_diff) if script_diff else None,
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_segment(row: sqlite3.Row) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id=row["segment_id"],
        take_id=row["take_id"],
        ch=row["ch"],
        speaker=row["speaker"],
        text=row["text"],
        start_frame=row["start_frame"],
        end_frame=row["end_frame"],
        created_at=row["created_at"],
    )


def _row_to_event(row: sqlite3.Row) -> TakeEvent:
    return TakeEvent(
        event_id=row["event_id"],
        take_id=row["take_id"],
        event_type=row["event_type"],
        ts=row["ts"],
        payload=json.loads(row["payload"]),
        created_at=row["created_at"],
    )


def _row_to_script_line(row: sqlite3.Row) -> ScriptLine:
    return ScriptLine(
        line_id=row["line_id"],
        script_id=row["script_id"],
        line_no=row["line_no"],
        character=row["character"],
        text=row["text"],
        created_at=row["created_at"],
    )


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
        apply_migrations(db_path)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        _configure_connection(self._conn)

    # ── 内部事务 helper ───────────────────────────────────────────────────────

    @contextmanager
    def _write_tx(self) -> Iterator[sqlite3.Connection]:
        """写事务 context manager：BEGIN IMMEDIATE 显式加锁，避免 WAL 下隐式事务竞争。"""
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE;")
            yield self._conn

    # ── 资源管理 ──────────────────────────────────────────────────────────────

    def close(self) -> None:
        """关闭底层 sqlite 连接。"""
        self._conn.close()

    def __enter__(self) -> "DAL":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()

    # ── scenes ──────────────────────────────────────────────────────────────

    def create_scene(
        self,
        scene_code: str,
        description: str | None = None,
        shoot_date: str | None = None,
    ) -> int:
        """创建场次，返回 scene_id。"""
        with self._write_tx() as conn:
            cur = conn.execute(
                "INSERT INTO scenes (scene_code, description, shoot_date) VALUES (?, ?, ?);",
                (scene_code, description, shoot_date),
            )
        return cur.lastrowid  # type: ignore[return-value]

    def set_active_scene(self, scene_id: int) -> None:
        """将指定 scene_id 设为活跃场次，清除其他场次的 is_active。"""
        with self._write_tx() as conn:
            conn.execute("UPDATE scenes SET is_active = 0;")
            conn.execute(
                "UPDATE scenes SET is_active = 1 WHERE scene_id = ?;", (scene_id,)
            )

    def get_active_scene_id(self) -> int | None:
        """返回当前活跃场次 ID，无则返回 None。"""
        row = self._conn.execute(
            "SELECT scene_id FROM scenes WHERE is_active = 1 LIMIT 1;"
        ).fetchone()
        return row["scene_id"] if row else None

    def list_scenes(self) -> list[dict]:
        """返回所有场次的基本信息列表（含 slugline 三列）。"""
        rows = self._conn.execute(
            "SELECT scene_id, scene_code, description, shoot_date, is_active, created_at, "
            "int_ext, time_of_day, location "
            "FROM scenes ORDER BY scene_id ASC;"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_scene_heading(
        self,
        scene_id: int,
        *,
        int_ext: str | None = None,
        time_of_day: str | None = None,
        location: str | None = None,
    ) -> None:
        """部分更新场次 slugline 字段。

        只写非 None 且非空白的字段（COALESCE 保留原值），避免把未传字段清空。
        None 或空白字符串（包括空串 "" 和纯空白 "   "）均视为「不更新该字段，保留原值」。
        三者全为 None 或空白时等同 no-op。
        """
        def _normalize(v: str | None) -> str | None:
            """空串/纯空白归一为 None，以便 COALESCE 保留原值。"""
            if v is None:
                return None
            return v if v.strip() else None

        with self._write_tx() as conn:
            conn.execute(
                "UPDATE scenes SET "
                "int_ext     = COALESCE(?, int_ext), "
                "time_of_day = COALESCE(?, time_of_day), "
                "location    = COALESCE(?, location) "
                "WHERE scene_id = ?;",
                (_normalize(int_ext), _normalize(time_of_day), _normalize(location), scene_id),
            )

    # ── takes ────────────────────────────────────────────────────────────────

    def start_take(
        self,
        scene_id: int,
        take_number: int,
        start_ts: float,
        shot: str | None = None,
    ) -> int:
        """新建 take 行，返回 take_id。"""
        with self._write_tx() as conn:
            cur = conn.execute(
                "INSERT INTO takes (scene_id, take_number, start_ts, shot) "
                "VALUES (?, ?, ?, ?);",
                (scene_id, take_number, start_ts, shot),
            )
        return cur.lastrowid  # type: ignore[return-value]

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
        script_diff 传 dict，DAL 内部 json.dumps 后存库；读取时 json.loads 还原。
        """
        script_diff_json = json.dumps(script_diff) if script_diff is not None else None
        with self._write_tx() as conn:
            conn.execute(
                f"UPDATE takes SET end_ts = ?, status = ?, script_diff = ?, notes = ?, "
                f"updated_at = {_NOW_TS_SQL} "
                f"WHERE take_id = ?;",
                (end_ts, status, script_diff_json, notes, take_id),
            )

    def update_take_np_output(
        self,
        take_id: int,
        performer_issues: dict | list | None,
        audio_quality: str | None,
        status: str | None,
    ) -> None:
        """NP Pipeline 写入结构化字段，不覆盖 end_ts。

        performer_issues 传 dict/list，DAL 内部 json.dumps 后存库；
        读取时 _row_to_take 会 json.loads 还原。
        """
        performer_issues_json = (
            json.dumps(performer_issues) if performer_issues is not None else None
        )
        with self._write_tx() as conn:
            conn.execute(
                f"UPDATE takes SET performer_issues = ?, audio_quality = ?, "
                f"status = COALESCE(?, status), "
                f"updated_at = {_NOW_TS_SQL} "
                f"WHERE take_id = ?;",
                (performer_issues_json, audio_quality, status, take_id),
            )

    def get_take(self, take_id: int) -> Take | None:
        """按 take_id 获取单条 take，不存在返回 None。"""
        row = self._conn.execute(
            "SELECT * FROM takes WHERE take_id = ?;", (take_id,)
        ).fetchone()
        return _row_to_take(row) if row else None

    def list_takes(self, scene_id: int | None = None) -> list[Take]:
        """返回 take 列表，可按 scene_id 过滤，按 take_number 升序。"""
        if scene_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM takes WHERE scene_id = ? ORDER BY take_number ASC;",
                (scene_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM takes ORDER BY take_number ASC;"
            ).fetchall()
        return [_row_to_take(r) for r in rows]

    # ── take_events ──────────────────────────────────────────────────────────

    def insert_take_event(
        self,
        take_id: int,
        event_type: str,
        payload: dict,
        ts: float,
    ) -> int:
        """写入 take 事件行，返回 event_id。"""
        payload_json = json.dumps(payload)
        with self._write_tx() as conn:
            cur = conn.execute(
                "INSERT INTO take_events (take_id, event_type, payload, ts) "
                "VALUES (?, ?, ?, ?);",
                (take_id, event_type, payload_json, ts),
            )
        return cur.lastrowid  # type: ignore[return-value]

    def list_take_events(
        self,
        take_id: int,
        event_type: str | None = None,
    ) -> list[TakeEvent]:
        """返回某 take 的事件列表，可按 event_type 过滤，按 ts 升序。"""
        if event_type is not None:
            rows = self._conn.execute(
                "SELECT * FROM take_events WHERE take_id = ? AND event_type = ? "
                "ORDER BY ts ASC;",
                (take_id, event_type),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM take_events WHERE take_id = ? ORDER BY ts ASC;",
                (take_id,),
            ).fetchall()
        return [_row_to_event(r) for r in rows]

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
        """写入一条转录片段，返回 segment_id。ch 必须为 1 或 2。

        start_frame / end_frame 单位为毫秒（秒 × 1000 取整），字段名沿用历史命名。
        """
        with self._write_tx() as conn:
            cur = conn.execute(
                "INSERT INTO transcript_segments "
                "(take_id, ch, speaker, text, start_frame, end_frame) "
                "VALUES (?, ?, ?, ?, ?, ?);",
                (take_id, ch, speaker, text, start_frame, end_frame),
            )
        return cur.lastrowid  # type: ignore[return-value]

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
        base = "SELECT * FROM transcript_segments WHERE take_id = ?"
        params: list[Any] = [take_id]
        if ch is not None:
            base += " AND ch = ?"
            params.append(ch)
        if speaker is not None:
            base += " AND speaker = ?"
            params.append(speaker)
        base += " ORDER BY start_frame ASC;"
        rows = self._conn.execute(base, params).fetchall()
        return [_row_to_segment(r) for r in rows]

    def get_segment(self, segment_id: int) -> TranscriptSegment | None:
        """按 segment_id 获取单条片段，不存在返回 None。"""
        row = self._conn.execute(
            "SELECT * FROM transcript_segments WHERE segment_id = ?;",
            (segment_id,),
        ).fetchone()
        return _row_to_segment(row) if row else None

    def update_segment_speaker(self, segment_id: int, speaker: str | None) -> int:
        """改单条片段的 speaker，返回受影响行数（0 = segment 不存在）。

        归属（take 匹配）与 ch1 限制由 route 层先用 get_segment 校验，不进 WHERE。
        speaker=None 表示置「未知」（schema 允许 NULL）。
        """
        with self._write_tx() as conn:
            cur = conn.execute(
                "UPDATE transcript_segments SET speaker = ? WHERE segment_id = ?;",
                (speaker, segment_id),
            )
        return cur.rowcount

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
        with self._write_tx() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT COALESCE(MAX(version), 0) AS max_v FROM scripts "
                    "WHERE scene_id = ?;",
                    (scene_id,),
                ).fetchone()
                version = row["max_v"] + 1
            cur = conn.execute(
                "INSERT INTO scripts (scene_id, raw_text, version) VALUES (?, ?, ?);",
                (scene_id, raw_text, version),
            )
        return cur.lastrowid  # type: ignore[return-value]

    def get_latest_script(self, scene_id: int) -> dict | None:
        """返回场次最新版本剧本（script_id + raw_text），无则返回 None。"""
        row = self._conn.execute(
            "SELECT script_id, raw_text, version FROM scripts "
            "WHERE scene_id = ? ORDER BY version DESC LIMIT 1;",
            (scene_id,),
        ).fetchone()
        return dict(row) if row else None

    # ── script_lines ─────────────────────────────────────────────────────────

    def insert_script_line(
        self,
        script_id: int,
        line_no: int,
        character: str | None,
        text: str,
    ) -> int:
        """插入一行台词，返回 line_id。FTS5 触发器自动同步。"""
        with self._write_tx() as conn:
            cur = conn.execute(
                "INSERT INTO script_lines (script_id, line_no, character, text) "
                "VALUES (?, ?, ?, ?);",
                (script_id, line_no, character, text),
            )
        return cur.lastrowid  # type: ignore[return-value]

    def match_script_line(
        self,
        query: str,
        scene_id: int | None = None,
    ) -> list[ScriptLine]:
        """
        用 FTS5 MATCH 检索台词，返回匹配行列表（按 BM25 排序）。
        scene_id 不为 None 时限制在该场次剧本内。
        """
        if scene_id is not None:
            rows = self._conn.execute(
                "SELECT sl.line_id, sl.script_id, sl.line_no, sl.character, sl.text, sl.created_at "
                "FROM script_lines_fts fts "
                "JOIN script_lines sl ON sl.line_id = fts.rowid "
                "JOIN scripts s ON s.script_id = sl.script_id "
                "WHERE fts.text MATCH ? AND s.scene_id = ? "
                "ORDER BY bm25(script_lines_fts);",
                (query, scene_id),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT sl.line_id, sl.script_id, sl.line_no, sl.character, sl.text, sl.created_at "
                "FROM script_lines_fts fts "
                "JOIN script_lines sl ON sl.line_id = fts.rowid "
                "WHERE fts.text MATCH ? "
                "ORDER BY bm25(script_lines_fts);",
                (query,),
            ).fetchall()
        return [_row_to_script_line(r) for r in rows]

    # ── take_line_matches ────────────────────────────────────────────────────

    def insert_take_line_match(
        self,
        take_id: int,
        line_id: int,
        diff_type: str,
        payload: dict,
    ) -> int:
        """写入 take-剧本行比对结果，返回 match_id。"""
        payload_json = json.dumps(payload)
        with self._write_tx() as conn:
            cur = conn.execute(
                "INSERT INTO take_line_matches (take_id, line_id, diff_type, payload) "
                "VALUES (?, ?, ?, ?);",
                (take_id, line_id, diff_type, payload_json),
            )
        return cur.lastrowid  # type: ignore[return-value]

    def list_take_line_matches(self, take_id: int) -> list[dict]:
        """返回某 take 的所有偏差记录，含 line_id + diff_type + payload。"""
        rows = self._conn.execute(
            "SELECT match_id, take_id, line_id, diff_type, payload, created_at "
            "FROM take_line_matches WHERE take_id = ? ORDER BY match_id ASC;",
            (take_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            result.append(d)
        return result

    # ── active_observers ─────────────────────────────────────────────────────

    def upsert_observer(self, connection_id: str, name: str) -> None:
        """插入或更新观察者记录（INSERT OR REPLACE）。"""
        with self._write_tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO active_observers (connection_id, name) "
                "VALUES (?, ?);",
                (connection_id, name),
            )

    def remove_observer(self, connection_id: str) -> None:
        """删除观察者记录。"""
        with self._write_tx() as conn:
            conn.execute(
                "DELETE FROM active_observers WHERE connection_id = ?;",
                (connection_id,),
            )

    def list_observers(self) -> list[dict]:
        """返回当前所有在线观察者列表。"""
        rows = self._conn.execute(
            "SELECT connection_id, name, joined_at FROM active_observers ORDER BY joined_at ASC;"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── audit_log ─────────────────────────────────────────────────────────────

    def append_audit(
        self,
        actor: str,
        action: str,
        payload: dict,
    ) -> int:
        """追加一条审计日志，返回 log_id。"""
        payload_json = json.dumps(payload)
        with self._write_tx() as conn:
            cur = conn.execute(
                "INSERT INTO audit_log (actor, action, payload) VALUES (?, ?, ?);",
                (actor, action, payload_json),
            )
        return cur.lastrowid  # type: ignore[return-value]

    # ── L2 Pipeline 写入（1.H）──────────────────────────────────────────────────

    def update_take_l2_output(
        self,
        take_id: int,
        script_diff: dict | None,
    ) -> None:
        """L2 Pipeline 写入 script_diff 字段。

        dict 走 json.dumps，None 写 NULL。
        """
        script_diff_json = json.dumps(script_diff) if script_diff is not None else None
        with self._write_tx() as conn:
            conn.execute(
                f"UPDATE takes SET script_diff = ?, updated_at = {_NOW_TS_SQL} WHERE take_id = ?;",
                (script_diff_json, take_id),
            )

    def insert_take_line_matches(
        self,
        take_id: int,
        matches: list[dict],
    ) -> None:
        """批量写入 take_line_matches。

        过滤 line_no==-1 的 insertion（按 l2-pipeline §D5 决策）。
        matches 每项需含：line_no / line_id / diff_type / detail（可选）。
        line_id 为 FK to script_lines，caller 负责在传入前做 line_no → line_id 映射。
        """
        rows_to_insert = [m for m in matches if m.get("line_no") != -1]
        if not rows_to_insert:
            return
        with self._write_tx() as conn:
            for m in rows_to_insert:
                detail = m.get("detail")
                payload_json = json.dumps({"detail": detail}) if detail is not None else "{}"
                conn.execute(
                    "INSERT INTO take_line_matches (take_id, line_id, diff_type, payload) "
                    "VALUES (?, ?, ?, ?);",
                    (take_id, m["line_id"], m["diff_type"], payload_json),
                )

    def list_script_lines(self, script_id: int) -> list[dict]:
        """返回剧本行列表，含 line_no / line_id / character / text，按 line_no ASC。"""
        rows = self._conn.execute(
            "SELECT line_id, line_no, character, text FROM script_lines "
            "WHERE script_id = ? ORDER BY line_no ASC;",
            (script_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── 2.B 新增方法 ─────────────────────────────────────────────────────────

    def set_take_status(self, take_id: int, status: str) -> None:
        """更新 take 的 status，并在 take_events 写一条 manual.mark 事件。

        status 必须是 'keeper' / 'ng' / 'hold' / 'tbd' 之一，否则抛 ValueError。
        单事务内完成：UPDATE takes + INSERT take_events（inline，不调 insert_take_event 避免嵌套事务）。
        """
        _VALID_STATUSES = {"keeper", "ng", "hold", "tbd"}
        if status not in _VALID_STATUSES:
            raise ValueError(
                f"非法 status 值 {status!r}，必须是 {sorted(_VALID_STATUSES)} 之一"
            )
        payload_json = json.dumps({"status": status})
        with self._write_tx() as conn:
            conn.execute(
                f"UPDATE takes SET status = ?, updated_at = {_NOW_TS_SQL} WHERE take_id = ?;",
                (status, take_id),
            )
            conn.execute(
                f"INSERT INTO take_events (take_id, event_type, ts, payload) "
                f"VALUES (?, 'manual.mark', {_NOW_TS_SQL}, ?);",
                (take_id, payload_json),
            )

    def update_take_meta(
        self,
        take_id: int,
        *,
        shot: str | None = None,
        scene_id: int | None = None,
        take_number: int | None = None,
        notes: str | None = None,
    ) -> None:
        """部分更新 take 元数据，处理 UNIQUE(scene_id, take_number) 冲突。在单个事务内完成。

        字段语义：
        - shot：None 表示不改，空串 "" 是合法值（清空 shot 标注）。
        - notes：None 表示不改，空串 "" 是合法值（清空备注）。
        - scene_id：目标场次 ID，None 表示不改（保持当前场）。目标 scene 不存在时抛 ValueError。
        - take_number：目标编号，None 表示不改或由冲突算法自动计算。

        冲突处理（参见 spec §6）：
        - 情形 A：仅改 scene_id（无 take_number）→ 追加为目标场 MAX(take_number)+1（空场为 1）。
        - 情形 B：同场改 take_number 且目标号被占用 → 三步 -1 占位交换两条 take 编号。
        - 情形 C：跨场 + 指定 take_number 且目标已占用 → 抛 TakeNumberConflictError（不做跨场交换）。
        - 情形 D：仅改 take_number 且目标号空闲 → 直接 UPDATE。

        成功后写一条 take_events（event_type='manual.edit'），payload 含 changed_fields 列表
        和 conflict_resolution（'append' / 'swap' / 'none'）。

        status 字段不在本方法，走 set_take_status。
        """
        # 取当前 take 快照（事务外读，仅用于后续判断，不影响事务隔离）
        row = self._conn.execute(
            "SELECT scene_id, take_number FROM takes WHERE take_id = ?;",
            (take_id,),
        ).fetchone()
        if row is None:
            # take 不存在，no-op（与 delete_take 一致的静默策略）
            return

        cur_scene: int = row["scene_id"]
        cur_number: int = row["take_number"]

        # 确定目标 scene
        target_scene = scene_id if scene_id is not None else cur_scene
        is_cross_scene = (scene_id is not None) and (scene_id != cur_scene)

        with self._write_tx() as conn:
            # 情形 A/C：涉及 scene_id 变更，先校验目标 scene 存在
            if scene_id is not None:
                exists = conn.execute(
                    "SELECT 1 FROM scenes WHERE scene_id = ?;", (scene_id,)
                ).fetchone()
                if not exists:
                    raise ValueError(f"目标 scene_id={scene_id} 不存在")

            # 计算目标编号及冲突解决方式
            conflict_resolution = "none"
            target_number: int

            if take_number is None:
                if is_cross_scene:
                    # 情形 A：移场，追加到目标场下一号
                    next_row = conn.execute(
                        "SELECT COALESCE(MAX(take_number), 0) + 1 AS next_num "
                        "FROM takes WHERE scene_id = ?;",
                        (target_scene,),
                    ).fetchone()
                    target_number = next_row["next_num"]
                    conflict_resolution = "append"
                else:
                    # 无 take_number，无跨场 → 不改编号
                    target_number = cur_number
            else:
                target_number = take_number
                if target_number != cur_number or is_cross_scene:
                    # 检查目标 (scene, number) 是否已被占用（排除自己）
                    occupied_row = conn.execute(
                        "SELECT take_id FROM takes "
                        "WHERE scene_id = ? AND take_number = ? AND take_id != ?;",
                        (target_scene, target_number, take_id),
                    ).fetchone()
                    if occupied_row is not None:
                        if is_cross_scene:
                            # 情形 C：跨场冲突，不做交换
                            raise TakeNumberConflictError(
                                f"目标 (scene_id={target_scene}, take_number={target_number}) "
                                "已被占用，跨场交换不支持，请先清理目标编号"
                            )
                        else:
                            # 情形 B：同场交换
                            occupied_take_id: int = occupied_row["take_id"]
                            conn.execute(
                                "UPDATE takes SET take_number = -1 WHERE take_id = ?;",
                                (occupied_take_id,),
                            )
                            conn.execute(
                                f"UPDATE takes SET take_number = ?, updated_at = {_NOW_TS_SQL} "
                                f"WHERE take_id = ?;",
                                (target_number, take_id),
                            )
                            conn.execute(
                                f"UPDATE takes SET take_number = ?, updated_at = {_NOW_TS_SQL} "
                                f"WHERE take_id = ?;",
                                (cur_number, occupied_take_id),
                            )
                            conflict_resolution = "swap"

            # 汇总需要更新的字段
            set_clauses: list[str] = []
            params: list[Any] = []

            if scene_id is not None:
                set_clauses.append("scene_id = ?")
                params.append(target_scene)
            if target_number != cur_number and conflict_resolution != "swap":
                # swap 情形已在上面处理，其余情形（append / none+改号）在此处理
                set_clauses.append("take_number = ?")
                params.append(target_number)
            if shot is not None:
                set_clauses.append("shot = ?")
                params.append(shot)
            if notes is not None:
                set_clauses.append("notes = ?")
                params.append(notes)

            # 记录本次改动的业务字段（供 take_events payload）
            changed_fields: list[str] = []
            if scene_id is not None:
                changed_fields.append("scene_id")
            if take_number is not None and target_number != cur_number:
                changed_fields.append("take_number")
            if is_cross_scene and take_number is None:
                changed_fields.append("take_number")  # append 也改了编号
            if shot is not None:
                changed_fields.append("shot")
            if notes is not None:
                changed_fields.append("notes")

            if set_clauses or conflict_resolution == "swap":
                if set_clauses:
                    set_clauses.append(f"updated_at = {_NOW_TS_SQL}")
                    params.append(take_id)
                    conn.execute(
                        f"UPDATE takes SET {', '.join(set_clauses)} WHERE take_id = ?;",
                        params,
                    )

            # 写 take_events（manual.edit），inline 不调 insert_take_event
            event_payload_json = json.dumps(
                {
                    "changed_fields": changed_fields,
                    "conflict_resolution": conflict_resolution,
                }
            )
            conn.execute(
                f"INSERT INTO take_events (take_id, event_type, ts, payload) "
                f"VALUES (?, 'manual.edit', {_NOW_TS_SQL}, ?);",
                (take_id, event_payload_json),
            )

    def delete_take(self, take_id: int) -> None:
        """硬删 take，子表靠 ON DELETE CASCADE 自动清，同时在 audit_log 留审计快照。

        执行顺序（单事务）：SELECT 快照 → INSERT audit_log → DELETE takes。
        take_events 对 take_id 是 CASCADE，随 DELETE 一并清除，审计靠 audit_log 保全。
        take 不存在时静默 no-op（取快照为空，不执行任何写操作）。
        """
        with self._write_tx() as conn:
            snapshot = conn.execute(
                "SELECT take_id, scene_id, take_number, status, shot, notes, "
                "start_ts, end_ts FROM takes WHERE take_id = ?;",
                (take_id,),
            ).fetchone()
            if snapshot is None:
                # take 不存在，静默 no-op
                return

            audit_payload = json.dumps(
                {
                    "take_id": snapshot["take_id"],
                    "scene_id": snapshot["scene_id"],
                    "take_number": snapshot["take_number"],
                    "status": snapshot["status"],
                    "shot": snapshot["shot"],
                    "notes": snapshot["notes"],
                    "start_ts": snapshot["start_ts"],
                    "end_ts": snapshot["end_ts"],
                }
            )
            conn.execute(
                f"INSERT INTO audit_log (actor, action, payload, ts) "
                f"VALUES ('user', 'take.delete', ?, {_NOW_TS_SQL});",
                (audit_payload,),
            )
            conn.execute(
                "DELETE FROM takes WHERE take_id = ?;",
                (take_id,),
            )
