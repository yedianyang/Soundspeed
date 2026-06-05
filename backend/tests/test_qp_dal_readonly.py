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
