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
        ("第一场", "1"),       # 中文口语序数（QP e2e 实测 4B 高频传法）
        ("第1场", "1"),        # 中文「第N场」+ 阿拉伯数字
        ("第72场", "72"),      # 多位阿拉伯
        ("第十场", "10"),      # 中文数字「十」
        ("第十一场", "11"),    # 复合中文数字（十X）
        ("第二十场", "20"),    # 复合中文数字（X十）
        ("第二十一场", "21"),  # 复合中文数字（X十Y）
        ("第九十九场", "99"),  # 复合上界
    ],
)
def test_normalize_scene_code(raw: str, expected: str) -> None:
    assert normalize_scene_code(raw) == expected


def test_resolve_scene_id_chinese_ordinal(dal: DAL) -> None:
    # 库里存 Scene_1，中文口语「第一场」也能对到（QP e2e 真模型实测的关键路径）
    sid = dal.create_scene("Scene_1")
    assert dal.resolve_scene_id("第一场") == sid
    assert dal.resolve_scene_id("第1场") == sid


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


def test_count_takes_status_no_match_returns_zero(dal: DAL) -> None:
    """status 过滤无匹配时应返回 0（不是 None 也不报错）。"""
    sid = dal.create_scene("Scene_6")
    dal.start_take(sid, "", 1000.0)  # 默认 tbd
    assert dal.count_takes(sid, status="ng") == 0


def test_search_script_lines_no_scene_id(dal: DAL) -> None:
    """不带 scene_id 走全剧本检索分支，能命中即可。"""
    _seed_scene_with_script(dal)
    hits = dal.search_script_lines("最近怎么样")
    assert any("最近" in h["text"] for h in hits)


def test_list_characters_no_script_returns_empty(dal: DAL) -> None:
    """场次存在但无剧本时 list_characters 返回空列表。"""
    sid = dal.create_scene("Scene_6")
    assert dal.list_characters(sid) == []


def test_get_scene_info_latest_script_version(dal: DAL) -> None:
    """多版本剧本时 get_scene_info / list_characters 只取最新版角色，不 union 历史版本。"""
    sid = dal.get_or_create_scene("Scene_8", int_ext="室内", time_of_day="日", location="书房")[0]
    # v1：角色 甲、乙
    s1 = dal.insert_script(sid, "v1 raw")
    dal.insert_script_line(s1, 1, "甲", "v1 台词甲。")
    dal.insert_script_line(s1, 2, "乙", "v1 台词乙。")
    # v2：角色 甲、丙（乙 消失）—— insert_script version=None 自动 +1 → version=2
    s2 = dal.insert_script(sid, "v2 raw")
    dal.insert_script_line(s2, 1, "甲", "v2 台词甲。")
    dal.insert_script_line(s2, 2, "丙", "v2 台词丙。")

    # 只反映最新版（v2）：2 个角色，不是 union 的 3 个
    info = dal.get_scene_info(sid)
    assert info["character_count"] == 2, f"期望 2，得到 {info['character_count']}（可能 union 了历史版本）"

    chars = dal.list_characters(sid)
    assert sorted(chars) == ["丙", "甲"], f"期望 [丙,甲]，得到 {sorted(chars)}（可能 union 了历史版本）"


# ---------------------------------------------------------------------------
# Task 8: search_script_lines 带场次 + 短查询 LIKE 回退（trigram 2 字盲区）
# ---------------------------------------------------------------------------


def test_search_script_lines_carries_scene_code(tmp_dal) -> None:
    sid = tmp_dal.create_scene("21")
    script_id = tmp_dal.insert_script(sid, "raw")
    tmp_dal.insert_script_line(script_id, 1, "甲", "这句话提到了螺丝刀")
    hits = tmp_dal.search_script_lines("螺丝刀")
    assert hits and hits[0]["scene_code"] == "21"


