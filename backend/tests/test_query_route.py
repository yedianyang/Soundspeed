import asyncio

from backend.api.routes.query import run_qp_and_broadcast


class _StubCM:
    def __init__(self):
        self.calls = []

    def broadcast(self, topic, payload):
        self.calls.append((topic, payload))


class _StubService:
    pass


def test_run_qp_and_broadcast_broadcasts(monkeypatch):
    async def _fake_query(*, text, dal, service, timeout=30.0):
        return "第一场一共拍了 3 条。"

    monkeypatch.setattr("backend.api.routes.query.run_qp_query", _fake_query)
    cm = _StubCM()
    answer = asyncio.run(
        run_qp_and_broadcast("第一场拍了多少条", "conn-7", dal=None, service=_StubService(), cm=cm)
    )
    assert answer == "第一场一共拍了 3 条。"
    assert cm.calls[0][0] == "qp.answer.conn-7"
    assert cm.calls[0][1].connection_id == "conn-7"
    assert cm.calls[0][1].answer_text == answer


def test_run_qp_and_broadcast_error_friendly(monkeypatch):
    async def _boom(*, text, dal, service, timeout=30.0):
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.api.routes.query.run_qp_query", _boom)
    cm = _StubCM()
    answer = asyncio.run(run_qp_and_broadcast("x", "c1", dal=None, service=_StubService(), cm=cm))
    assert "抱歉" in answer
    assert cm.calls[0][0] == "qp.answer.c1"


def test_run_qp_and_broadcast_carries_client_id(monkeypatch):
    """文本 query 分支：client_id 透传进 qp.answer payload，供前端队列按 client_id 把答案
    落到对应那条 qaItem（与 voice-qp 共享同一 client_id 字段契约）。"""
    async def _fake_query(*, text, dal, service, timeout=30.0):
        return "答案"

    monkeypatch.setattr("backend.api.routes.query.run_qp_query", _fake_query)
    cm = _StubCM()
    asyncio.run(
        run_qp_and_broadcast(
            "q", "conn-9", dal=None, service=_StubService(), cm=cm, client_id="cid-1"
        )
    )
    assert cm.calls[0][1].client_id == "cid-1"


def test_run_qp_and_broadcast_client_id_defaults_none(monkeypatch):
    """不传 client_id（如直连 /api/v1/query demo）→ payload.client_id 为 None，向后兼容。"""
    async def _fake_query(*, text, dal, service, timeout=30.0):
        return "答案"

    monkeypatch.setattr("backend.api.routes.query.run_qp_query", _fake_query)
    cm = _StubCM()
    asyncio.run(run_qp_and_broadcast("q", "conn-9", dal=None, service=_StubService(), cm=cm))
    assert cm.calls[0][1].client_id is None
