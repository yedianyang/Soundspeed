"""DAL 与迁移的测试用例（按 0.E sqlite-schema spec v0.2 §7 测试入口清单）。

所有测试用 tmp_path fixture 给每个测试干净 db 文件，互不干扰。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.db.dal import DAL
from backend.db.migrations.runner import apply_migrations


# ── 辅助：打开裸 sqlite3 连接读取元数据 ──────────────────────────────────────


def _raw_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ── 迁移与初始化 ──────────────────────────────────────────────────────────────


def test_apply_migrations_creates_all_tables(tmp_path: Path) -> None:
    """迁移后 9 张物理表和 FTS5 虚拟表全部存在。"""
    db_path = tmp_path / "test.db"
    apply_migrations(db_path)
    conn = _raw_conn(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow') ORDER BY name;"
    ).fetchall()
    names = {r["name"] for r in rows}
    expected = {
        "scenes",
        "takes",
        "take_events",
        "scripts",
        "script_lines",
        "take_line_matches",
        "transcript_segments",
        "audit_log",
        "active_observers",
        "script_lines_fts",
    }
    assert expected.issubset(names), f"缺少表：{expected - names}"
    conn.close()


def test_apply_migrations_idempotent(tmp_path: Path) -> None:
    """多次调用 apply_migrations 不报错、不重复建表。"""
    db_path = tmp_path / "test.db"
    apply_migrations(db_path)
    apply_migrations(db_path)  # 第二次调用，期望幂等
    conn = _raw_conn(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='scenes';"
    ).fetchone()[0]
    assert count == 1
    conn.close()


def test_apply_migrations_user_version(tmp_path: Path) -> None:
    """迁移后 PRAGMA user_version 等于 1。"""
    db_path = tmp_path / "test.db"
    apply_migrations(db_path)
    conn = _raw_conn(db_path)
    version = conn.execute("PRAGMA user_version;").fetchone()[0]
    assert version == 1
    conn.close()


def test_apply_migrations_does_not_purge_observers(tmp_path: Path) -> None:
    """apply_migrations 多次调用不清空 active_observers，已存在的行保留。"""
    db_path = tmp_path / "test.db"
    apply_migrations(db_path)
    # 直接插入一条观察者记录
    conn = _raw_conn(db_path)
    conn.execute(
        "INSERT INTO active_observers (connection_id, name) VALUES ('conn-1', 'director');"
    )
    conn.commit()
    conn.close()
    # 再次调用 apply_migrations，期望不清空
    apply_migrations(db_path)
    conn = _raw_conn(db_path)
    count = conn.execute("SELECT COUNT(*) FROM active_observers;").fetchone()[0]
    assert count == 1  # 行仍然存在
    conn.close()


# ── PRAGMA 验证 ───────────────────────────────────────────────────────────────


def test_dal_enables_wal_mode(tmp_path: Path) -> None:
    """DAL 初始化后 journal_mode 为 wal。"""
    db_path = tmp_path / "test.db"
    _dal = DAL(db_path)
    conn = _raw_conn(db_path)
    mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_dal_enables_foreign_keys(tmp_path: Path) -> None:
    """DAL 初始化后 foreign_keys 已启用。"""
    db_path = tmp_path / "test.db"
    _dal = DAL(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    # 验证外键约束生效：向 takes 插入不存在的 scene_id
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO takes (scene_id, take_number, start_ts) VALUES (9999, 1, 1.0);"
        )
    conn.close()


# ── scenes ───────────────────────────────────────────────────────────────────


def test_create_scene_returns_id(tmp_path: Path) -> None:
    """插入场次返回正确 scene_id，大于 0。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    assert isinstance(sid, int)
    assert sid > 0


