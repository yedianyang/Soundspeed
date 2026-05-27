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

from dataclasses import dataclass


@dataclass
class _InferPayload:
    """worker 内部处理的推理负载。"""

    messages: list[dict]
    task_type: str
    gen_kwargs: dict


class LLMService:
    """LLM 推理单例。

    不直接实例化，通过 get_service() 获取。
    """

    def __init__(self) -> None:
        raise NotImplementedError

    async def infer(
        self,
        messages: list[dict],
        task_type: str,
        priority: int = 2,
        timeout: float | None = 30.0,
    ) -> str:
        """统一推理入口。

        Args:
            messages: 标准 chat 消息列表，每个元素有 "role" 和 "content" 键。
            task_type: TASK_CONFIG 中的合法 key。
            priority: 1=用户态, 2=普通, 3=批处理。超出范围抛 ValueError。
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
        raise NotImplementedError

    async def aclose(self) -> None:
        """关闭 worker task，清空队列中未处理的 Future。

        若 worker 从未启动（首次 infer 未调用过），直接返回（no-op）。
        FastAPI lifespan 负责在应用关闭时调用此方法。
        """
        raise NotImplementedError

    async def _worker(self) -> None:
        """长运行 worker：串行从队列取任务，逐个推理。

        单线程 asyncio 语义保证无竞态；只启动一次（见 infer 入口检查）。
        """
        raise NotImplementedError


# 模块级单例
_service: LLMService | None = None


def get_service() -> LLMService:
    """返回 LLMService 单例，首次调用时创建实例。

    不触发模型加载（lazy init，首次 infer 时才初始化 client 和 worker）。
    """
    raise NotImplementedError


def _reset_service() -> None:
    """仅供测试使用，清空模块级单例，避免测试间单例污染。

    生产代码不调用此函数。
    """
    raise NotImplementedError
