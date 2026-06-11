"""note.applied / note.clarify / note.confirm topic 常量 + payload 形状（asdict 友好）。"""

from dataclasses import FrozenInstanceError, asdict

import pytest

from backend.core.events import (
    NOTE_APPLIED,
    NOTE_CLARIFY,
    NOTE_CONFIRM,
    NoteAppliedPayload,
    NoteClarifyPayload,
    NoteConfirmPayload,
)


def test_topic_constants() -> None:
    assert NOTE_APPLIED == "note.applied"
    assert NOTE_CLARIFY == "note.clarify"
    assert NOTE_CONFIRM == "note.confirm"


def test_applied_payload_asdict() -> None:
    p = NoteAppliedPayload(
        client_id="cid",
        changes=[{"op": "mark", "take_id": 3, "scene_code": "1", "shot": "",
                  "take_number": 3, "take_suffix": "", "status": "keep",
                  "content": None, "category": None}],
        ts=5.0,
    )
    d = asdict(p)
    assert d["client_id"] == "cid"
    assert d["changes"][0]["op"] == "mark"
    assert d["ts"] == 5.0


def test_clarify_payload_asdict() -> None:
    p = NoteClarifyPayload(
        client_id="cid",
        message="第1条有多条",
        candidates=[{"take_id": 1, "scene_code": "1", "shot": "", "take_number": 1, "status": "tbd"}],
        ts=5.0,
    )
    d = asdict(p)
    assert d["message"] == "第1条有多条"
    assert d["candidates"][0]["take_id"] == 1


def test_confirm_topic_constant() -> None:
    assert NOTE_CONFIRM == "note.confirm"


def test_confirm_payload_frozen() -> None:
    p = NoteConfirmPayload(
        client_id="cid",
        extraction={"scene_code": "1", "shot": "A", "take_number": 1, "take_suffix": "",
                    "status": "ng", "content": None, "category": None},
        disagreement=["scene_code", "shot"],
        options={"scenes": ["1", "2"], "shots": ["A", "B"], "take_numbers": [1, 2]},
        ts=1.0,
    )
    with pytest.raises(FrozenInstanceError):
        p.client_id = "other"  # type: ignore[misc]


def test_confirm_payload_asdict() -> None:
    extraction = {
        "scene_code": "1", "shot": "A", "take_number": 1, "take_suffix": "",
        "status": "ng", "content": "重来", "category": "performance",
    }
    options = {"scenes": ["1"], "shots": ["A"], "take_numbers": [1]}
    p = NoteConfirmPayload(
        client_id="cid-confirm",
        extraction=extraction,
        disagreement=["scene_code"],
        options=options,
        ts=9.9,
    )
    d = asdict(p)
    assert d["client_id"] == "cid-confirm"
    assert d["extraction"]["scene_code"] == "1"
    assert d["disagreement"] == ["scene_code"]
    assert d["options"]["shots"] == ["A"]
    assert d["ts"] == 9.9
