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


# ── 1.J-1.L 新增：GET 端点 / llm.status 转发 / dev ASR 注入 / llm_service lifecycle ──


def test_list_takes_empty_returns_200(tmp_dal: DAL, monkeypatch) -> None:
    """GET /api/v1/takes（带 token）→ 200，body {"takes":[]}。"""
    orch = create_orchestrator(tmp_dal)
    client = _make_client(orch, monkeypatch)

    resp = client.get("/api/v1/takes", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 200
    assert resp.json() == {"takes": []}


def test_list_takes_returns_all(tmp_dal: DAL, monkeypatch) -> None:
    """写两条 take → GET /api/v1/takes 返回 len==2。"""
    orch = create_orchestrator(tmp_dal)
    client = _make_client(orch, monkeypatch)
    headers = {"Authorization": f"Bearer {_TOKEN}"}

    scene_id = tmp_dal.create_scene("scene_lt1")
    # 通过 REST 端点建 take（保证 session 状态正确）
    client.post("/api/v1/take/start", json={"scene_id": scene_id, "shot": "A"}, headers=headers)
    client.post("/api/v1/take/end", headers=headers)
    client.post("/api/v1/take/start", json={"scene_id": scene_id, "shot": "B"}, headers=headers)
    client.post("/api/v1/take/end", headers=headers)

    resp = client.get("/api/v1/takes", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["takes"]) == 2


def test_list_takes_scene_id_filter(tmp_dal: DAL, monkeypatch) -> None:
    """scene_1 + scene_2 各一条，?scene_id=scene_1 → 只返回 scene_1 的。"""
    orch = create_orchestrator(tmp_dal)
    client = _make_client(orch, monkeypatch)
    headers = {"Authorization": f"Bearer {_TOKEN}"}

    scene1 = tmp_dal.create_scene("scene_f1a")
    scene2 = tmp_dal.create_scene("scene_f1b")
    client.post("/api/v1/take/start", json={"scene_id": scene1, "shot": None}, headers=headers)
    client.post("/api/v1/take/end", headers=headers)
    client.post("/api/v1/take/start", json={"scene_id": scene2, "shot": None}, headers=headers)
    client.post("/api/v1/take/end", headers=headers)

    resp = client.get(f"/api/v1/takes?scene_id={scene1}", headers=headers)
    assert resp.status_code == 200
    takes = resp.json()["takes"]
    assert len(takes) == 1
    assert takes[0]["scene_id"] == scene1


def test_list_takes_field_contract(tmp_dal: DAL, monkeypatch) -> None:
    """TakeDTO 含 11 个规定字段，有意省略 performer_issues / audio_quality。"""
    orch = create_orchestrator(tmp_dal)
    client = _make_client(orch, monkeypatch)
    headers = {"Authorization": f"Bearer {_TOKEN}"}

    scene_id = tmp_dal.create_scene("scene_fc1")
    client.post("/api/v1/take/start", json={"scene_id": scene_id, "shot": "X"}, headers=headers)
    client.post("/api/v1/take/end", headers=headers)

    resp = client.get("/api/v1/takes", headers=headers)
    assert resp.status_code == 200
    take = resp.json()["takes"][0]

    required_fields = {
        "take_id", "scene_id", "take_number", "shot",
        "start_ts", "end_ts", "status",
        "script_diff", "notes", "created_at", "updated_at",
    }
    assert required_fields.issubset(take.keys())
    # 有意省略字段不得出现
    assert "performer_issues" not in take
    assert "audio_quality" not in take


def test_list_takes_without_token_returns_401(tmp_dal: DAL, monkeypatch) -> None:
    """GET /api/v1/takes 无 Authorization 头 → 401。"""
    orch = create_orchestrator(tmp_dal)
    client = _make_client(orch, monkeypatch)

    resp = client.get("/api/v1/takes")
    assert resp.status_code == 401


def test_get_take_returns_detail_with_segments(tmp_dal: DAL, monkeypatch) -> None:
    """GET /api/v1/takes/{take_id} → 200，含 segments 列表。"""
    orch = create_orchestrator(tmp_dal)
    client = _make_client(orch, monkeypatch)
    headers = {"Authorization": f"Bearer {_TOKEN}"}

    scene_id = tmp_dal.create_scene("scene_det1")
    client.post("/api/v1/take/start", json={"scene_id": scene_id, "shot": None}, headers=headers)
    # 直接用 DAL 插入 segment，避免需要 ASR pipeline
    take_id = tmp_dal.list_takes(scene_id)[0].take_id
    tmp_dal.insert_segment(take_id=take_id, ch=1, speaker="A", text="hello", start_frame=0, end_frame=1000)

    resp = client.get(f"/api/v1/takes/{take_id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["take_id"] == take_id
    assert "segments" in body
    assert len(body["segments"]) == 1
    seg = body["segments"][0]
    assert seg["text"] == "hello"
    assert seg["ch"] == 1
    assert seg["speaker"] == "A"


def test_get_take_404_when_missing(tmp_dal: DAL, monkeypatch) -> None:
    """GET /api/v1/takes/999 → 404。"""
    orch = create_orchestrator(tmp_dal)
    client = _make_client(orch, monkeypatch)
    headers = {"Authorization": f"Bearer {_TOKEN}"}

    resp = client.get("/api/v1/takes/999", headers=headers)
    assert resp.status_code == 404


def test_llm_status_forwarded_to_ws(tmp_dal: DAL, monkeypatch) -> None:
    """合成 publish llm.status → 已连 WS 收到 {"topic":"llm.status",...}。

    llm.status / LlmStatusPayload 导入放函数体内（RED 阶段这两个符号尚不存在，
    顶层 import 会炸掉 collection，令 35 条基线全红，分不清 feature-missing 还是 import 错）。
    """
    from backend.core.events import LLM_STATUS, LlmStatusPayload  # noqa: PLC0415

    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    orch = create_orchestrator(tmp_dal)
    app = create_app(orch)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?token={_TOKEN}") as ws:
            payload = LlmStatusPayload(state="loading", task_type="l2_take", take_id=1)
            t = threading.Thread(target=lambda: orch.publish(LLM_STATUS, payload))
            t.start()
            t.join()

            msg = ws.receive_json()
            assert msg["topic"] == "llm.status"
            assert msg["payload"]["state"] == "loading"
            assert msg["payload"]["task_type"] == "l2_take"
            assert msg["payload"]["take_id"] == 1


def test_debug_asr_publishes_final(tmp_dal: DAL, monkeypatch) -> None:
    """SOUNDSPEED_DEV=1 挂载；POST /api/v1/debug/asr {is_partial:false} → orch 收到 ASR_FINAL_CH1。

    ASR_FINAL_CH1 / AsrFinalPayload 已在文件顶层 import（既有测试依赖），可直接用。
    """
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    monkeypatch.setenv("SOUNDSPEED_DEV", "1")
    orch = create_orchestrator(tmp_dal)
    app = create_app(orch)
    client = TestClient(app)

    received: list[object] = []
    orch.subscribe(ASR_FINAL_CH1, lambda p: received.append(p))

    resp = client.post(
        "/api/v1/debug/asr",
        json={"ch": 1, "text": "hello debug", "speaker": "A", "is_partial": False},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert resp.status_code == 200
    assert len(received) == 1
    from backend.core.events import AsrFinalPayload  # noqa: PLC0415
    payload = received[0]
    assert isinstance(payload, AsrFinalPayload)
    assert payload.text == "hello debug"
    assert payload.speaker == "A"
    assert payload.is_partial is False


def test_debug_asr_publishes_partial(tmp_dal: DAL, monkeypatch) -> None:
    """SOUNDSPEED_DEV=1；is_partial:true → orch 收到 ASR_PARTIAL_CH1。"""
    from backend.core.events import ASR_PARTIAL_CH1  # noqa: PLC0415

    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    monkeypatch.setenv("SOUNDSPEED_DEV", "1")
    orch = create_orchestrator(tmp_dal)
    app = create_app(orch)
    client = TestClient(app)

    received: list[object] = []
    orch.subscribe(ASR_PARTIAL_CH1, lambda p: received.append(p))

    resp = client.post(
        "/api/v1/debug/asr",
        json={"ch": 1, "text": "partial text", "speaker": None, "is_partial": True},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert resp.status_code == 200
    assert len(received) == 1
    from backend.core.events import AsrPartialPayload  # noqa: PLC0415
    payload = received[0]
    assert isinstance(payload, AsrPartialPayload)
    assert payload.text == "partial text"
    assert payload.is_partial is True


def test_debug_asr_final_persists_segment_when_take_active(tmp_dal: DAL, monkeypatch) -> None:
    """回归测试：active take 时 POST /debug/asr is_partial=false → 段写入 DB。

    smoke 发现的 bug：end_frame == start_frame 触发 CHECK(end_frame > start_frame)，
    sqlite3.IntegrityError 被 publish() 吞掉，段静默不存库（1.L take detail 为空）。
    修复后 end_frame = start_frame + 1000，此测试断言段确实写入。

    流程：先 POST take/start（session.take_active=True + take 行存在）→
    POST debug/asr ch=1 is_partial=false → dal.list_segments(take_id) 返回该段。
    """
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    monkeypatch.setenv("SOUNDSPEED_DEV", "1")
    scene_id = tmp_dal.create_scene("scene_debug_persist")
    orch = create_orchestrator(tmp_dal)
    app = create_app(orch)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {_TOKEN}"}

    # 先起一个 take，使 session.take_active=True
    resp = client.post(
        "/api/v1/take/start",
        json={"scene_id": scene_id, "shot": None},
        headers=headers,
    )
    assert resp.status_code == 200
    take_id = tmp_dal.list_takes(scene_id)[0].take_id

    # 注入 final ASR（修复前会因 CHECK 约束失败静默丢弃）
    resp = client.post(
        "/api/v1/debug/asr",
        json={"ch": 1, "text": "injected segment", "speaker": "A", "is_partial": False},
        headers=headers,
    )
    assert resp.status_code == 200

    # 断言段已写入 DB（不是只推了 WS 就完）
    segments = tmp_dal.list_segments(take_id)
    assert len(segments) == 1, f"段应写库，实际 list_segments 返回 {len(segments)} 条"
    assert segments[0].text == "injected segment"
    assert segments[0].ch == 1
    assert segments[0].speaker == "A"
    # 顺带验证 end_frame > start_frame（约束合规）
    assert segments[0].end_frame > segments[0].start_frame


def test_debug_asr_absent_without_dev_flag(tmp_dal: DAL, monkeypatch) -> None:
    """SOUNDSPEED_DEV 未设 → POST /api/v1/debug/asr → 404（路由未挂载）。"""
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    monkeypatch.delenv("SOUNDSPEED_DEV", raising=False)
    orch = create_orchestrator(tmp_dal)
    app = create_app(orch)
    client = TestClient(app)

    resp = client.post(
        "/api/v1/debug/asr",
        json={"ch": 1, "text": "x", "speaker": None, "is_partial": False},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert resp.status_code == 404


def test_create_app_stores_llm_service(tmp_dal: DAL, monkeypatch) -> None:
    """create_app(orch, llm_service=stub) → app.state.llm_service is stub。"""
    from unittest.mock import MagicMock

    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    stub_llm = MagicMock()
    orch = create_orchestrator(tmp_dal)
    app = create_app(orch, llm_service=stub_llm)
    assert app.state.llm_service is stub_llm


def test_lifespan_calls_aclose_on_shutdown(tmp_dal: DAL, monkeypatch) -> None:
    """TestClient with 块退出 → stub llm_service.aclose() 被 await 一次。"""
    from unittest.mock import AsyncMock, MagicMock

    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    stub_llm = MagicMock()
    stub_llm.aclose = AsyncMock()
    orch = create_orchestrator(tmp_dal)
    app = create_app(orch, llm_service=stub_llm)

    with TestClient(app):
        pass  # 触发 lifespan startup + shutdown

    stub_llm.aclose.assert_awaited_once()


def test_entrypoint_build_app_healthz(tmp_path, monkeypatch) -> None:
    """build_app() → TestClient → GET /healthz == 200，llm_service 是 get_service() 单例。

    设 SOUNDSPEED_DB 指向 tmp_path，避免写 ./soundspeed.db。
    llm_service import 放函数体内，避免 RED 阶段符号不存在时炸 collection。
    """
    db_file = tmp_path / "entry_test.db"
    monkeypatch.setenv("SOUNDSPEED_DB", str(db_file))
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)

    from backend.api.entrypoint import build_app  # noqa: PLC0415
    from backend.llm.service import get_service  # noqa: PLC0415

    app = build_app()
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
    assert app.state.llm_service is get_service()


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