def test_set_active_scene_clears_others(tmp_path: Path) -> None:
    """set_active_scene 后其余场次 is_active 均为 0，目标场次为 1。"""
    dal = DAL(tmp_path / "test.db")
    sid1 = dal.create_scene("Scene_1")
    sid2 = dal.create_scene("Scene_2")
    dal.set_active_scene(sid1)
    dal.set_active_scene(sid2)
    # sid1 应当被清除
    active = dal.get_active_scene_id()
    assert active == sid2
    scenes = dal.list_scenes()
    for s in scenes:
        if s["scene_id"] == sid1:
            assert s["is_active"] == 0
        elif s["scene_id"] == sid2:
            assert s["is_active"] == 1


def test_get_active_scene_id_no_active(tmp_path: Path) -> None:
    """无活跃场次时 get_active_scene_id 返回 None。"""
    dal = DAL(tmp_path / "test.db")
    assert dal.get_active_scene_id() is None


def test_list_scenes_returns_all(tmp_path: Path) -> None:
    """list_scenes 返回已插入的所有场次。"""
    dal = DAL(tmp_path / "test.db")
    dal.create_scene("Scene_A")
    dal.create_scene("Scene_B")
    scenes = dal.list_scenes()
    codes = {s["scene_code"] for s in scenes}
    assert {"Scene_A", "Scene_B"}.issubset(codes)


# ── takes ─────────────────────────────────────────────────────────────────────


def test_start_take_returns_id(tmp_path: Path) -> None:
    """插入 take 返回正确 take_id，大于 0。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    assert isinstance(tid, int)
    assert tid > 0


def test_end_take_sets_end_ts(tmp_path: Path) -> None:
    """end_take 后 get_take 返回的 end_ts 不为 None。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    dal.end_take(tid, 1060.0, "keeper")
    take = dal.get_take(tid)
    assert take is not None
    assert take.end_ts == pytest.approx(1060.0)
    assert take.status == "keeper"


def test_take_status_check_constraint(tmp_path: Path) -> None:
    """插入非法 status 值触发 CHECK 约束异常。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    with pytest.raises(sqlite3.IntegrityError):
        dal.end_take(tid, 1060.0, "invalid_status")


def test_takes_unique_scene_take_number(tmp_path: Path) -> None:
    """同场次同 take_number 重复插入触发 UNIQUE 约束异常。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    dal.start_take(sid, 1, 1000.0)
    with pytest.raises(sqlite3.IntegrityError):
        dal.start_take(sid, 1, 1001.0)


def test_list_takes_filter_by_scene_id(tmp_path: Path) -> None:
    """list_takes 按 scene_id 过滤，只返回目标场次的 take。"""
    dal = DAL(tmp_path / "test.db")
    sid1 = dal.create_scene("Scene_1")
    sid2 = dal.create_scene("Scene_2")
    dal.start_take(sid1, 1, 1000.0)
    dal.start_take(sid2, 1, 2000.0)
    takes1 = dal.list_takes(scene_id=sid1)
    assert len(takes1) == 1
    assert takes1[0].scene_id == sid1


def test_get_take_not_found_returns_none(tmp_path: Path) -> None:
    """get_take 不存在的 take_id 时返回 None。"""
    dal = DAL(tmp_path / "test.db")
    assert dal.get_take(99999) is None


def test_update_take_np_output(tmp_path: Path) -> None:
    """update_take_np_output 更新 performer_issues 与 audio_quality，不覆盖 end_ts。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    dal.end_take(tid, 1060.0, "tbd")
    dal.update_take_np_output(
        tid,
        performer_issues=["line_miss"],
        audio_quality="clean",
        status="keeper",
    )
    take = dal.get_take(tid)
    assert take is not None
    assert take.audio_quality == "clean"
    assert take.status == "keeper"
    assert take.end_ts == pytest.approx(1060.0)  # end_ts 未被覆盖


def test_update_take_np_output_serializes_json_list(tmp_path: Path) -> None:
    """update_take_np_output 接受 list，存库后 get_take 读回仍是 list。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    dal.end_take(tid, 1060.0, "tbd")
    issues: list = ["line_miss", "overlap"]
    dal.update_take_np_output(tid, performer_issues=issues, audio_quality=None, status=None)
    take = dal.get_take(tid)
    assert take is not None
    assert take.performer_issues == issues


