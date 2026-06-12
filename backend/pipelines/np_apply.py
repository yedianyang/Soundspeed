"""NP 多目标 apply：解析出的 take_id 列表 → 逐个 set_take_status + insert_note，产出 changes[]。

mark!=none → set_take_status（"none" 绝不下传，会 ValueError）；note_text!="" → insert_note
（category 用 note_category 枚举值）。两者都做（mark 与 note 拆开，设计 §4）。每个变更产一条 Change，
供 orchestrator 组 note.applied 回灌 + 逐 take 发 take.changed。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Change:
    op: str  # "mark" | "note"
    take_id: int
    scene_code: str
    shot: str
    take_number: int
    take_suffix: str
    status: str | None = None  # op=mark
    content: str | None = None  # op=note
    category: str | None = None  # op=note


def _change(op: str, take: Any, **over) -> Change:
    base = dict(
        op=op,
        take_id=take.take_id,
        scene_code="",
        shot=take.shot,
        take_number=take.take_number,
        take_suffix=take.take_suffix,
    )
    base.update(over)
    return Change(**base)


def apply_targets(
    extraction: Any,
    take_ids: list[int],
    dal: Any,
    *,
    raw_text: str,
    ts: float,
) -> list[Change]:
    """逐 take 应用 mark + note，返回成功的 changes[]。"""
    changes: list[Change] = []
    do_mark = extraction.mark != "none"
    do_note = extraction.note_text != ""

    for take_id in take_ids:
        take = dal.get_take(take_id)
        if take is None:
            continue  # 解析后被删的极端竞态：跳过，不硬标
        if do_mark:
            dal.set_take_status(take_id, extraction.mark)
            refreshed = dal.get_take(take_id) or take
            changes.append(_change("mark", refreshed, status=extraction.mark))
        if do_note:
            dal.insert_note(
                take_id=take_id,
                category=extraction.note_category,
                content=extraction.note_text,
                raw_text=raw_text,
                ts=ts,
            )
            changes.append(
                _change(
                    "note",
                    take,
                    content=extraction.note_text,
                    category=extraction.note_category,
                )
            )
    return changes
