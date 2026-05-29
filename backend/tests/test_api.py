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


def test_take_start_with_wrong_token_returns_401(tmp_dal: DAL, monkeypatch) -> None:
    """带错误但 ASCII 的 token → 401。守 compare_digest 比对逻辑本身。

    现有逻辑对 ASCII 错 token 已正确返 401，本测试修前就应绿——补漏测（之前完全
    没测比对逻辑，删了 compare_digest 也不会红），作为安全回归。
    """
    orch = create_orchestrator(tmp_dal)
    client = _make_client(orch, monkeypatch)

    resp = client.post(
        "/api/v1/take/start",
        json={"scene_id": 1, "shot": "A"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


def test_take_start_with_non_ascii_token_returns_401(tmp_dal: DAL, monkeypatch) -> None:
    """带非 ASCII token（café 的 é，U+00E9，latin-1 内 → httpx 可编码进头）→ 401。

    header 值传 raw bytes b"Bearer caf\xe9"：httpx 对 str header 值按 ASCII 严格编码，
    "Bearer café" 会在客户端 UnicodeEncodeError 死掉（测到的是 transport 不是比对逻辑）。
    传 bytes 则 httpx 原样上线，starlette 服务端按 latin-1 解码 \xe9 → credentials
    "café"（非 ASCII str）——正是生产里的真实 bug 路径。
    修前红：secrets.compare_digest(str, str) 对含非 ASCII 的 str 在 auth.py 抛 TypeError，
    require_admin 未 catch → TestClient(raise_server_exceptions=True) 把 TypeError
    重抛进本测试（不是返 500）。
    修后绿：auth.py 改 bytes 比对，非 ASCII 安全比对失败 → 干净 401。
    """
    orch = create_orchestrator(tmp_dal)
    client = _make_client(orch, monkeypatch)

    resp = client.post(
        "/api/v1/take/start",
        json={"scene_id": 1, "shot": "A"},
        headers={"Authorization": b"Bearer caf\xe9"},
    )
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


def test_ws_non_ascii_token_rejected(tmp_dal: DAL, monkeypatch) -> None:
    """/ws?token=<非 ASCII> → 干净 close 1008（WebSocketDisconnect code==1008）。

    修前红：ws_endpoint 里 secrets.compare_digest(token, expected) 对含非 ASCII 的
    str 抛 TypeError，在 close(1008) 之前抛 → 握手异常终止，surface 的不是
    WebSocketDisconnect(1008)，pytest.raises(WebSocketDisconnect) 拿不到 1008 → RED。
    修后绿：ws.py 改 bytes 比对，比对干净失败 → close(1008)。
    用 café（é，latin-1 内）保证 httpx 能编码进 query，RED 是服务端 TypeError 而非
    客户端编码错。镜像 test_ws_without_token_rejected。
    """
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    app = create_app(create_orchestrator(tmp_dal))

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws?token=caf%C3%A9"):
                pass
    assert exc_info.value.code == 1008


def test_ws_binary_frame_cleans_up_connection(tmp_dal: DAL, monkeypatch) -> None:
    """连上 ws 后发 binary frame → 连接最终从 cm._active 清理（不泄漏）。

    机制：保活循环 await receive_text() 收到 binary frame 时 starlette 抛 KeyError
    （message 只有 "bytes" 没 "text"）。
    修前红：循环只 except WebSocketDisconnect，KeyError 逃逸 → cm.disconnect 不跑 →
    _active 残留 1 个死连接（泄漏）。
    修后绿：finally: cm.disconnect(websocket) 保证任何退出路径都清理 → _active 空。
    KeyError 在两种状态下都会传播（finally 不吞它），唯一判别是 len(cm._active)：
    修前 1、修后 0。故整个 with 块包 try/except KeyError，让异常到不了断言。
    退出 with 的 join 保证服务端 task 跑完 finally 才走到断言（同 test_ws_disconnect_cleans_up）。
    """
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    app = create_app(create_orchestrator(tmp_dal))

    with TestClient(app) as client:
        cm = client.app.state.connection_manager
        try:
            with client.websocket_connect(f"/ws?token={_TOKEN}") as ws:
                ws.send_bytes(b"binary-frame")
        except KeyError:
            pass  # binary frame 致服务端 KeyError，两种状态都传播；判别只看 _active
        assert len(cm._active) == 0  # type: ignore[attr-defined]


def test_take_changed_forwarded_to_ws(tmp_dal: DAL, monkeypatch) -> None:
    """连上 ws 后 POST /take/start → ws 收到一条 take.changed（loop 线程内投递路径）。

    端到端真实链路：take/start → _on_take_start 写库 → publish(TAKE_CHANGED) →
    CM.broadcast（loop 线程内，走 create_task 路径）→ ws.send_json。

    必须先 create_scene：_on_take_start 在 publish 之前 start_take 写库，scene 不存在
    会让它在 publish 前抛异常并被 publish 吞掉，take.changed 永不发，receive_json 死挂。
    """
    # 无超时风险：下面 ws.receive_json() 无超时，若上游回归致 take.changed 永不发，
    # CI 会超时挂死而非干净 fail。当前环境无 pytest-timeout 插件（--markers 无 timeout），
    # 不引脆弱的 watchdog 线程。TODO: 装 pytest-timeout 后给本函数加 @pytest.mark.timeout(N)。
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
    # 无超时风险：下面 ws.receive_json() 无超时，若上游回归致 asr.final 永不转发，
    # CI 会超时挂死而非干净 fail。当前环境无 pytest-timeout 插件（--markers 无 timeout），
    # 不引脆弱的 watchdog 线程。TODO: 装 pytest-timeout 后给本函数加 @pytest.mark.timeout(N)。
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


# ── 切片 B：codex review BLOCKING 修复回归测试 ─────────────────────────────────
#
# 这些测试直接构造 ConnectionManager（ws.py 此时已存在，非 RED 阶段模块缺失），
# 不经 TestClient，单元级覆盖 WS loop lifecycle / Future 处理两个 BLOCKING。


def test_log_future_exception_ignores_cancelled() -> None:
    """BLOCKING 2：done-callback 对已 cancelled 的 Future 不自炸。

    concurrent.futures.Future.exception() 对 cancelled future 抛 CancelledError，
    若 callback 不先判 fut.cancelled() 就取 exception()，callback 自身崩。
    修前：_log_future_exception 直接 fut.exception() → CancelledError 冒泡。
    修后：callback 开头 if fut.cancelled(): return → 安全 no-op。
    """
    from concurrent.futures import Future

    from backend.api.ws import ConnectionManager

    fut: Future[object] = Future()
    fut.cancel()
    assert fut.cancelled()

    # 不抛即通过（修前会抛 CancelledError）。
    ConnectionManager._log_future_exception(fut)


def test_broadcast_noop_after_loop_cleared() -> None:
    """shutdown 清 loop（set_loop(None)）后 broadcast 安全 no-op，不抛。

    覆盖 lifespan shutdown 段 cm.set_loop(None) 后的安全性。命中 _loop is None 守卫，
    基线已绿（保留作回归，确保 None 守卫永远在 is_running() 之前短路）。
    """
    from backend.api.ws import ConnectionManager
    from backend.core.events import TAKE_CHANGED, TakeChangedPayload

    cm = ConnectionManager()
    cm.set_loop(None)

    payload = TakeChangedPayload(
        take_id=1,
        scene_id=1,
        take_number=1,
        status="tbd",
        script_diff=None,
    )
    # 不抛即通过（None 守卫 no-op）。
    cm.broadcast(TAKE_CHANGED, payload)


def test_broadcast_noop_when_loop_not_running() -> None:
    """BLOCKING 1：loop 已建但未跑（stopped-but-not-closed）→ 跨线程 broadcast no-op。

    创建但从不 run 的 loop：is_closed() 为 False、is_running() 为 False。修前
    broadcast 只查 is_closed()，会走 else 分支 run_coroutine_threadsafe 把 coroutine
    调度到不跑的 loop → coroutine 泄漏。修后 is_running() 守卫直接 no-op。

    用 spy 替换 asyncio.run_coroutine_threadsafe 检测是否被调用：修前被调（RED），
    修后不被调（GREEN）。本测试在主线程跑，主线程无 running loop，
    asyncio.get_running_loop() 抛 RuntimeError → running=None → 命中跨线程 else 分支。
    """
    import asyncio as _asyncio

    from backend.api.ws import ConnectionManager
    from backend.core.events import TAKE_CHANGED, TakeChangedPayload

    loop = _asyncio.new_event_loop()  # 建但从不 run → not closed, not running
    try:
        assert not loop.is_closed()
        assert not loop.is_running()

        cm = ConnectionManager()
        cm.set_loop(loop)

        called = False

        def _spy(coro: object, target_loop: object):  # noqa: ANN202
            nonlocal called
            called = True
            coro.close()  # 防真泄漏（仅 spy 内）  # type: ignore[attr-defined]

        import backend.api.ws as ws_mod

        orig = ws_mod.asyncio.run_coroutine_threadsafe
        ws_mod.asyncio.run_coroutine_threadsafe = _spy  # type: ignore[assignment]
        try:
            payload = TakeChangedPayload(
                take_id=1,
                scene_id=1,
                take_number=1,
                status="tbd",
                script_diff=None,
            )
            cm.broadcast(TAKE_CHANGED, payload)
        finally:
            ws_mod.asyncio.run_coroutine_threadsafe = orig  # type: ignore[assignment]

        # 修前 called=True（调度到死 loop，泄漏）；修后 called=False（is_running 守卫 no-op）。
        assert called is False
    finally:
        loop.close()