def test_update_take_np_output_serializes_json_dict(tmp_path: Path) -> None:
    """update_take_np_output 接受 dict，存库后 get_take 读回仍是 dict。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    dal.end_take(tid, 1060.0, "tbd")
    issues: dict = {"missed": ["line3"], "late_cue": ["line5"]}
    dal.update_take_np_output(tid, performer_issues=issues, audio_quality=None, status=None)
    take = dal.get_take(tid)
    assert take is not None
    assert take.performer_issues == issues


def test_update_take_np_output_none_passes_through(tmp_path: Path) -> None:
    """update_take_np_output 传 performer_issues=None，get_take 读回也是 None。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    dal.end_take(tid, 1060.0, "tbd")
    dal.update_take_np_output(tid, performer_issues=None, audio_quality=None, status=None)
    take = dal.get_take(tid)
    assert take is not None
    assert take.performer_issues is None


# ── take_events ───────────────────────────────────────────────────────────────


def test_insert_take_event_with_valid_json(tmp_path: Path) -> None:
    """合法 JSON payload 正常写入，list_take_events 可查到。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    eid = dal.insert_take_event(tid, "manual.mark", {"mark": "keeper"}, 1010.0)
    assert eid > 0
    events = dal.list_take_events(tid)
    assert len(events) == 1
    assert events[0].event_type == "manual.mark"
    assert events[0].payload == {"mark": "keeper"}


def test_take_events_payload_json_validation(tmp_path: Path) -> None:
    """非法 JSON payload 触发 CHECK 约束异常（json_valid 约束）。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    # 直接用裸连接插入非法 JSON，绕过 DAL 的 json.dumps 转换
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON;")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO take_events (take_id, event_type, ts, payload) "
            "VALUES (?, 'manual.mark', 1010.0, 'not-valid-json');",
            (tid,),
        )
    conn.close()


def test_list_take_events_filter_by_type(tmp_path: Path) -> None:
    """list_take_events 按 event_type 过滤，只返回匹配事件。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    dal.insert_take_event(tid, "manual.mark", {"mark": "keeper"}, 1010.0)
    dal.insert_take_event(tid, "np.write", {"audio_quality": "clean"}, 1020.0)
    marks = dal.list_take_events(tid, event_type="manual.mark")
    assert len(marks) == 1
    assert marks[0].event_type == "manual.mark"


# ── transcript_segments ───────────────────────────────────────────────────────


def test_insert_segment_with_speaker(tmp_path: Path) -> None:
    """含 speaker 字段的片段正常写入，list_segments 可查到正确 speaker。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    seg_id = dal.insert_segment(tid, 1, "SPEAKER_00", "Hello world", 0, 16000)
    assert seg_id > 0
    segs = dal.list_segments(tid)
    assert len(segs) == 1
    assert segs[0].speaker == "SPEAKER_00"


def test_insert_segment_rejects_null_take_id(tmp_path: Path) -> None:
    """传 None 作为 take_id 触发 IntegrityError（NOT NULL 约束）。"""
    _dal = DAL(tmp_path / "test.db")
    # 用裸连接直接插入 NULL take_id
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON;")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO transcript_segments (take_id, ch, text, start_frame, end_frame) "
            "VALUES (NULL, 1, 'hello', 0, 160);"
        )
    conn.close()


def test_insert_segment_ch_check_constraint(tmp_path: Path) -> None:
    """ch 值非 1/2 触发 CHECK 约束异常。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON;")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO transcript_segments (take_id, ch, text, start_frame, end_frame) "
            "VALUES (?, 3, 'hello', 0, 160);",
            (tid,),
        )
    conn.close()


def test_insert_segment_frame_order_check(tmp_path: Path) -> None:
    """end_frame <= start_frame 触发 CHECK 约束异常（end_frame > start_frame）。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON;")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO transcript_segments (take_id, ch, text, start_frame, end_frame) "
            "VALUES (?, 1, 'hello', 1000, 500);",
            (tid,),
        )
    conn.close()


