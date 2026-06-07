"""多目标 apply：每个 take_id 做 mark（mark!=none）+ note（note_text!=\"\"），产出 changes[]。"""

from dataclasses import dataclass

from backend.pipelines.np_apply import Change, apply_targets
from backend.pipelines.np_extract import NPExtraction


@dataclass
class _FakeTake:
    take_id: int
    scene_id: int = 10
    shot: str = ""
    take_number: int = 1
    take_suffix: str = ""
    status: str = "tbd"


class _SpyDAL:
    def __init__(self, takes):
        self._takes = {t.take_id: t for t in takes}
        self.status_calls: list[tuple[int, str]] = []
        self.note_calls: list[tuple] = []
        self._next_event = 100

    def get_take(self, take_id):
        return self._takes.get(take_id)

    def set_take_status(self, take_id, status):
        if status not in {"pass", "ng", "keep", "tbd"}:
            raise ValueError(status)
        self.status_calls.append((take_id, status))
        if take_id in self._takes:
            self._takes[take_id].status = status

    def insert_note(self, take_id, category, content, raw_text, ts):
        self.note_calls.append((take_id, category, content, raw_text, ts))
        self._next_event += 1
        return self._next_event


def _ext(**over) -> NPExtraction:
    base = dict(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=[],
        deictic="current", mark="none", note_text="", note_category="note",
    )
    base.update(over)
    return NPExtraction(**base)


def test_mark_only_multi_target() -> None:
    dal = _SpyDAL([_FakeTake(2), _FakeTake(3)])
    changes = apply_targets(_ext(mark="pass"), [2, 3], dal, raw_text="第二三条过", ts=5.0)
    assert dal.status_calls == [(2, "pass"), (3, "pass")]
    assert dal.note_calls == []
    assert [(c.op, c.take_id, c.status) for c in changes] == [
        ("mark", 2, "pass"), ("mark", 3, "pass"),
    ]


def test_note_only() -> None:
    dal = _SpyDAL([_FakeTake(3)])
    changes = apply_targets(
        _ext(mark="none", note_text="收音小", note_category="issue"),
        [3], dal, raw_text="收音小", ts=5.0,
    )
    assert dal.status_calls == []
    assert dal.note_calls == [(3, "issue", "收音小", "收音小", 5.0)]
    assert [(c.op, c.take_id, c.category, c.content) for c in changes] == [
        ("note", 3, "issue", "收音小"),
    ]


def test_mark_and_note_both() -> None:
    dal = _SpyDAL([_FakeTake(3)])
    changes = apply_targets(
        _ext(mark="keep", note_text="后半段可用", note_category="note"),
        [3], dal, raw_text="这条保，后半段可用", ts=5.0,
    )
    assert dal.status_calls == [(3, "keep")]
    assert dal.note_calls == [(3, "note", "后半段可用", "这条保，后半段可用", 5.0)]
    ops = [(c.op, c.take_id) for c in changes]
    assert ("mark", 3) in ops and ("note", 3) in ops


def test_mark_none_never_calls_set_status() -> None:
    dal = _SpyDAL([_FakeTake(3)])
    apply_targets(_ext(mark="none", note_text="x", note_category="note"), [3], dal, raw_text="x", ts=1.0)
    assert dal.status_calls == []  # 绝不把 "none" 传给 set_take_status


def test_tbd_is_applied() -> None:
    dal = _SpyDAL([_FakeTake(3)])
    apply_targets(_ext(mark="tbd"), [3], dal, raw_text="待定", ts=1.0)
    assert dal.status_calls == [(3, "tbd")]
