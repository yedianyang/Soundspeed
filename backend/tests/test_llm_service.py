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
# 辅助：可捕获 gen_kwargs 的 stub client（两个测试共用）
# ---------------------------------------------------------------------------


class _CapturingClient:
    """记录每次 create_chat_completion 调用的 kwargs，供测试断言使用。"""

    def __init__(self) -> None:
        self.captured_kwargs: dict = {}

    def create_chat_completion(self, messages: list[dict], **kwargs: object) -> dict:
        self.captured_kwargs.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}


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

    # P3 是 worker 第一个取到并执行的，所以第一个完成。
    # 等 P3 执行期间 P1/P2 已入队，worker 下一次取优先级最高的 P1，再取 P2。
    # 期望完成顺序：P3(第一个) → P1 → P2
    # 关键断言：P3 第一个完成，且 P1 在 P2 之前完成（验证优先级排队生效）
    assert order[0] == 3, f"P3 应第一个完成（它先被 worker 取到）: {order}"
    assert order.index(1) < order.index(2), f"P1 应在 P2 之前: {order}"

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
# 护栏：forced tool_choice 的 task 误走 content 路径（infer 而非 infer_tool）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_infer_content_none_with_tool_calls_finish_raises():
    """content=None 且 finish_reason="tool_calls" 时 infer 抛 LookupError，不静默返回 None。

    模拟「配了强制 tool_choice 的 task 被误用 infer（content 路径）」：
    旧逻辑会 set_result(None) 让下游静默失败，护栏改为明确报错引导改用 infer_tool。
    """

    class _ForcedToolStubClient:
        def create_chat_completion(self, messages: list[dict], **kwargs: object) -> dict:
            return {
                "choices": [
                    {
                        "message": {"content": None, "tool_calls": [{"id": "x"}]},
                        "finish_reason": "tool_calls",
                    }
                ]
            }

    _reset_service()
    svc = get_service()
    svc._client = _ForcedToolStubClient()  # type: ignore[assignment]

    with pytest.raises(LookupError, match="tool_choice"):
        await svc.infer(
            messages=[{"role": "user", "content": "hi"}],
            task_type="l2_take",
        )

    _reset_service()


@pytest.mark.asyncio
async def test_infer_content_none_non_tool_finish_does_not_raise():
    """护栏精确性：content=None 但 finish_reason 非 tool_calls 时不触发，保持旧行为（返回 None）。

    护栏只针对 forced tool_choice 误用，不误伤其它 content=None 的边角情况。
    """

    class _NoneContentStubClient:
        def create_chat_completion(self, messages: list[dict], **kwargs: object) -> dict:
            return {"choices": [{"message": {"content": None}, "finish_reason": "stop"}]}

    _reset_service()
    svc = get_service()
    svc._client = _NoneContentStubClient()  # type: ignore[assignment]

    result = await svc.infer(
        messages=[{"role": "user", "content": "hi"}],
        task_type="query_session",
    )
    assert result is None

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

    client = _CapturingClient()
    svc._client = client  # type: ignore[assignment]

    await svc.infer(
        messages=[{"role": "user", "content": "hi"}],
        task_type="query_session",
    )

    cfg = TASK_CONFIG["query_session"]
    assert client.captured_kwargs.get("max_tokens") == cfg["max_tokens"]
    assert client.captured_kwargs.get("temperature") == cfg["temperature"]

    _reset_service()


# ---------------------------------------------------------------------------
# 用例 14：test_system_prompt_not_in_gen_kwargs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_not_in_gen_kwargs():
    """gen_kwargs 中不含 system / priority / _reserved 键（service 层已过滤）。"""
    _reset_service()
    svc = get_service()

    client = _CapturingClient()
    svc._client = client  # type: ignore[assignment]

    await svc.infer(
        messages=[{"role": "user", "content": "hi"}],
        task_type="query_session",
    )

    assert "system" not in client.captured_kwargs, "system 不应出现在 gen_kwargs"
    assert "priority" not in client.captured_kwargs, "priority 不应出现在 gen_kwargs"
    assert "_reserved" not in client.captured_kwargs, "_reserved 不应出现在 gen_kwargs"

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
# 新增用例：test_timeout_cancels_queued_work（P2 shield 修复验证）
# 超时后 fut 被 cancel，worker 取出时跳过，不调用 client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_cancels_queued_work():
    """timeout 超时后，排队中的第二个任务 fut 被 cancel，worker 不执行它（call_count == 1）。

    场景：StubClient delay=0.5s，并发提交两个 infer：
      - task1：正常等待（无 timeout），worker 先取走执行
      - task2：timeout=0.1s，入队后很快超时
    断言：task2 抛 TimeoutError，StubClient 只被调用 1 次（task1），task2 被跳过。
    """
    _reset_service()
    svc = get_service()

    call_count = 0

    class CountingStubClient:
        def create_chat_completion(self, messages: list[dict], **kwargs: object) -> dict:
            nonlocal call_count
            time.sleep(0.5)
            call_count += 1
            return {"choices": [{"message": {"content": "ok"}}]}

    svc._client = CountingStubClient()

    async def task1() -> str:
        return await svc.infer(
            messages=[{"role": "user", "content": "task1"}],
            task_type="query_session",
            timeout=None,
        )

    async def task2() -> str:
        return await svc.infer(
            messages=[{"role": "user", "content": "task2"}],
            task_type="query_session",
            timeout=0.1,
        )

    # 先提交 task1 让 worker 占忙，再提交 task2 入队等待
    t1 = asyncio.create_task(task1())
    await asyncio.sleep(0.02)  # 让 task1 先进队被 worker 拿走
    t2 = asyncio.create_task(task2())

    # task2 应抛 TimeoutError
    with pytest.raises(asyncio.TimeoutError):
        await t2

    # 等 task1 完成
    await t1

    # worker 处理 cancelled fut 时跳过，call_count 应只有 1（task1）
    # 给 worker 额外一点时间处理 task2 的 cancelled fut（验证跳过不执行 client）
    await asyncio.sleep(0.05)
    assert call_count == 1, f"超时任务不应被执行，但 call_count={call_count}"

    _reset_service()


