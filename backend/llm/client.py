"""底层模型加载与 client 抽象。

LLMClient 协议：定义 service 层依赖的接口契约。
GemmaClient：llama-cpp-python 封装（生产用）。
StubClient：确定性 stub（测试用）。

设计参考：llm-service-design v1.1 §底层模型加载 + 1.F 实施 spec §6。
"""

from __future__ import annotations

import os
import time
from typing import Protocol, runtime_checkable

# 默认模型路径，由环境变量覆盖
_DEFAULT_MODEL_PATH = "models/gemma-4-E4B-it-Q4_K_M.gguf"

# llama-cpp-python 加载参数（来自 0.C spike 选型，llm-backend-selection v0.4 §11.3）
# n_ctx=8192：从 v0.3 的 4096 升一倍，支持长 take 场景（剧本 100+ 行 / 5+ 分钟转录），
# Q4_K_M @ 8192 实测 RSS ~6.5 GB（M1 Max 16 GB 余量充足，见 llm-backend-selection §3.3）。
_LLAMA_DEFAULTS: dict[str, object] = {
    "n_ctx": 8192,
    "n_gpu_layers": -1,  # 全卸载到 Metal（macOS）
    "seed": 42,
    "verbose": False,
}


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

    加载参数来自 0.C spike 选型（llm-backend-selection v0.4 §11.3）：
      n_ctx=8192, n_gpu_layers=-1（全 Metal 卸载）, seed=42, verbose=False。
    model_path 从环境变量 GEMMA_MODEL_PATH 读取，
    默认 models/gemma-4-E4B-it-Q4_K_M.gguf。

    不暴露内部 Llama 对象，调用方只能通过 create_chat_completion 接口，
    保证后续替换为 Ollama / vLLM 时只改 client.py。
    """

    def __init__(self, model_path: str | None = None, **llama_kwargs: object) -> None:
        from llama_cpp import Llama  # type: ignore[import]

        resolved_path = model_path or os.environ.get("GEMMA_MODEL_PATH", _DEFAULT_MODEL_PATH)
        params = {**_LLAMA_DEFAULTS, **llama_kwargs}
        self._llm = Llama(model_path=resolved_path, **params)  # type: ignore[arg-type]

    def create_chat_completion(
        self,
        messages: list[dict],
        **kwargs: object,
    ) -> dict:
        result: dict = self._llm.create_chat_completion(messages=messages, **kwargs)  # type: ignore[arg-type]
        return result


class StubClient:
    """确定性 stub，不加载模型，供单元测试 fixture 使用。

    delay > 0 时调用 time.sleep 模拟同步阻塞推理耗时，
    service 层通过 asyncio.to_thread 包裹使其不阻塞事件循环。
    """

    def __init__(self, response: str = "stub response", delay: float = 0.0) -> None:
        self._response = response
        self._delay = delay

    def create_chat_completion(
        self,
        messages: list[dict],
        **kwargs: object,
    ) -> dict:
        if self._delay:
            time.sleep(self._delay)
        return {"choices": [{"message": {"content": self._response}}]}
