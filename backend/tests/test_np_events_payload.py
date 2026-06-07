"""note.applied / note.clarify topic 常量 + payload 形状（asdict 友好）。"""

from dataclasses import asdict

from backend.core.events import (
    NOTE_APPLIED,
    NOTE_CLARIFY,
    NoteAppliedPayload,
    NoteClarifyPayload,
)


def test_topic_constants() -> None:
    assert NOTE_APPLIED == "note.applied"
    assert NOTE_CLARIFY == "note.clarify"


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