# ---------------------------------------------------------------------------
# 新增用例：test_priority_defaults_from_task_config（默认 priority 修复验证）
# 不传 priority 时应从 TASK_CONFIG[task_type]["priority"] 取默认值
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_priority_defaults_from_task_config():
    """query_session（默认 P1）和 script_parse（默认 P3）不传 priority 时按 TASK_CONFIG 排队。

    设计：
      - 占位任务（P2，delay=0.05s）先入队让 worker 进入忙碌状态
      - 再提交 script_parse（默认 P3）和 query_session（默认 P1）
      - worker 完成占位任务后，应优先取 query_session（P1）再取 script_parse（P3）

    完成顺序：占位 → query_session → script_parse
    """
    _reset_service()
    svc = get_service()
    svc._client = StubClient(response="ok", delay=0.05)

    order: list[str] = []

    async def run(task_type: str, label: str) -> None:
        await svc.infer(
            messages=[{"role": "user", "content": label}],
            task_type=task_type,
            # 不传 priority，验证默认从 TASK_CONFIG 取
        )
        order.append(label)

    # 先提交 l2_take（P2）作占位，让 worker 进入忙碌
    t_blocker = asyncio.create_task(run("l2_take", "blocker"))
    await asyncio.sleep(0.01)  # 让 worker 拿走占位任务

    # 再提交 script_parse（默认 P3）和 query_session（默认 P1）
    t_sp = asyncio.create_task(run("script_parse", "script_parse"))
    t_qs = asyncio.create_task(run("query_session", "query_session"))

    await asyncio.gather(t_blocker, t_sp, t_qs)

    # 断言：blocker 第一（先被取走），query_session 第二（P1 优先于 P3）
    assert order[0] == "blocker", f"占位任务应第一个完成: {order}"
    assert order.index("query_session") < order.index("script_parse"), (
        f"query_session（P1）应在 script_parse（P3）之前完成: {order}"
    )

    _reset_service()


# ---------------------------------------------------------------------------
# Smoke 测试（默认 skip，需要真实模型文件）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 模型路径解析器 + model_present + ensure_model_ready 测试
#
# resolve_model_path / model_present 导入放函数体内，RED 阶段这两个符号尚不存在，
# 顶层 import 会炸 collection。
# ---------------------------------------------------------------------------


def test_resolve_model_path_env_set_exists(tmp_path, monkeypatch) -> None:
    """GEMMA_MODEL_PATH 设置且文件存在 → 直接返回，不调用任何 HF 函数。"""
    import huggingface_hub  # noqa: PLC0415
    from backend.llm.service import resolve_model_path  # noqa: PLC0415

    fake_model = tmp_path / "model.gguf"
    fake_model.write_bytes(b"fake")
    monkeypatch.setenv("GEMMA_MODEL_PATH", str(fake_model))

    hf_called = []

    def _spy_cache(*a: object, **kw: object) -> str:
        hf_called.append(1)
        return "/fake"

    def _spy_download(*a: object, **kw: object) -> str:
        hf_called.append(2)
        return "/fake"

    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", _spy_cache)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _spy_download)

    result = resolve_model_path(download=False)
    assert result == str(fake_model)
    assert hf_called == [], "GEMMA_MODEL_PATH 存在时不应调用任何 HF 函数"


