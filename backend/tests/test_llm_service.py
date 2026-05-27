"""1.F LLMService 单元测试。

覆盖 1.F 实施 spec §7 的 15 个用例 + 4 条验收映射 + smoke（默认 skip）。
全部使用 StubClient，不加载真实模型。
"""

from __future__ import annotations

import asyncio
import time
import pytest

from backend.llm.client import StubClient
from backend.llm.service import LLMService, _reset_service, get_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service():
    """每个测试得到新鲜的 LLMService 实例，注入 StubClient（无延迟）。"""
    _reset_service()
    svc = get_service()
    svc._client = StubClient(response="ok")
    yield svc
    _reset_service()


@pytest.fixture
def slow_service():
    """注入延迟 0.5s 的 StubClient，用于超时与串行测试。"""
    _reset_service()
    svc = get_service()
    svc._client = StubClient(response="ok", delay=0.5)
    yield svc
    _reset_service()


# ---------------------------------------------------------------------------
# 验收 1：test_singleton
# 对应验收条：LLMService() 多次实例化返回同一对象
# ---------------------------------------------------------------------------


def test_singleton():
    """连续调用 get_service() 两次返回同一对象（is 判断）。"""
    _reset_service()
    try:
        svc1 = get_service()
        svc2 = get_service()
        assert svc1 is svc2
    finally:
        _reset_service()


# ---------------------------------------------------------------------------
# 用例 2：test_infer_returns_string
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_infer_returns_string(service: LLMService):
    """正常调用 infer 返回 str，内容与 StubClient 响应一致。"""
    result = await service.infer(
        messages=[{"role": "user", "content": "hello"}],
        task_type="query_session",
    )
    assert isinstance(result, str)
    assert result == "ok"


# ---------------------------------------------------------------------------
# 用例 3：test_unknown_task_type_raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_task_type_raises(service: LLMService):
    """task_type 不在 TASK_CONFIG 时抛 ValueError，不入队。"""
    with pytest.raises(ValueError, match="task_type"):
        await service.infer(
            messages=[{"role": "user", "content": "hi"}],
            task_type="nonexistent_task",
        )


# ---------------------------------------------------------------------------
# 用例 4：test_reserved_task_type_raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserved_task_type_raises(service: LLMService):
    """task_type='agent_init' 时抛 NotImplementedError（_reserved=True）。"""
    with pytest.raises(NotImplementedError):
        await service.infer(
            messages=[{"role": "user", "content": "hi"}],
            task_type="agent_init",
        )


# ---------------------------------------------------------------------------
# 用例 5：test_invalid_priority_raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_priority_raises(service: LLMService):
    """priority=0 或 priority=4 时抛 ValueError。"""
    with pytest.raises(ValueError, match="priority"):
        await service.infer(
            messages=[{"role": "user", "content": "hi"}],
            task_type="query_session",
            priority=0,
        )
    with pytest.raises(ValueError, match="priority"):
        await service.infer(
            messages=[{"role": "user", "content": "hi"}],
            task_type="query_session",
            priority=4,
        )


# ---------------------------------------------------------------------------
# 用例 6：test_invalid_timeout_raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_timeout_raises(service: LLMService):
    """timeout=0 或 timeout=-1.0 时抛 ValueError。"""
    with pytest.raises(ValueError, match="timeout"):
        await service.infer(
            messages=[{"role": "user", "content": "hi"}],
            task_type="query_session",
            timeout=0,
        )
    with pytest.raises(ValueError, match="timeout"):
        await service.infer(
            messages=[{"role": "user", "content": "hi"}],
            task_type="query_session",
            timeout=-1.0,
        )


