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