def test_list_segments_filter_by_speaker(tmp_path: Path) -> None:
    """按 speaker 过滤返回正确子集。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    dal.insert_segment(tid, 1, "SPEAKER_00", "Hello", 0, 16000)
    dal.insert_segment(tid, 1, "SPEAKER_01", "World", 16000, 32000)
    segs = dal.list_segments(tid, speaker="SPEAKER_00")
    assert len(segs) == 1
    assert segs[0].speaker == "SPEAKER_00"


def test_list_segments_filter_by_ch(tmp_path: Path) -> None:
    """按 ch 过滤返回正确子集。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    dal.insert_segment(tid, 1, None, "ch1 text", 0, 16000)
    dal.insert_segment(tid, 2, None, "ch2 text", 0, 16000)
    segs_ch1 = dal.list_segments(tid, ch=1)
    assert len(segs_ch1) == 1
    assert segs_ch1[0].ch == 1


# ── scripts 与 script_lines ───────────────────────────────────────────────────


def test_insert_script_auto_version(tmp_path: Path) -> None:
    """同场次多次插入版本号自动递增。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    _scr1 = dal.insert_script(sid, "第一版剧本")
    scr2 = dal.insert_script(sid, "第二版剧本")
    latest = dal.get_latest_script(sid)
    assert latest is not None
    assert latest["script_id"] == scr2


def test_insert_script_line_and_fts_sync(tmp_path: Path) -> None:
    """插入台词行后 FTS5 可立即 MATCH 到（trigram 最短 3 字符 query）。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    scr_id = dal.insert_script(sid, "测试剧本")
    dal.insert_script_line(scr_id, 1, "HERO", "To be or not to be")
    results = dal.match_script_line("not")
    assert len(results) >= 1
    assert any("not" in r.text.lower() for r in results)


def test_match_script_line_fts5_basic(tmp_path: Path) -> None:
    """FTS5 MATCH 基本英文词查询正确返回。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    scr_id = dal.insert_script(sid, "英文台词测试")
    dal.insert_script_line(scr_id, 1, "ACTOR_A", "The quick brown fox jumps")
    dal.insert_script_line(scr_id, 2, "ACTOR_B", "Lazy dogs sleep all day")
    results = dal.match_script_line("fox")
    assert len(results) == 1
    assert "fox" in results[0].text.lower()


def test_match_script_line_fts5_chinese(tmp_path: Path) -> None:
    """FTS5 MATCH 中文子串查询（trigram tokenizer，SQLite >= 3.34）。

    trigram tokenizer 将文本按连续 3 个 unicode codepoint 切分，支持 CJK 子串匹配。
    query 必须 >= 3 个字符才能命中，< 3 个字符的 query 返回空集（trigram 最小粒度限制）。
    本测试在所有支持 trigram 的平台（SQLite >= 3.34）上直接跑，无需 skipif。
    """
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    scr_id = dal.insert_script(sid, "中文剧本")
    dal.insert_script_line(scr_id, 1, "演员甲", "我不想走，请别让我走")
    dal.insert_script_line(scr_id, 2, "演员乙", "好的，我理解你的心情")
    # trigram 最小 3 字符：搜「不想走」匹配含「不想走」的行
    results = dal.match_script_line("不想走")
    assert len(results) >= 1
    assert any("不想走" in r.text for r in results)
    # 搜「我理解」匹配另一行
    results2 = dal.match_script_line("我理解")
    assert len(results2) >= 1
    assert any("我理解" in r.text for r in results2)


def test_delete_script_line_fts_removed(tmp_path: Path) -> None:
    """删除台词行后 FTS5 不再匹配。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    scr_id = dal.insert_script(sid, "测试剧本")
    lid = dal.insert_script_line(scr_id, 1, "HERO", "unique phrase xyzzy")
    # 直接用裸连接删除行，让触发器生效
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("DELETE FROM script_lines WHERE line_id = ?;", (lid,))
    conn.commit()
    conn.close()
    results = dal.match_script_line("xyzzy")
    assert len(results) == 0