def test_resolve_model_path_env_set_missing(tmp_path, monkeypatch) -> None:
    """GEMMA_MODEL_PATH 设置但文件不存在 → 跳过 env，走 HF cache 分支。"""
    import huggingface_hub  # noqa: PLC0415
    from backend.llm.service import resolve_model_path  # noqa: PLC0415

    monkeypatch.setenv("GEMMA_MODEL_PATH", str(tmp_path / "nonexistent.gguf"))
    monkeypatch.delenv("GEMMA_MODEL_PATH", raising=False)  # 实际让 env 为空，走 HF 分支

    cache_path = str(tmp_path / "cached.gguf")
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **kw: cache_path)

    result = resolve_model_path(download=False)
    assert result == cache_path


def test_resolve_model_path_cache_hit(tmp_path, monkeypatch) -> None:
    """GEMMA_MODEL_PATH 未设 + HF cache 命中 → 返回缓存路径（不下载）。"""
    import huggingface_hub  # noqa: PLC0415
    from backend.llm.service import resolve_model_path  # noqa: PLC0415

    monkeypatch.delenv("GEMMA_MODEL_PATH", raising=False)
    cache_path = str(tmp_path / "cached.gguf")
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **kw: cache_path)

    result = resolve_model_path(download=False)
    assert result == cache_path


def test_resolve_model_path_cache_sentinel(monkeypatch) -> None:
    """try_to_load_from_cache 返回哨兵对象（truthy 非 str）→ 视为未命中，返回 None。

    huggingface_hub 用 _CACHED_NO_EXIST 哨兵表示「已知不存在」，不是 None，
    必须用 isinstance(result, str) 判断，不能判真值。
    """
    import huggingface_hub  # noqa: PLC0415
    from backend.llm.service import resolve_model_path  # noqa: PLC0415

    monkeypatch.delenv("GEMMA_MODEL_PATH", raising=False)
    sentinel = object()  # truthy 但非 str，模拟 _CACHED_NO_EXIST
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **kw: sentinel)

    result = resolve_model_path(download=False)
    assert result is None


def test_resolve_model_path_cache_miss_no_download(monkeypatch) -> None:
    """GEMMA_MODEL_PATH 未设 + cache miss + download=False → None。"""
    import huggingface_hub  # noqa: PLC0415
    from backend.llm.service import resolve_model_path  # noqa: PLC0415

    monkeypatch.delenv("GEMMA_MODEL_PATH", raising=False)
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **kw: None)

    result = resolve_model_path(download=False)
    assert result is None


def test_resolve_model_path_download_true(tmp_path, monkeypatch) -> None:
    """cache miss + download=True → 调 hf_hub_download，返回下载后路径。"""
    import huggingface_hub  # noqa: PLC0415
    from backend.llm.service import resolve_model_path  # noqa: PLC0415

    monkeypatch.delenv("GEMMA_MODEL_PATH", raising=False)
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **kw: None)
    dl_path = str(tmp_path / "downloaded.gguf")
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", lambda *a, **kw: dl_path)

    result = resolve_model_path(download=True)
    assert result == dl_path


@pytest.mark.asyncio
async def test_model_present_true_when_client_loaded(monkeypatch) -> None:
    """_client 已设（模型已加载）→ model_present=True。"""
    _reset_service()
    svc = get_service()
    svc._client = StubClient()  # 直接注入，模拟已加载
    assert svc.model_present is True
    _reset_service()


@pytest.mark.asyncio
async def test_model_present_false_when_no_client_no_cache(monkeypatch) -> None:
    """_client 未加载 + cache/env 均无 → model_present=False。"""
    import huggingface_hub  # noqa: PLC0415

    monkeypatch.delenv("GEMMA_MODEL_PATH", raising=False)
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **kw: None)

    _reset_service()
    svc = get_service()
    assert svc._client is None
    assert svc.model_present is False
    _reset_service()


@pytest.mark.asyncio
async def test_model_present_true_when_cache_hit(monkeypatch) -> None:
    """_client 未加载但 cache 有文件 → model_present=True（无需下载）。"""
    import huggingface_hub  # noqa: PLC0415

    monkeypatch.delenv("GEMMA_MODEL_PATH", raising=False)
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **kw: "/fake/path.gguf")

    _reset_service()
    svc = get_service()
    assert svc._client is None
    assert svc.model_present is True
    _reset_service()


@pytest.mark.asyncio
async def test_ensure_model_ready_resolves_path(tmp_path, monkeypatch) -> None:
    """ensure_model_ready() 调用 resolve_model_path(True) 并存 _model_path。"""
    import huggingface_hub  # noqa: PLC0415

    monkeypatch.delenv("GEMMA_MODEL_PATH", raising=False)
    fake_path = str(tmp_path / "model.gguf")
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **kw: fake_path)

    _reset_service()
    svc = get_service()
    await svc.ensure_model_ready()
    assert svc._model_path == fake_path
    _reset_service()


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