def test_search_script_lines_short_query_like_fallback(tmp_dal) -> None:
    # trigram FTS 对 2 字查询 0 命中（实证），须 LIKE 回退
    sid = tmp_dal.create_scene("22")
    script_id = tmp_dal.insert_script(sid, "raw")
    tmp_dal.insert_script_line(script_id, 1, "乙", "这份合同必须签。")
    hits = tmp_dal.search_script_lines("合同")
    assert len(hits) == 1 and hits[0]["scene_code"] == "22"
    tmp_dal.insert_script_line(script_id, 2, "乙", "这价格是100元整。")
    assert tmp_dal.search_script_lines("0%") == []  # 2字含通配符:走 LIKE 且 % 被转义 → 0 命中


# ---------------------------------------------------------------------------
# Task 3: query_readonly 万能笔安全墙（D-QP-04）
# ---------------------------------------------------------------------------

def test_query_readonly_allows_select(dal: DAL) -> None:
    dal.create_scene("Scene_1")
    res = dal.query_readonly("SELECT scene_code FROM scenes;")
    assert res["row_count"] == 1
    assert res["rows"][0]["scene_code"] == "Scene_1"
    assert res["truncated"] is False


def test_query_readonly_allows_cte(dal: DAL) -> None:
    dal.create_scene("Scene_1")
    res = dal.query_readonly(
        "WITH x AS (SELECT scene_code FROM scenes) SELECT * FROM x;"
    )
    assert "error" not in res
    assert res["row_count"] == 1


def test_query_readonly_blocks_write(dal: DAL) -> None:
    res = dal.query_readonly("INSERT INTO scenes (scene_code) VALUES ('x');")
    assert "error" in res


def test_query_readonly_blocks_attach(dal: DAL) -> None:
    # mode=ro 拦不住 ATTACH，必须靠 authorizer（spec §6.2 ✅实测）
    res = dal.query_readonly("ATTACH DATABASE ':memory:' AS evil;")
    assert "error" in res


def test_query_readonly_blocks_pragma(dal: DAL) -> None:
    res = dal.query_readonly("PRAGMA table_info(scenes);")
    assert "error" in res


def test_query_readonly_blocks_multi_statement(dal: DAL) -> None:
    res = dal.query_readonly("SELECT 1; SELECT 2;")
    assert "error" in res  # 单游标只执行一条，多句 raise Warning


def test_query_readonly_allows_fts_match(dal: DAL) -> None:
    # 影子表按设计可读（MATCH 内部需要 _config/_idx，且漏不出 script_lines 之外信息）
    _seed_scene_with_script(dal)
    res = dal.query_readonly(
        "SELECT text FROM script_lines_fts WHERE text MATCH '好久不见';"
    )
    assert "error" not in res
    assert res["row_count"] >= 1


def test_query_readonly_truncates_rows(dal: DAL) -> None:
    sid = dal.create_scene("Scene_1")
    for i in range(5):
        dal.start_take(sid, "", 1000.0 + i)
    res = dal.query_readonly("SELECT take_id FROM takes;", max_rows=3)
    assert res["row_count"] == 3
    assert res["truncated"] is True


def test_query_readonly_allows_pragma_data_version(dal: DAL) -> None:
    # scoped PRAGMA 放行：data_version 是 MATCH 内部所需，锁定只放行它
    res = dal.query_readonly("PRAGMA data_version;")
    assert "error" not in res
    assert res["row_count"] == 1


def test_query_readonly_blocks_pragma_table_info(dal: DAL) -> None:
    # scoped PRAGMA：table_info 不是 data_version，仍被 DENY
    res = dal.query_readonly("PRAGMA table_info(scenes);")
    assert "error" in res


def test_query_readonly_blocks_load_extension(dal: DAL) -> None:
    # load_extension 是 RCE 向量，authorizer 层独立 DENY（纵深防御，不依赖 enable_load_extension）
    res = dal.query_readonly("SELECT load_extension('/tmp/evil.so');")
    assert "error" in res


def test_query_readonly_timeout(dal: DAL) -> None:
    # DoS 防线：progress_handler 在 deadline 后返回非零，SQLite 中断查询
    res = dal.query_readonly(
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) "
        "SELECT count(*) FROM c;",
        timeout_s=0.1,
    )
    assert "error" in res


# ---------------------------------------------------------------------------
# Task 12: get_script_lines DAL 新方法（B6 get_scene_script 工具下层）
# ---------------------------------------------------------------------------


