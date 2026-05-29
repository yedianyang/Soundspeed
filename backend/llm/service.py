"""LLMService：单例 + asyncio.PriorityQueue + asyncio.Lock + worker task。

设计依据：
  llm-service-design v1.1 §决策 1-3
  1.F 实施 spec §3-§4

公共 API：
  get_service() -> LLMService      工厂函数，返回模块级单例
  _reset_service() -> None          仅供测试使用，清空单例
  LLMService.infer(...)             统一推理入口
  LLMService.aclose()               关闭 worker，释放资源
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.llm.config import TASK_CONFIG

if TYPE_CHECKING:
    from backend.llm.client import LLMClient

# gen_kwargs 中过滤掉的元字段（不传给 client）
_META_KEYS = frozenset({"priority", "_reserved", "system"})


@dataclass
class _InferPayload:
    """worker 内部处理的推理负载。"""

    messages: list[dict]
    task_type: str
    gen_kwargs: dict


class LLMService:
    """LLM 推理单例。

    不直接实例化，通过 get_service() 获取。

    内部结构：
    - _queue: asyncio.PriorityQueue，元素 (priority, counter, fut, payload)
    - _lock: asyncio.Lock，串行化 client 调用
    - _worker_task: 长运行 asyncio.Task，从队列取任务逐个推理
    - _client: LLMClient 实例，首次 infer 时 lazy 初始化
    - _counter: itertools.count()，保证相同 priority 下 FIFO
    """

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue[
            tuple[int, int, asyncio.Future[str], _InferPayload]
        ] = asyncio.PriorityQueue()
        self._lock: asyncio.Lock = asyncio.Lock()
        self._worker_task: asyncio.Task[None] | None = None
        self._client: LLMClient | None = None
        self._counter = itertools.count()

    @property
    def model_loaded(self) -> bool:
        """首次 infer 前（_ensure_client 未调用）为 False，加载后为 True。

        供 Orchestrator 区分 loading（首次权重加载）vs running（后续推理）。
        """
        return self._client is not None

    def _ensure_worker(self) -> None:
        """Lazy 启动 worker task，只启动一次。

        asyncio 事件循环单线程语义保证此处无竞态：
        同一 event loop 中 infer 协程是顺序执行的，不会出现两个协程
        同时通过 self._worker_task is None 判断的情况。
        """
        if self._worker_task is None:
            self._worker_task = asyncio.get_running_loop().create_task(self._worker())

    def _ensure_client(self) -> LLMClient:
        """Lazy 初始化 client（首次推理时加载模型）。"""
        if self._client is None:
            from backend.llm.client import GemmaClient

            self._client = GemmaClient()
        return self._client

    async def infer(
        self,
        messages: list[dict],
        task_type: str,
        priority: int | None = None,
        timeout: float | None = 30.0,
    ) -> str:
        """统一推理入口。

        Args:
            messages: 标准 chat 消息列表，每个元素有 "role" 和 "content" 键。
            task_type: TASK_CONFIG 中的合法 key。
            priority: 1=用户态, 2=普通, 3=批处理。None 时从 TASK_CONFIG[task_type]["priority"] 取默认值。
                      超出范围抛 ValueError。
            timeout: 最大等待时间（含排队 + 推理）秒数，None 表示不超时。
                     传 0 或负数抛 ValueError。

        Returns:
            LLM 生成的文本字符串（choices[0]["message"]["content"]）。

        Raises:
            ValueError: task_type 不在 TASK_CONFIG，或 priority/timeout 非法。
            NotImplementedError: task_type 标 _reserved=True（当前为 agent_init）。
            asyncio.TimeoutError: 排队 + 推理总耗时超 timeout。
            RuntimeError: client.create_chat_completion 内部崩溃时通过 Future 回传。
            LookupError: client 返回 dict 缺少 choices[0]["message"]["content"]。
        """
        # 入参校验（不合法立即抛，不入队）
        if task_type not in TASK_CONFIG:
            raise ValueError(f"未知的 task_type: {task_type!r}，合法值: {list(TASK_CONFIG)}")
        cfg = TASK_CONFIG[task_type]
        if cfg.get("_reserved"):
            raise NotImplementedError(
                f"task_type={task_type!r} 标记为 _reserved=True，MVP 阶段不可用"
            )
        # priority 为 None 时从 TASK_CONFIG 取默认值
        if priority is None:
            priority = cfg["priority"]
        if priority not in (1, 2, 3):
            raise ValueError(f"priority 必须为 1/2/3，收到: {priority!r}")
        if timeout is not None and timeout <= 0:
            raise ValueError(f"timeout 必须为正数或 None，收到: {timeout!r}")

        # 从 TASK_CONFIG 提取 gen_kwargs，过滤掉元字段
        gen_kwargs = {k: v for k, v in cfg.items() if k not in _META_KEYS}

        # 入队
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        payload = _InferPayload(messages=messages, task_type=task_type, gen_kwargs=gen_kwargs)
        counter = next(self._counter)
        await self._queue.put((priority, counter, fut, payload))

        # 确保 worker 已启动（lazy）
        self._ensure_worker()

        # 等待结果，支持超时。
        # 不用 shield：超时后 wait_for 自动 cancel(fut)，
        # worker 下一轮取出时 fut.cancelled() 为 True，直接跳过，节省推理资源。
        return await asyncio.wait_for(fut, timeout=timeout)

    async def aclose(self) -> None:
        """关闭 worker task，清空队列中未处理的 Future。

        若 worker 从未启动（lazy init 且 infer 从未被调用），直接返回（no-op）。
        FastAPI lifespan 负责在应用关闭时调用此方法。
        """
        if self._worker_task is None:
            return

        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass

        # 清空队列中未处理的 Future
        while not self._queue.empty():
            try:
                _, _, fut, _ = self._queue.get_nowait()
                if not fut.done():
                    fut.set_exception(asyncio.CancelledError())
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

        self._worker_task = None

    async def _worker(self) -> None:
        """长运行 worker：串行从队列取任务，逐个推理。

        单线程 asyncio 语义保证无竞态；只启动一次（见 _ensure_worker）。
        worker 异常处理：捕获所有异常并通过 fut.set_exception 回传，
        worker 本身不退出。只有 asyncio.CancelledError 允许穿透（触发 task 退出）。
        """
        while True:
            priority, counter, fut, payload = await self._queue.get()
            # 超时后 fut 被 cancel，跳过此任务
            if fut.cancelled():
                self._queue.task_done()
                continue
            try:
                client = self._ensure_client()
                async with self._lock:
                    result_dict = await asyncio.to_thread(
                        client.create_chat_completion,
                        messages=payload.messages,
                        **payload.gen_kwargs,
                    )
                try:
                    text: str = result_dict["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError) as e:
                    raise LookupError(
                        f"client 返回 dict 格式异常，缺少 choices[0]['message']['content']: {e}"
                    ) from e
                if not fut.done():
                    fut.set_result(text)
            except asyncio.CancelledError:
                # worker 被取消，清理后退出
                if not fut.done():
                    fut.set_exception(asyncio.CancelledError())
                raise
            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)
            finally:
                self._queue.task_done()


# 模块级单例
_service: LLMService | None = None


def get_service() -> LLMService:
    """返回 LLMService 单例，首次调用时创建实例。

    不触发模型加载（lazy init，首次 infer 时才初始化 client 和 worker）。
    """
    global _service
    if _service is None:
        _service = LLMService()
    return _service


def _reset_service() -> None:
    """仅供测试使用，清空模块级单例，避免测试间单例污染。

    生产代码不调用此函数。
    """
    global _service
    _service = None