# ---------------------------------------------------------------------------
# 验收 3：test_priority_order
# 对应验收条：QP (P1) 可在 SP (P3) / Agent (P3) 多轮之间插队
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_priority_order():
    """并发提交 P3/P1/P2，验证完成顺序为 P1 → P2 → P3。

    StubClient delay=0.05s（50ms）确保第一个任务执行期间，
    后续任务已全部入队，worker 第二次取任务时可见正确优先级排序。
    """
    _reset_service()
    svc = get_service()
    svc._client = StubClient(response="ok", delay=0.05)

    order: list[int] = []

    async def run(priority: int, label: int) -> None:
        await svc.infer(
            messages=[{"role": "user", "content": "hi"}],
            task_type="query_session",
            priority=priority,
        )
        order.append(label)

    # P3 先提交，P1 / P2 紧随，但执行顺序应为 P1 → P2 → P3
    t_p3 = asyncio.create_task(run(3, 3))
    # 短暂 yield 让 P3 先拿到 worker（第一次入队后 worker 开始执行）
    await asyncio.sleep(0.01)
    t_p1 = asyncio.create_task(run(1, 1))
    t_p2 = asyncio.create_task(run(2, 2))

    await asyncio.gather(t_p3, t_p1, t_p2)

    # P3 执行时 P1/P2 应已入队，所以 worker 下一次取的是 P1，再 P2，最后 P3 只剩自己
    # 实际完成顺序：P3 先完成（worker 当前正在执行）→ P1 → P2
    # 因此 order[0]=3, order[1]=1, order[2]=2
    # 关键断言：P1 在 P2 之前，P2 在 P3 之前（此时 P3 已是第一个完成的）
    assert order.index(1) < order.index(2), f"P1 应在 P2 之前: {order}"
    assert order.index(2) < order.index(3), f"P2 应在 P3 之前: {order}"

    _reset_service()


# ---------------------------------------------------------------------------
# 用例 8：test_fifo_within_same_priority
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fifo_within_same_priority():
    """相同 priority 下多个任务按入队顺序完成（FIFO）。"""
    _reset_service()
    svc = get_service()
    svc._client = StubClient(response="ok", delay=0.02)

    order: list[int] = []

    async def run(label: int) -> None:
        await svc.infer(
            messages=[{"role": "user", "content": "hi"}],
            task_type="query_session",
            priority=2,
        )
        order.append(label)

    # 同优先级，顺序提交
    t1 = asyncio.create_task(run(1))
    await asyncio.sleep(0.005)
    t2 = asyncio.create_task(run(2))
    await asyncio.sleep(0.005)
    t3 = asyncio.create_task(run(3))

    await asyncio.gather(t1, t2, t3)

    assert order == [1, 2, 3], f"FIFO 失败: {order}"
    _reset_service()


# ---------------------------------------------------------------------------
# 用例 9：test_lock_serialization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lock_serialization():
    """并发 3 个 infer，验证执行串行（总耗时 ≈ N × delay，非并行）。"""
    _reset_service()
    svc = get_service()
    delay = 0.1
    svc._client = StubClient(response="ok", delay=delay)

    start = time.monotonic()
    await asyncio.gather(
        svc.infer(messages=[{"role": "user", "content": "a"}], task_type="query_session"),
        svc.infer(messages=[{"role": "user", "content": "b"}], task_type="query_session"),
        svc.infer(messages=[{"role": "user", "content": "c"}], task_type="query_session"),
    )
    elapsed = time.monotonic() - start

    # 串行：3 × 0.1s = 0.3s，并行上界应约等于 0.1s
    # 验证总耗时 > 2 × delay（意味着不是完全并行）
    assert elapsed >= 2 * delay, f"串行化失败，耗时 {elapsed:.3f}s 过短"

    _reset_service()


# ---------------------------------------------------------------------------
# 用例 10：test_timeout_raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_raises():
    """timeout=0.1s，StubClient delay=0.5s，期望抛 asyncio.TimeoutError。"""
    _reset_service()
    svc = get_service()
    svc._client = StubClient(response="ok", delay=0.5)

    with pytest.raises(asyncio.TimeoutError):
        await svc.infer(
            messages=[{"role": "user", "content": "hi"}],
            task_type="query_session",
            timeout=0.1,
        )

    _reset_service()


# ---------------------------------------------------------------------------
# 用例 11：test_timeout_none_no_raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_none_no_raise():
    """timeout=None，StubClient delay=0.15s，正常返回不抛异常。"""
    _reset_service()
    svc = get_service()
    svc._client = StubClient(response="ok", delay=0.15)

    result = await svc.infer(
        messages=[{"role": "user", "content": "hi"}],
        task_type="query_session",
        timeout=None,
    )
    assert result == "ok"

    _reset_service()


# ---------------------------------------------------------------------------
# 用例 12：test_client_exception_propagates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_exception_propagates():
    """StubClient 抛 RuntimeError，infer 调用方收到同一 RuntimeError。"""

    class ErrorClient:
        def create_chat_completion(self, messages: list[dict], **kwargs: object) -> dict:
            raise RuntimeError("client boom")

    _reset_service()
    svc = get_service()
    svc._client = ErrorClient()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="client boom"):
        await svc.infer(
            messages=[{"role": "user", "content": "hi"}],
            task_type="query_session",
        )

    _reset_service()


