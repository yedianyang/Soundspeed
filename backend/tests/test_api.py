"""1.I FastAPI API 层 — 切片 A（REST 基座）测试。

TDD 红阶段：backend.api 尚不存在，5 个测试应全部 fail（feature-missing 红）。

覆盖（切片 A，仅 REST，不含 WS）：
  - GET /healthz → 200（无鉴权）
  - POST /api/v1/take/start 无 token → 401
  - POST /api/v1/take/start 带正确 token → 200 + orchestrator 收到 TakeStartPayload
  - POST /api/v1/take/end 带正确 token → 200 + orchestrator 收到 TakeEndPayload
  - POST /api/v1/take/end 在注入 deps 时创建 _l2_task（决策 1 核心，防回归）

测试用 FastAPI TestClient（httpx）。app 用 create_app(orchestrator) 工厂注入测试
orchestrator（带 tmp_dal）。ADMIN_TOKEN 用 monkeypatch.setenv 注入，create_app 在
构造时读环境（不在 import 时捕获，否则 setenv 不生效）。

注意：REST 测试一律 plain def，不加 @pytest.mark.asyncio。TestClient 在它自己的
portal 线程里同步驱动 event loop，POST 返回后即可直接断言 _l2_task，无需 await。
"""
from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.api.app import create_app
from backend.core.events import (
    ASR_FINAL_CH1,
    TAKE_END,
    TAKE_START,
    AsrFinalPayload,
    TakeEndPayload,
    TakeStartPayload,
)
from backend.core.orchestrator import Orchestrator, create_orchestrator
from backend.core.session import SessionState
from backend.db.dal import DAL

_TOKEN = "test-admin-token"


def _make_client(orchestrator: Orchestrator, monkeypatch) -> TestClient:
    """构造 TestClient：先 setenv ADMIN_TOKEN，再 create_app（构造时读环境）。"""
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    app = create_app(orchestrator)
    return TestClient(app)


def test_healthz_returns_200(tmp_dal: DAL, monkeypatch) -> None:
    """GET /healthz → 200（无鉴权）。"""
    orch = create_orchestrator(tmp_dal)
    client = _make_client(orch, monkeypatch)

    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_take_start_without_token_returns_401(tmp_dal: DAL, monkeypatch) -> None:
    """POST /api/v1/take/start 无 Authorization 头 → 401。"""
    orch = create_orchestrator(tmp_dal)
    client = _make_client(orch, monkeypatch)

    resp = client.post("/api/v1/take/start", json={"scene_id": 1, "shot": "A"})
    assert resp.status_code == 401