def test_get_script_lines_latest_version_ordered(tmp_dal) -> None:
    from backend.tests.qp_eval_seed import seed_qp_eval_db
    seed_qp_eval_db(tmp_dal)
    sid16 = tmp_dal.resolve_scene_id("16")
    lines = tmp_dal.get_script_lines(sid16, limit=3)
    assert len(lines) == 3
    assert lines[0]["character"] is None  # 舞台指示行在前
    assert lines[0]["line_no"] == 1
    assert tmp_dal.get_script_lines(tmp_dal.resolve_scene_id("15"), limit=3) == []  # 无剧本


# ---------------------------------------------------------------------------
# Task 2: list_shots / list_take_numbers 确认卡值域 DAL 查询
# ---------------------------------------------------------------------------


def test_list_shots_two_distinct_shots(tmp_dal: DAL) -> None:
    """某场有两个不同 shot，各有一条 take，list_shots 返回两者（排序）。"""
    sid = tmp_dal.create_scene("Scene_1")
    tmp_dal.start_take(sid, "A", 1000.0)
    tmp_dal.start_take(sid, "B", 1001.0)
    assert tmp_dal.list_shots(sid) == ["A", "B"]


def test_list_shots_dedup_same_shot_multiple_takes(tmp_dal: DAL) -> None:
    """同一 shot 有多条 take，list_shots 只出现一次（DISTINCT 去重）。"""
    sid = tmp_dal.create_scene("Scene_2")
    tmp_dal.start_take(sid, "A", 1000.0)
    tmp_dal.start_take(sid, "A", 1001.0)
    assert tmp_dal.list_shots(sid) == ["A"]


def test_list_shots_excludes_soft_deleted_only_shot(tmp_dal: DAL) -> None:
    """某 shot 的全部 take 均被软删 → 该 shot 不出现。"""
    sid = tmp_dal.create_scene("Scene_3")
    t1, _ = tmp_dal.start_take(sid, "X", 1000.0)
    tmp_dal.delete_take(t1)
    assert tmp_dal.list_shots(sid) == []


def test_list_shots_partial_soft_delete_keeps_shot(tmp_dal: DAL) -> None:
    """同 shot 有一条 live + 一条软删，shot 仍出现（live 存在）。"""
    sid = tmp_dal.create_scene("Scene_4")
    t1, _ = tmp_dal.start_take(sid, "Y", 1000.0)
    tmp_dal.start_take(sid, "Y", 1001.0)
    tmp_dal.delete_take(t1)
    assert tmp_dal.list_shots(sid) == ["Y"]


def test_list_shots_empty_scene(tmp_dal: DAL) -> None:
    """无 take 的场次，list_shots 返回 []。"""
    sid = tmp_dal.create_scene("Scene_5")
    assert tmp_dal.list_shots(sid) == []


def test_list_take_numbers_ascending(tmp_dal: DAL) -> None:
    """某场某镜有多条 take，返回升序 take_number 列表。"""
    sid = tmp_dal.create_scene("Scene_6")
    tmp_dal.start_take(sid, "A", 1000.0)  # take_number=1
    tmp_dal.start_take(sid, "A", 1001.0)  # take_number=2
    tmp_dal.start_take(sid, "A", 1002.0)  # take_number=3
    assert tmp_dal.list_take_numbers(sid, "A") == [1, 2, 3]


def test_list_take_numbers_excludes_soft_deleted(tmp_dal: DAL) -> None:
    """软删 take 不出现在 take_number 列表中。"""
    sid = tmp_dal.create_scene("Scene_7")
    t1, _ = tmp_dal.start_take(sid, "B", 1000.0)  # take_number=1
    tmp_dal.start_take(sid, "B", 1001.0)           # take_number=2
    tmp_dal.delete_take(t1)
    assert tmp_dal.list_take_numbers(sid, "B") == [2]


def test_list_take_numbers_empty(tmp_dal: DAL) -> None:
    """无匹配时返回空列表。"""
    sid = tmp_dal.create_scene("Scene_8")
    assert tmp_dal.list_take_numbers(sid, "C") == []
