"""底层模型加载与 client 抽象。

LLMClient 协议：定义 service 层依赖的接口契约。
GemmaClient：llama-cpp-python 封装（生产用）。
StubClient：确定性 stub（测试用）。

设计参考：llm-service-design v1.1 §底层模型加载 + 1.F 实施 spec §6。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """service 层依赖的 client 协议。

    约定：返回 OpenAI 风格 dict，choices[0]["message"]["content"] 为生成文本。
    """

    def create_chat_completion(
        self,
        messages: list[dict],
        **kwargs: object,
    ) -> dict:
        """同步执行 chat completion，返回 OpenAI 风格 dict。"""
        ...


class GemmaClient:
    """llama-cpp-python 封装，实现 LLMClient 协议。

    加载参数来自 0.C spike 选型（llm-backend-selection v0.3 §11.3）：
      n_ctx=4096, n_gpu_layers=-1（全 Metal 卸载）, seed=42, verbose=False。
    model_path 从环境变量 GEMMA_MODEL_PATH 读取，
    默认 models/gemma-4-E4B-it-Q4_K_M.gguf。
    """

    def __init__(self, model_path: str, **llama_kwargs: object) -> None:
        raise NotImplementedError

    def create_chat_completion(
        self,
        messages: list[dict],
        **kwargs: object,
    ) -> dict:
        raise NotImplementedError


class StubClient:
    """确定性 stub，不加载模型，供单元测试 fixture 使用。

    delay > 0 时调用 time.sleep 模拟同步阻塞推理耗时，
    service 层通过 asyncio.to_thread 包裹使其不阻塞事件循环。
    """

    def __init__(self, response: str = "stub response", delay: float = 0.0) -> None:
        raise NotImplementedError

    def create_chat_completion(
        self,
        messages: list[dict],
        **kwargs: object,
    ) -> dict:
        raise NotImplementedError
