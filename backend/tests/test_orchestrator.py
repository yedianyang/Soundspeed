"""test_orchestrator.py：覆盖 spec §7 全部 15 条测试。

§7.1 pub/sub 基础（4 条）
§7.2 内置 handler asr.final.ch1 / asr.final.ch2（8 条）
§7.3 SessionState 行为（3 条）
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from backend.core.events import (
    ASR_FINAL_CH1,
    ASR_FINAL_CH2,
    AsrFinalPayload,
)
from backend.core.orchestrator import Orchestrator
from backend.core.session import SessionState
from backend.db.dal import DAL


# ── 夹具 ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def dal(tmp_path: Path) -> DAL:  # type: ignore[misc]
    d = DAL(tmp_path / "test.db")
    yield d  # type: ignore[misc]
    d.close()


# ── §7.1 pub/sub 基础 ────────────────────────────────────────────────────────


def test_subscribe_and_publish_calls_handler(dal: DAL) -> None:
    """subscribe 后 publish 同事件，handler 被调一次，payload 透传。"""
    orch = Orchestrator(dal)
    received: list[object] = []

    orch.subscribe("test.event", lambda p: received.append(p))
    payload = object()
    orch.publish("test.event", payload)

    assert len(received) == 1
    assert received[0] is payload


def test_publish_unregistered_event_is_noop(dal: DAL) -> None:
    """未 subscribe 的 event_type publish 不抛错。"""
    orch = Orchestrator(dal)
    # 不应抛异常
    orch.publish("nonexistent.event", object())


def test_multiple_handlers_called_in_subscribe_order(dal: DAL) -> None:
    """同 event_type 注册两个 handler，按 subscribe 顺序调用。"""
    orch = Orchestrator(dal)
    order: list[int] = []

    orch.subscribe("test.event", lambda p: order.append(1))
    orch.subscribe("test.event", lambda p: order.append(2))
    orch.publish("test.event", object())

    assert order == [1, 2]


def test_handler_exception_does_not_block_others(dal: DAL, caplog: pytest.LogCaptureFixture) -> None:
    """第一个 handler 抛异常，第二个仍被调，publish 调用方不见异常。"""
    orch = Orchestrator(dal)
    called: list[bool] = []

    def bad_handler(p: object) -> None:
        raise RuntimeError("boom")

    def good_handler(p: object) -> None:
        called.append(True)

    orch.subscribe("test.event", bad_handler)
    orch.subscribe("test.event", good_handler)

    with caplog.at_level(logging.ERROR, logger="backend.core.orchestrator"):
        # publish 不抛异常
        orch.publish("test.event", object())

    # 第二个 handler 被调
    assert called == [True]
    # 有 ERROR 级别日志
    assert any(r.levelno == logging.ERROR for r in caplog.records)


# ── §7.2 内置 handler ────────────────────────────────────────────────────────


def _make_active_session(take_id: int) -> SessionState:
    """构造 take_active=True 的 SessionState（绕过 take_start stub）。"""
    session = SessionState()
    session.take_id = take_id
    session.take_active = True
    return session


def _asr_final_payload(
    *,
    text: str = "hello",
    start_frame: int = 0,
    end_frame: int = 800,
    speaker: str | None = "A",
    take_id: int | None = None,
) -> AsrFinalPayload:
    return AsrFinalPayload(
        text=text,
        start_frame=start_frame,
        end_frame=end_frame,
        speaker=speaker,
        take_id=take_id,
        is_partial=False,
    )


def test_asr_final_ch1_writes_segment_when_take_active(dal: DAL) -> None:
    """session.take_active=True 时 publish asr.final.ch1 写入 transcript_segments（ch=1）。"""
    scene_id = dal.create_scene("scene_test")
    take_id = dal.start_take(scene_id, take_number=1, start_ts=time.time())
    session = _make_active_session(take_id)
    orch = Orchestrator(dal, session=session)

    payload = _asr_final_payload(text="今天天气", speaker="演员A", start_frame=0, end_frame=800)
    orch.publish(ASR_FINAL_CH1, payload)

    segments = dal.list_segments(take_id, ch=1)
    assert len(segments) == 1
    assert segments[0].text == "今天天气"
    assert segments[0].speaker == "演员A"
    assert segments[0].ch == 1


def test_asr_final_ch1_skipped_when_take_inactive(dal: DAL) -> None:
    """session.take_active=False 时 publish asr.final.ch1 不写库。"""
    scene_id = dal.create_scene("scene_test")
    take_id = dal.start_take(scene_id, take_number=1, start_ts=time.time())
    session = SessionState()  # take_active 默认 False
    session.take_id = take_id
    orch = Orchestrator(dal, session=session)

    orch.publish(ASR_FINAL_CH1, _asr_final_payload())

    segments = dal.list_segments(take_id, ch=1)
    assert len(segments) == 0


def test_asr_final_ch1_falls_back_to_session_take_id_when_payload_null(dal: DAL) -> None:
    """payload.take_id=None 时 handler 回退用 session.take_id 写库。"""
    scene_id = dal.create_scene("scene_test")
    take_id = dal.start_take(scene_id, take_number=2, start_ts=time.time())
    session = _make_active_session(take_id)
    orch = Orchestrator(dal, session=session)

    # payload.take_id=None，handler 应用 session.take_id
    payload = _asr_final_payload(take_id=None, text="回退到 session take")
    orch.publish(ASR_FINAL_CH1, payload)

    segments = dal.list_segments(take_id, ch=1)
    assert len(segments) == 1
    assert segments[0].text == "回退到 session take"


def test_asr_final_ch1_uses_payload_take_id_when_provided(dal: DAL) -> None:
    """payload.take_id=session.take_id 时 handler 用 payload 路径写库（验证非纯回退）。"""
    scene_id = dal.create_scene("scene_test")
    take_id = dal.start_take(scene_id, take_number=3, start_ts=time.time())
    session = _make_active_session(take_id)
    orch = Orchestrator(dal, session=session)

    # payload.take_id 与 session.take_id 相同，走 payload 路径
    payload = _asr_final_payload(take_id=take_id, text="payload 路径写库")
    orch.publish(ASR_FINAL_CH1, payload)

    segments = dal.list_segments(take_id, ch=1)
    assert len(segments) == 1
    assert segments[0].text == "payload 路径写库"


def test_asr_final_ch1_writes_to_payload_take_id_on_mismatch(
    dal: DAL, caplog: pytest.LogCaptureFixture
) -> None:
    """payload.take_id 与 session.take_id 不匹配时按 payload 写库，且记 warning 日志。"""
    scene_id = dal.create_scene("scene_test")
    take5 = dal.start_take(scene_id, take_number=5, start_ts=time.time())
    take6 = dal.start_take(scene_id, take_number=6, start_ts=time.time())

    session = SessionState()
    session.take_id = take6
    session.take_active = True
    orch = Orchestrator(dal, session=session)

    # 跨 take 边界迟到 segment：payload 属于 take5，session 已切到 take6
    payload = _asr_final_payload(take_id=take5, text="迟到的 Cut")

    with caplog.at_level(logging.WARNING, logger="backend.core.orchestrator"):
        orch.publish(ASR_FINAL_CH1, payload)

    # take5 收到这段，take6 不收
    assert len(dal.list_segments(take5, ch=1)) == 1
    assert len(dal.list_segments(take6, ch=1)) == 0
    # warning 日志含关键词
    assert any(
        "cross-take" in r.message.lower() or "take_id" in r.message
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


def test_asr_final_ch2_writes_segment_when_take_active(dal: DAL) -> None:
    """session.take_active=True 时 publish asr.final.ch2 写入 transcript_segments（ch=2，speaker=None）。"""
    scene_id = dal.create_scene("scene_test")
    take_id = dal.start_take(scene_id, take_number=1, start_ts=time.time())
    session = _make_active_session(take_id)
    orch = Orchestrator(dal, session=session)

    payload = _asr_final_payload(text="录音师备注", speaker="录音师", start_frame=0, end_frame=800)
    orch.publish(ASR_FINAL_CH2, payload)

    segments = dal.list_segments(take_id, ch=2)
    assert len(segments) == 1
    assert segments[0].text == "录音师备注"
    # ch2 speaker 强制为 None（不管 payload.speaker）
    assert segments[0].speaker is None
    assert segments[0].ch == 2


def test_asr_final_ch2_skipped_when_take_inactive(dal: DAL) -> None:
    """session.take_active=False 时 publish asr.final.ch2 不写库。"""
    scene_id = dal.create_scene("scene_test")
    take_id = dal.start_take(scene_id, take_number=1, start_ts=time.time())
    session = SessionState()  # take_active 默认 False
    session.take_id = take_id
    orch = Orchestrator(dal, session=session)

    orch.publish(ASR_FINAL_CH2, _asr_final_payload())

    segments = dal.list_segments(take_id, ch=2)
    assert len(segments) == 0


def test_asr_final_ch1_and_ch2_share_timeline(dal: DAL) -> None:
    """同一 take 内交替 publish ch1/ch2，list_segments(take_id) 按 start_frame ASC 两路交错。"""
    scene_id = dal.create_scene("scene_test")
    take_id = dal.start_take(scene_id, take_number=1, start_ts=time.time())
    session = _make_active_session(take_id)
    orch = Orchestrator(dal, session=session)

    # 交替 publish：ch1@0, ch2@1600, ch1@3200, ch2@4800
    orch.publish(ASR_FINAL_CH1, _asr_final_payload(text="ch1_1", start_frame=0, end_frame=800))
    orch.publish(ASR_FINAL_CH2, _asr_final_payload(text="ch2_1", start_frame=1600, end_frame=2400))
    orch.publish(ASR_FINAL_CH1, _asr_final_payload(text="ch1_2", start_frame=3200, end_frame=4000))
    orch.publish(ASR_FINAL_CH2, _asr_final_payload(text="ch2_2", start_frame=4800, end_frame=5600))

    segments = dal.list_segments(take_id)  # 不传 ch，按 start_frame ASC
    assert len(segments) == 4
    assert [s.ch for s in segments] == [1, 2, 1, 2]
    assert [s.start_frame for s in segments] == [0, 1600, 3200, 4800]


# ── §7.3 SessionState 行为 ────────────────────────────────────────────────────


def test_session_take_start_sets_fields() -> None:
    """take_start 调用后 take_id / take_number / take_start_ts / shot 写入，take_active=True。"""
    session = SessionState()
    session.take_start(take_id=42, take_number=3, start_ts=1234567890.0, shot="S1A")

    assert session.take_id == 42
    assert session.take_number == 3
    assert session.take_start_ts == 1234567890.0
    assert session.shot == "S1A"
    assert session.take_active is True


def test_session_take_end_keeps_take_id() -> None:
    """take_end 后 take_active=False，take_id 不清空。"""
    session = SessionState()
    session.take_start(take_id=7, take_number=1, start_ts=1000.0, shot=None)
    session.take_end()

    assert session.take_active is False
    assert session.take_id == 7


def test_session_register_unregister_observer() -> None:
    """active_connections set 增删行为。"""
    session = SessionState()
    session.register_observer("conn-1")
    session.register_observer("conn-2")

    assert "conn-1" in session.active_connections
    assert "conn-2" in session.active_connections

    session.unregister_observer("conn-1")
    assert "conn-1" not in session.active_connections
    assert "conn-2" in session.active_connections