def test_take_start_with_valid_token_publishes_event(tmp_dal: DAL, monkeypatch) -> None:
    """带正确 Bearer token，POST take/start → 200，orchestrator 收到 TakeStartPayload。

    spy 断言：scene_id==1、shot=="A"、start_ts 是 float（服务端 time.time() 生成）。
    """
    orch = create_orchestrator(tmp_dal)
    client = _make_client(orch, monkeypatch)

    received: list[object] = []
    orch.subscribe(TAKE_START, lambda p: received.append(p))

    resp = client.post(
        "/api/v1/take/start",
        json={"scene_id": 1, "shot": "A"},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert resp.status_code == 200

    assert len(received) == 1
    payload = received[0]
    assert isinstance(payload, TakeStartPayload)
    assert payload.scene_id == 1
    assert payload.shot == "A"
    assert isinstance(payload.start_ts, float)


def test_take_end_with_valid_token_publishes_event(tmp_dal: DAL, monkeypatch) -> None:
    """带 token POST take/end（body 空）→ 200，orchestrator 收到 TakeEndPayload。

    spy 断言：end_ts 是 float（服务端 time.time() 生成）。
    """
    orch = create_orchestrator(tmp_dal)
    client = _make_client(orch, monkeypatch)

    received: list[object] = []
    orch.subscribe(TAKE_END, lambda p: received.append(p))

    # body 空：take/end 端点不要求 body，否则 422
    resp = client.post(
        "/api/v1/take/end",
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert resp.status_code == 200

    assert len(received) == 1
    payload = received[0]
    assert isinstance(payload, TakeEndPayload)
    assert isinstance(payload.end_ts, float)


def test_take_end_creates_l2_task_when_deps_injected(tmp_dal: DAL, monkeypatch) -> None:
    """决策 1 核心防回归：注入 deps 时 take/end 后 _l2_task 被创建。

    机制：take/end 的 fire-and-forget L2 靠 asyncio.get_running_loop()
    （orchestrator.py:214），且仅在注入 llm_service+l2_runner 时走到。
    端点必须 async def——FastAPI 在 TestClient 的 event loop 内直接 await async 端点，
    get_running_loop() 成功 → loop.create_task → _l2_task 被赋值。

    若端点被改回 def，FastAPI 会丢到线程池跑 → 无 running loop →
    get_running_loop() 抛 RuntimeError → 被 publish() 吞掉（orchestrator.py:79-82）→
    _l2_task 仍为 None。本测试就是守门它不被「简化」回 def。
    """
    scene_id = tmp_dal.create_scene("scene_api_l2")

    # stub llm_service（任意非 None）+ stub l2_runner（async 返回假对象）
    stub_svc = MagicMock()
    stub_runner = AsyncMock(return_value=MagicMock())

    session = SessionState()
    orch = create_orchestrator(
        tmp_dal, session, llm_service=stub_svc, l2_runner=stub_runner
    )
    client = _make_client(orch, monkeypatch)

    headers = {"Authorization": f"Bearer {_TOKEN}"}

    # 先起一个 take（take/end 需要 session.take_id 非 None 才会调度 L2）
    start_resp = client.post(
        "/api/v1/take/start",
        json={"scene_id": scene_id, "shot": None},
        headers=headers,
    )
    assert start_resp.status_code == 200
    assert session.take_id is not None

    # take/end → 在 async 端点的 running loop 下 fire-and-forget L2 被调度
    end_resp = client.post("/api/v1/take/end", headers=headers)
    assert end_resp.status_code == 200

    # 不 await：仅断言 task 被创建（决策 1 防回归核心断言）
    assert orch._l2_task is not None  # type: ignore[attr-defined]


# ── 切片 B：WS + 事件转发 ──────────────────────────────────────────────────────
#
# WS 测试必须用 `with TestClient(app) as client:`——只有 with 块才触发 lifespan，
# loop ref / orchestrator 订阅才生效。集合外的裸 TestClient 不跑 lifespan。
#
# ConnectionManager 一律通过 client.app.state.connection_manager 访问，
# 测试文件顶层不 import ws.py 符号——RED 阶段 ws.py 还不存在，顶层 import 会让整个
# 文件 collection 失败，连切片 A 的 5 个测试一起变红，分不清是 feature-missing 还是
# import 炸了。


def test_ws_without_token_rejected(tmp_dal: DAL, monkeypatch) -> None:
    """/ws 无 token → 握手被拒，close code 1008（不 accept）。

    断 code == 1008 而非仅断 WebSocketDisconnect：RED 阶段 /ws 路由还不存在，
    连不存在的 WS 路由本就抛 WebSocketDisconnect（关闭码非 1008），只断异常类型会假绿。
    断 1008 才真测到鉴权拒绝。
    """
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    app = create_app(create_orchestrator(tmp_dal))

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws"):
                pass
    assert exc_info.value.code == 1008


def test_ws_with_valid_token_connects(tmp_dal: DAL, monkeypatch) -> None:
    """/ws?token=<TOKEN> → 连上不被拒。"""
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    app = create_app(create_orchestrator(tmp_dal))

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?token={_TOKEN}") as ws:
            # 连上即可——能进入 with 块说明握手成功（accept 了）。
            assert ws is not None


def test_take_changed_forwarded_to_ws(tmp_dal: DAL, monkeypatch) -> None:
    """连上 ws 后 POST /take/start → ws 收到一条 take.changed（loop 线程内投递路径）。

    端到端真实链路：take/start → _on_take_start 写库 → publish(TAKE_CHANGED) →
    CM.broadcast（loop 线程内，走 create_task 路径）→ ws.send_json。

    必须先 create_scene：_on_take_start 在 publish 之前 start_take 写库，scene 不存在
    会让它在 publish 前抛异常并被 publish 吞掉，take.changed 永不发，receive_json 死挂。
    """
    scene_id = tmp_dal.create_scene("scene_ws_take")
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    app = create_app(create_orchestrator(tmp_dal))

    headers = {"Authorization": f"Bearer {_TOKEN}"}
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?token={_TOKEN}") as ws:
            resp = client.post(
                "/api/v1/take/start",
                json={"scene_id": scene_id, "shot": "A"},
                headers=headers,
            )
            assert resp.status_code == 200

            msg = ws.receive_json()
            assert msg["topic"] == "take.changed"
            assert msg["payload"]["scene_id"] == scene_id
            assert msg["payload"]["take_id"] is not None
            assert msg["payload"]["take_number"] == 1


def test_asr_final_forwarded_from_background_thread(tmp_dal: DAL, monkeypatch) -> None:
    """连上 ws 后，从后台线程 publish(ASR_FINAL_CH1, ...) → ws 收到 asr.final.ch1。

    跨线程路径（run_coroutine_threadsafe）：模拟未来 1.A ASR 线程触发同步 handler。
    take 未 active 时 _on_asr_final 直接 return（不写库），桥接转发仍应发生——
    转发是订阅 CM.broadcast，与内置 segment handler 互不影响。
    """
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    orch = create_orchestrator(tmp_dal)
    app = create_app(orch)

    payload = AsrFinalPayload(
        text="hello from background",
        start_frame=0,
        end_frame=1000,
        speaker="A",
        take_id=None,
        is_partial=False,
    )

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?token={_TOKEN}") as ws:
            t = threading.Thread(target=lambda: orch.publish(ASR_FINAL_CH1, payload))
            t.start()
            t.join()

            msg = ws.receive_json()
            assert msg["topic"] == "asr.final.ch1"
            assert msg["payload"]["text"] == "hello from background"
            assert msg["payload"]["speaker"] == "A"


def test_ws_disconnect_cleans_up(tmp_dal: DAL, monkeypatch) -> None:
    """连上再退出 with → cm._active 为空（连接清理）。"""
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    app = create_app(create_orchestrator(tmp_dal))

    with TestClient(app) as client:
        cm = client.app.state.connection_manager
        with client.websocket_connect(f"/ws?token={_TOKEN}"):
            pass
        # 退出 ws 上下文：TestClient join 服务端 task，
        # except WebSocketDisconnect 里 cm.disconnect 跑完才返回 → _active 应空。
        assert len(cm._active) == 0  # type: ignore[attr-defined]
