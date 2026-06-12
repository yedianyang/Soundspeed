"""get_take_by_coords：按 (scene_id, shot, take_number) 查活跃 take（排除软删）。

4-tuple unique 含 take_suffix，故同坐标可有多行（'+'/'++'）→ 返回 list，调用方据「不唯一」走 clarify。
start_take 自动按 (scene_id, shot) 计 take_number，故同场不同 shot 的首条都是 take_number=1。
"""

from backend.db.dal import DAL


def test_returns_empty_when_absent(tmp_dal: DAL) -> None:
    sid = tmp_dal.create_scene("1")
    assert tmp_dal.get_take_by_coords(sid, "", 1) == []


def test_returns_single_match(tmp_dal: DAL) -> None:
    sid = tmp_dal.create_scene("1")
    tid, _ = tmp_dal.start_take(sid, "", 0.0)
    out = tmp_dal.get_take_by_coords(sid, "", 1)
    assert [t.take_id for t in out] == [tid]


def test_excludes_soft_deleted(tmp_dal: DAL) -> None:
    sid = tmp_dal.create_scene("1")
    tid, _ = tmp_dal.start_take(sid, "", 0.0)
    tmp_dal.delete_take(tid)
    assert tmp_dal.get_take_by_coords(sid, "", 1) == []


def test_filters_by_shot(tmp_dal: DAL) -> None:
    sid = tmp_dal.create_scene("1")
    t_noshot, _ = tmp_dal.start_take(sid, "", 0.0)
    t_shot4, _ = tmp_dal.start_take(sid, "4", 0.0)
    assert [t.take_id for t in tmp_dal.get_take_by_coords(sid, "", 1)] == [t_noshot]
    assert [t.take_id for t in tmp_dal.get_take_by_coords(sid, "4", 1)] == [t_shot4]
