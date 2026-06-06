"""底层模型加载与 client 抽象。

LLMClient 协议：定义 service 层依赖的接口契约。
GemmaClient：llama-cpp-python 封装（生产用）。
StubClient：确定性 stub（测试用）。

设计参考：llm-service-design v1.1 §底层模型加载 + 1.F 实施 spec §6。
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from backend.llm.errors import ModelUnavailableError

if TYPE_CHECKING:
    from backend.llm.multimodal import MultimodalGemma4Handler

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
        """同步执行 chat completion，返回 OpenAI 风格 dict。

        多模态音频路径用 audio kwarg（GemmaClient 显式声明该形参，经 **kwargs 兼容本协议）：
        非 None 时 messages 含音频哨兵、client 把字节喂给 handler；纯文本 client 收到 audio 报错。
        """
        ...


class GemmaClient:
    """llama-cpp-python 封装，实现 LLMClient 协议。

    加载参数来自 0.C spike 选型（llm-backend-selection v0.4 §11.3）：
      n_ctx=8192, n_gpu_layers=-1（全 Metal 卸载）, seed=42, verbose=False。
    model_path 从环境变量 GEMMA_MODEL_PATH 读取，
    默认 models/gemma-4-E4B-it-Q4_K_M.gguf。

    不暴露内部 Llama 对象，调用方只能通过 create_chat_completion 接口，
    保证后续替换为 Ollama / vLLM 时只改 client.py。

    ⚠️ 并发不安全：多模态实例下 create_chat_completion 对 text+tools 请求临时换/还原
    `self._llm.chat_handler`（换原生 FunctionGemma formatter），并发调用会串 handler。
    依赖外层串行——生产恒经 LLMService 的 `_lock`（service.py:_worker），勿绕过 service 并发直调。
    """

    def __init__(
        self,
        model_path: str | None = None,
        mmproj_path: str | None = None,
        **llama_kwargs: object,
    ) -> None:
        from llama_cpp import Llama  # type: ignore[import]

        resolved_path = model_path or os.environ.get("GEMMA_MODEL_PATH", _DEFAULT_MODEL_PATH)
        params = {**_LLAMA_DEFAULTS, **llama_kwargs}
        # 显存吃紧时用 SOUNDSPEED_LLM_GPU_LAYERS 覆盖：0=纯 CPU（释放显存给 whisper/pyannote，
        # L2 非实时可接受）；-1=全 GPU。8GB 卡上三模型同跑会 OOM，本机建议设 0。
        gpu_env = os.environ.get("SOUNDSPEED_LLM_GPU_LAYERS")
        if gpu_env is not None and "n_gpu_layers" not in llama_kwargs:
            try:
                params["n_gpu_layers"] = int(gpu_env)
            except ValueError:
                pass

        # mmproj_path 给定 → 升级为多模态单实例（text/audio/image 三入口共用，方案 A，spec §5.1）。
        # 文本（L2 + 文本 NP）也借道这一份 handler，保证文本输入统一格式化。
        self._handler: MultimodalGemma4Handler | None = None
        if mmproj_path is not None:
            from backend.llm.multimodal import MultimodalGemma4Handler  # noqa: PLC0415

            self._handler = MultimodalGemma4Handler(
                clip_model_path=mmproj_path, verbose=bool(params.get("verbose"))
            )
            params["chat_handler"] = self._handler
            # vision-ready：gemma4 vision non-causal 要 1120 image token 落单 ubatch，
            # 故 n_batch=n_ubatch=2048。audio/text 用不上但无害（caller 显式传则尊重）。
            params.setdefault("n_batch", 2048)
            params.setdefault("n_ubatch", 2048)

        # self._llm 标 Any：llama_cpp 是底层封装边界，不让其精确返回类型（CreateChatCompletion
        # Response | Iterator）泄漏到本层（否则 result: dict 赋值处处需 cast/ignore）。
        self._llm: Any = Llama(model_path=resolved_path, **params)  # type: ignore[arg-type]

        # 多模态 handler 的 CHAT_FORMAT（继承 Gemma4ChatHandler）**不渲染工具声明**（无 FunctionGemma
        # <|tool> 宏），带 tools 的请求模型看不到工具 → auto 路径不调工具、forced 路径靠 grammar 兜。
        # 为「文本 + tools」请求另建 GGUF 内嵌的原生 FunctionGemma Jinja formatter（会渲染 tools），
        # create_chat_completion 按需临时换上（_lock 串行下无竞态）。音频/图像仍走多模态 handler。
        # 纯文本 GemmaClient（无 mmproj）的 Llama 本就用内嵌模板渲染 tools，无需此 handler，留 None。
        self._text_tool_handler: Any = None
        if self._handler is not None:
            self._text_tool_handler = self._build_native_tool_handler()

    def _build_native_tool_handler(self) -> Any:
        """从 GGUF 内嵌 chat_template 建原生 FunctionGemma Jinja formatter（渲染 tools 声明）。

        多模态 handler 装上后 Llama 用其 CHAT_FORMAT（不含工具宏），故文本 + tools 请求需换回此
        原生 formatter。无内嵌模板（理论不该发生）返回 None，create_chat_completion 退回原 handler。
        """
        from llama_cpp.llama_chat_format import Jinja2ChatFormatter  # noqa: PLC0415

        # 整体兜底：取不到内嵌模板 / token（如测试用 FakeLlama，或非 gemma 模型）→ 返回 None，
        # create_chat_completion 退回原 handler（多模态/默认），不因此构造失败。
        try:
            template = self._llm.metadata.get("tokenizer.chat_template")
            if not template:
                return None

            # bos/eos 取自模型自身 token。必须 special=True：默认 detokenize 剥特殊 token 会返回空串，
            # 导致 Jinja 模板不 prepend <bos> → prompt 坏 → 模型立即吐 EOS（空输出）。空则兜底字面量。
            def _tok_text(token_id: int) -> str:
                try:
                    return self._llm.detokenize([token_id], special=True).decode("utf-8", "ignore")
                except Exception:  # noqa: BLE001
                    return ""

            bos = _tok_text(self._llm.token_bos()) or "<bos>"
            eos = _tok_text(self._llm.token_eos()) or "<eos>"
            return Jinja2ChatFormatter(
                template=template, bos_token=bos, eos_token=eos
            ).to_chat_handler()
        except Exception:  # noqa: BLE001
            return None

    def create_chat_completion(
        self,
        messages: list[dict],
        **kwargs: object,
    ) -> dict:
        # 音频路径：audio 经 kwargs 传入（保持签名与 LLMClient 协议一致，不破坏 StubClient 等的结构匹配）。
        audio = kwargs.pop("audio", None)
        if audio is not None:
            if self._handler is None:
                raise ModelUnavailableError("纯文本 GemmaClient 不支持音频推理（未挂多模态 handler）")
            if not isinstance(audio, (bytes, bytearray)):
                raise TypeError(f"audio 必须是 bytes，收到 {type(audio).__name__}")
            # 音频必须走多模态 handler（mtmd 上下文）；其 CHAT_FORMAT 不渲染 tools 但 voice NP
            # 走 forced tool_choice，grammar 兜工具，无碍。_lock 串行下设音频，推理后必复位避免串号。
            self._handler.set_pending_audio(bytes(audio))
            try:
                result: dict = self._llm.create_chat_completion(messages=messages, **kwargs)
            finally:
                self._handler.set_pending_audio(None)
            return result
        # 文本 + tools：多模态 handler 不渲染工具声明 → 临时换 GGUF 原生 FunctionGemma formatter。
        # _lock（LLMService 层）串行化所有 client 调用，故 chat_handler 的临时换/还原无竞态。
        # ⚠️ 3.x vision：未来 image content + tools 的请求不带 audio= kwarg，会落进本分支换原生
        #   formatter——但原生 formatter 不处理图像嵌入，须在此判据补 content-type 守卫（届时）。
        if self._text_tool_handler is not None and kwargs.get("tools"):
            prev = self._llm.chat_handler
            self._llm.chat_handler = self._text_tool_handler
            try:
                return self._llm.create_chat_completion(messages=messages, **kwargs)
            finally:
                self._llm.chat_handler = prev
        return self._llm.create_chat_completion(messages=messages, **kwargs)


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