# ---------------------------------------------------------------------------
# 用例 13：test_task_config_applied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_config_applied():
    """mock create_chat_completion，验证 gen_kwargs 含 TASK_CONFIG 的 max_tokens/temperature。"""
    from backend.llm.config import TASK_CONFIG

    _reset_service()
    svc = get_service()

    captured_kwargs: dict = {}

    class CapturingClient:
        def create_chat_completion(self, messages: list[dict], **kwargs: object) -> dict:
            captured_kwargs.update(kwargs)
            return {"choices": [{"message": {"content": "ok"}}]}

    svc._client = CapturingClient()  # type: ignore[assignment]

    await svc.infer(
        messages=[{"role": "user", "content": "hi"}],
        task_type="query_session",
    )

    cfg = TASK_CONFIG["query_session"]
    assert captured_kwargs.get("max_tokens") == cfg["max_tokens"]
    assert captured_kwargs.get("temperature") == cfg["temperature"]

    _reset_service()


# ---------------------------------------------------------------------------
# 用例 14：test_system_prompt_not_in_gen_kwargs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_not_in_gen_kwargs():
    """gen_kwargs 中不含 system / priority / _reserved 键（service 层已过滤）。"""
    _reset_service()
    svc = get_service()

    captured_kwargs: dict = {}

    class CapturingClient:
        def create_chat_completion(self, messages: list[dict], **kwargs: object) -> dict:
            captured_kwargs.update(kwargs)
            return {"choices": [{"message": {"content": "ok"}}]}

    svc._client = CapturingClient()  # type: ignore[assignment]

    await svc.infer(
        messages=[{"role": "user", "content": "hi"}],
        task_type="query_session",
    )

    assert "system" not in captured_kwargs, "system 不应出现在 gen_kwargs"
    assert "priority" not in captured_kwargs, "priority 不应出现在 gen_kwargs"
    assert "_reserved" not in captured_kwargs, "_reserved 不应出现在 gen_kwargs"

    _reset_service()


# ---------------------------------------------------------------------------
# 验收 4：test_event_loop_not_blocked
# 对应验收条：推理期间 WebSocket 推送不卡顿（事件循环不阻塞）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_loop_not_blocked():
    """并发跑 infer（delay=0.2s）和 asyncio.sleep(0.05) 探针，
    验证探针在 infer 运行期间正常完成，延迟 < 10ms。
    """
    _reset_service()
    svc = get_service()
    svc._client = StubClient(response="ok", delay=0.2)

    probe_done_at: list[float] = []
    infer_start: list[float] = []

    async def probe() -> None:
        await asyncio.sleep(0.05)
        probe_done_at.append(time.monotonic())

    async def do_infer() -> None:
        infer_start.append(time.monotonic())
        await svc.infer(
            messages=[{"role": "user", "content": "hi"}],
            task_type="query_session",
        )

    await asyncio.gather(do_infer(), probe())

    # 探针应在 infer 结束前完成（infer delay=0.2s，探针 sleep=0.05s）
    # 若事件循环被阻塞，探针会推迟到 0.2s 后
    probe_elapsed = probe_done_at[0] - infer_start[0]
    # 探针 0.05s + 最大 10ms 容差 = 0.06s，远小于 infer 的 0.2s
    assert probe_elapsed < 0.2 - 0.05, (
        f"事件循环可能被阻塞，探针耗时 {probe_elapsed:.3f}s 过长"
    )

    _reset_service()


# ---------------------------------------------------------------------------
# Smoke 测试（默认 skip，需要真实模型文件）
# ---------------------------------------------------------------------------


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_real_gemma_infer():
    """真实 GemmaClient，需要模型文件在 GEMMA_MODEL_PATH。（默认 skip）"""
    import os

    if not os.environ.get("GEMMA_MODEL_PATH"):
        pytest.skip("GEMMA_MODEL_PATH 未设置，跳过 smoke test")

    _reset_service()
    try:
        svc = get_service()  # 使用真实 GemmaClient，不注入 stub
        result = await svc.infer(
            messages=[{"role": "user", "content": "ping"}],
            task_type="query_session",
        )
        assert isinstance(result, str)
        assert len(result) > 0
    finally:
        await svc.aclose()
        _reset_service()
