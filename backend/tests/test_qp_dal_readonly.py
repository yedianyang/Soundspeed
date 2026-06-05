"""QP 只读路径单测：normalize_scene_code / resolve_scene_id / _readonly_conn。

所有 QP 读走临时 mode=ro 连接（D-QP-12），不碰共享 self._conn。
"""
from __future__ import annotations

import pytest

from backend.db.dal import DAL, normalize_scene_code


@pytest.fixture
def dal(tmp_path) -> DAL:
    d = DAL(tmp_path / "qp.db")
    yield d
    d.close()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Scene_3A", "3A"),
        ("scene 3a", "3A"),
        ("Sc_72", "72"),
        ("S72", "72"),
        ("场3", "3"),
        ("  72 ", "72"),
        ("3", "3"),
        ("", ""),
        ("sce3", "SCE3"),   # 前缀后未紧跟数字，不误剥
        ("Scene3", "3"),    # 无分隔符、前缀后紧跟数字，正常剥
    ],
)
def test_normalize_scene_code(raw: str, expected: str) -> None:
    assert normalize_scene_code(raw) == expected


def test_resolve_scene_id_matches_via_normalize(dal: DAL) -> None:
    sid = dal.create_scene("Scene_72")
    # 口语变体都能对到同一 scene_id
    assert dal.resolve_scene_id("72") == sid
    assert dal.resolve_scene_id("S72") == sid
    assert dal.resolve_scene_id("scene 72") == sid


def test_resolve_scene_id_missing_returns_none(dal: DAL) -> None:
    dal.create_scene("Scene_1")
    # 不同数字 = 不同场，不模糊替换（spec §7.5）
    assert dal.resolve_scene_id("2") is None
    assert dal.resolve_scene_id("") is None


def test_resolve_scene_id_symmetric_no_prefix_stored(dal: DAL) -> None:
    """库里存无前缀的纯数字编号，用带前缀的引用也能查回来（对称性）。"""
    sid = dal.create_scene("72")
    assert dal.resolve_scene_id("S72") == sid
    assert dal.resolve_scene_id("Scene_72") == sid


def test_readonly_conn_blocks_writes(dal: DAL) -> None:
    import sqlite3

    dal.create_scene("Scene_1")
    with dal._readonly_conn() as conn:
        # 读没问题
        rows = conn.execute("SELECT scene_code FROM scenes;").fetchall()
        assert rows[0]["scene_code"] == "Scene_1"
        # 写被 mode=ro 拦死
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO scenes (scene_code) VALUES ('x');")


def _seed_scene_with_script(dal: DAL) -> int:
    """建一个带剧本（2 角色 + 1 舞台指示）的场次，返回 scene_id。"""
    sid = dal.get_or_create_scene(
        "Scene_5",
        int_ext="室内",
        time_of_day="日",
        location="咖啡馆",
    )[0]
    script_id = dal.insert_script(sid, "raw")
    dal.insert_script_line(script_id, 1, "李雷", "你好，韩梅梅。")
    dal.insert_script_line(script_id, 2, "韩梅梅", "好久不见。")
    dal.insert_script_line(script_id, 3, "李雷", "最近怎么样？")
    dal.insert_script_line(script_id, 4, None, "（两人握手）")  # 舞台指示，character=NULL
    return sid


def test_count_takes_filters_soft_deleted(dal: DAL) -> None:
    sid = dal.create_scene("Scene_5")
    t1, _ = dal.start_take(sid, "", 1000.0)
    dal.start_take(sid, "", 1001.0)
    dal.delete_take(t1)  # 软删
    assert dal.count_takes(sid) == 1  # 软删的不计


def test_count_takes_status_filter(dal: DAL) -> None:
    sid = dal.create_scene("Scene_5")
    t1, _ = dal.start_take(sid, "", 1000.0)
    dal.start_take(sid, "", 1001.0)
    dal.set_take_status(t1, "keep")  # v9 正名：keeper→keep, hold→pass（schema CHECK pass/ng/keep/tbd）
    assert dal.count_takes(sid, status="keep") == 1
    assert dal.count_takes(sid, status="tbd") == 1


def test_get_scene_info(dal: DAL) -> None:
    sid = _seed_scene_with_script(dal)
    info = dal.get_scene_info(sid)
    assert info["scene_code"] == "Scene_5"
    assert info["location"] == "咖啡馆"
    assert info["int_ext"] == "室内"
    assert info["time_of_day"] == "日"
    assert info["character_count"] == 2  # 李雷/韩梅梅，舞台指示 NULL 不计


def test_get_scene_info_missing(dal: DAL) -> None:
    assert dal.get_scene_info(99999) is None


def test_list_characters_dedup_excludes_stage_dirs(dal: DAL) -> None:
    sid = _seed_scene_with_script(dal)
    chars = dal.list_characters(sid)
    assert sorted(chars) == ["李雷", "韩梅梅"]  # 去重 + 舞台指示(NULL) 不出现


def test_search_script_lines_fts(dal: DAL) -> None:
    sid = _seed_scene_with_script(dal)
    hits = dal.search_script_lines("好久不见", scene_id=sid)
    assert any("好久不见" in h["text"] for h in hits)
    assert all({"line_no", "character", "text"} <= set(h) for h in hits)
