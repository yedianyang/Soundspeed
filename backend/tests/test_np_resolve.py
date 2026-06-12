"""确定性解析：NPExtraction + 上下文 → take_id 列表 / clarify（纯 unit，fake DAL）。"""

from dataclasses import dataclass

import pytest

from backend.pipelines.np_extract import NPExtraction
from backend.pipelines.np_resolve import (
    NPContext,
    ResolveResult,
    resolve_targets,
)


@dataclass
class _FakeTake:
    take_id: int
    scene_id: int
    shot: str
    take_number: int
    take_suffix: str = ""
    end_ts: float | None = 1.0
    status: str = "tbd"
    scene_code: str = "1"


class _FakeDAL:
    """最小 DAL 替身：get_take_by_coords / get_take / list_takes / resolve_scene_id。"""

    def __init__(self, takes: list[_FakeTake]):
        self._takes = {t.take_id: t for t in takes}

    def get_take(self, take_id):
        return self._takes.get(take_id)

    def get_take_by_coords(self, scene_id, shot, take_number):
        return [
            t for t in self._takes.values()
            if t.scene_id == scene_id and t.shot == shot and t.take_number == take_number
        ]

    def list_takes(self, scene_id=None):
        out = [t for t in self._takes.values() if scene_id is None or t.scene_id == scene_id]
        return sorted(out, key=lambda t: t.take_number)

    def resolve_scene_id(self, scene_ref):
        for t in self._takes.values():
            if t.scene_code == scene_ref:
                return t.scene_id
        return None


def _ext(**over) -> NPExtraction:
    base = dict(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=[],
        deictic="current", mark="none", note_text="", note_category="note",
    )
    base.update(over)
    return NPExtraction(**base)


def _ctx(**over) -> NPContext:
    base = dict(
        current_scene_id=10, current_scene_code="1", current_shot="",
        current_take_id=3, current_take_number=3,
    )
    base.update(over)
    return NPContext(**base)


SCENE_TAKES = [
    _FakeTake(take_id=1, scene_id=10, shot="", take_number=1),
    _FakeTake(take_id=2, scene_id=10, shot="", take_number=2),
    _FakeTake(take_id=3, scene_id=10, shot="", take_number=3),
]


def test_deictic_current_resolves_active_take() -> None:
    r = resolve_targets(_ext(deictic="current"), _ctx(), _FakeDAL(SCENE_TAKES))
    assert r == ResolveResult(take_ids=[3], clarify=False, message=None, candidates=[])


def test_deictic_prev_resolves_most_recent_completed() -> None:
    # 当前活跃=3 → prev = 该场镜下最高 take_number<active 且 end_ts 已落 = take 2
    r = resolve_targets(_ext(deictic="prev"), _ctx(), _FakeDAL(SCENE_TAKES))
    assert r.take_ids == [2] and not r.clarify


def test_take_ordinals_resolve_by_coords() -> None:
    r = resolve_targets(_ext(deictic="none", take_ordinals=[1, 2]), _ctx(), _FakeDAL(SCENE_TAKES))
    assert sorted(r.take_ids) == [1, 2] and not r.clarify


def test_take_ordinal_with_shot_ordinal() -> None:
    takes = SCENE_TAKES + [_FakeTake(take_id=9, scene_id=10, shot="4", take_number=1)]
    # 第四进第一次 → shot_ordinal=4 映射 shot="4"、take 1
    r = resolve_targets(_ext(deictic="none", shot_ordinal=4, take_ordinals=[1]), _ctx(), _FakeDAL(takes))
    assert r.take_ids == [9] and not r.clarify


def test_nonexistent_take_clarifies() -> None:
    r = resolve_targets(_ext(deictic="none", take_ordinals=[99]), _ctx(), _FakeDAL(SCENE_TAKES))
    assert r.clarify and r.take_ids == []


def test_ambiguous_suffix_clarifies() -> None:
    takes = [
        _FakeTake(take_id=1, scene_id=10, shot="", take_number=1, take_suffix=""),
        _FakeTake(take_id=2, scene_id=10, shot="", take_number=1, take_suffix="+"),
    ]
    r = resolve_targets(_ext(deictic="none", take_ordinals=[1]), _ctx(), _FakeDAL(takes))
    assert r.clarify and {c.take_id for c in r.candidates} == {1, 2}
    assert {c.take_suffix for c in r.candidates} == {"", "+"}


def test_current_but_no_active_take_clarifies() -> None:
    r = resolve_targets(
        _ext(deictic="current"),
        _ctx(current_take_id=None, current_take_number=None),
        _FakeDAL(SCENE_TAKES),
    )
    assert r.clarify and r.take_ids == []


def test_explicit_scene_ordinal_resolves_scene() -> None:
    # scene_ordinal=1 → resolve_scene_id("1")=10；take 3 在该场
    r = resolve_targets(
        _ext(deictic="none", scene_ordinal=1, take_ordinals=[3]),
        _ctx(),
        _FakeDAL(SCENE_TAKES),
    )
    assert r.take_ids == [3] and not r.clarify


def test_unknown_scene_clarifies() -> None:
    r = resolve_targets(
        _ext(deictic="none", scene_ordinal=72, take_ordinals=[1]),
        _ctx(),
        _FakeDAL(SCENE_TAKES),
    )
    assert r.clarify and r.take_ids == []


def test_explicit_shot_without_take_clarifies() -> None:
    # 第四进过了：shot_ordinal=4、无 take 号、deictic=none → 不能硬标当前条，必须 clarify
    r = resolve_targets(_ext(deictic="none", shot_ordinal=4), _ctx(), _FakeDAL(SCENE_TAKES))
    assert r.clarify and r.take_ids == []


def test_explicit_scene_without_take_clarifies() -> None:
    r = resolve_targets(_ext(deictic="none", scene_ordinal=1), _ctx(), _FakeDAL(SCENE_TAKES))
    assert r.clarify and r.take_ids == []


def test_no_explicit_none_deictic_falls_back_to_current() -> None:
    # 没点场/镜、deictic=none、无 ordinals（如「收音有点小」模型给 none）→ 兜底当前活跃条
    r = resolve_targets(_ext(deictic="none"), _ctx(), _FakeDAL(SCENE_TAKES))
    assert r.take_ids == [3] and not r.clarify