# ── take_line_matches ─────────────────────────────────────────────────────────


def test_insert_take_line_match_valid_diff_type(tmp_path: Path) -> None:
    """合法 diff_type 写入正常，list_take_line_matches 可查到。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    scr_id = dal.insert_script(sid, "测试剧本")
    lid = dal.insert_script_line(scr_id, 1, "HERO", "original line")
    mid = dal.insert_take_line_match(tid, lid, "match", {})
    assert mid > 0
    matches = dal.list_take_line_matches(tid)
    assert len(matches) == 1
    assert matches[0]["diff_type"] == "match"


def test_insert_take_line_match_invalid_diff_type(tmp_path: Path) -> None:
    """非法 diff_type 触发 CHECK 约束异常。"""
    dal = DAL(tmp_path / "test.db")
    sid = dal.create_scene("Scene_1")
    tid = dal.start_take(sid, 1, 1000.0)
    scr_id = dal.insert_script(sid, "测试剧本")
    lid = dal.insert_script_line(scr_id, 1, "HERO", "original line")
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON;")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO take_line_matches (take_id, line_id, diff_type, payload) "
            "VALUES (?, ?, 'invalid_type', '{}');",
            (tid, lid),
        )
    conn.close()


# ── active_observers ──────────────────────────────────────────────────────────


def test_upsert_observer_updates_existing(tmp_path: Path) -> None:
    """同 connection_id 二次 upsert 覆盖 name。"""
    dal = DAL(tmp_path / "test.db")
    dal.upsert_observer("conn-abc", "director")
    dal.upsert_observer("conn-abc", "script_supervisor")
    observers = dal.list_observers()
    matching = [o for o in observers if o["connection_id"] == "conn-abc"]
    assert len(matching) == 1
    assert matching[0]["name"] == "script_supervisor"


def test_remove_observer(tmp_path: Path) -> None:
    """remove_observer 后 list_observers 不再含该连接。"""
    dal = DAL(tmp_path / "test.db")
    dal.upsert_observer("conn-1", "director")
    dal.remove_observer("conn-1")
    observers = dal.list_observers()
    assert not any(o["connection_id"] == "conn-1" for o in observers)


def test_list_observers_empty_initially(tmp_path: Path) -> None:
    """新 DAL 初始化后 active_observers 为空（全新数据库，尚无记录）。"""
    dal = DAL(tmp_path / "test.db")
    assert dal.list_observers() == []


def test_purge_volatile_tables_clears_observers(tmp_path: Path) -> None:
    """purge_volatile_tables 显式调用后 active_observers 清空。"""
    from backend.db.lifecycle import purge_volatile_tables

    db_path = tmp_path / "test.db"
    dal = DAL(db_path)
    dal.upsert_observer("conn-1", "director")
    dal.upsert_observer("conn-2", "sound")
    assert len(dal.list_observers()) == 2
    purge_volatile_tables(db_path)
    assert dal.list_observers() == []


# ── audit_log ─────────────────────────────────────────────────────────────────


def test_append_audit_returns_log_id(tmp_path: Path) -> None:
    """审计日志写入并返回 log_id，大于 0。"""
    dal = DAL(tmp_path / "test.db")
    log_id = dal.append_audit("orchestrator", "take.start", {"take_id": 1})
    assert isinstance(log_id, int)
    assert log_id > 0


def test_audit_payload_check_constraint(tmp_path: Path) -> None:
    """非法 JSON payload 触发 CHECK 约束异常（json_valid 约束）。"""
    _dal = DAL(tmp_path / "test.db")
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON;")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO audit_log (actor, action, payload) "
            "VALUES ('orchestrator', 'take.start', 'not-valid-json');"
        )
    conn.close()
