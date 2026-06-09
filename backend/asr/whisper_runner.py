"""WhisperRunner：一段 16kHz int16 PCM → 文字（whisper.cpp via pywhispercpp）。

模型懒加载；可注入 transcriber（测试用假实例，生产用 pywhispercpp Model）。
段级用法：上游 VAD 收尾一个 SpeechSegment → 调一次 transcribe_pcm → 出一条文字。
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

from backend.asr.config import ASRConfig

logger = logging.getLogger(__name__)

_INT16_FULL_SCALE = 32768.0


def validate_param_keys(params: dict[str, Any]) -> dict[str, Any]:
    """校验调参 key 都是 pywhispercpp 已知参数，否则报错。

    transcribe(**params) 对未知 kwarg 是静默吞掉的——打错名（如 beem_size）不会报错，
    解码照样走默认，绿测试还以为生效了。在交给模型前对着 PARAMS_SCHEMA 校验，把这种
    错误从「悄悄退化」变成「当场炸」。
    """
    from pywhispercpp.constants import PARAMS_SCHEMA  # 延迟导入，无 pywhispercpp 环境不阻塞

    unknown = set(params) - set(PARAMS_SCHEMA)
    if unknown:
        raise ValueError(f"未知的 whisper 调参（会被静默吞掉）: {sorted(unknown)}")
    return params


def build_transcribe_params(config: ASRConfig) -> dict[str, Any]:
    """把 ASRConfig 的解码调参拼成 pywhispercpp transcribe(**params) 的 kwargs。

    beam_search 在 pywhispercpp 1.5.0 是嵌套 dict（不是裸 int）；beam_size<=1 时
    whisper.cpp 自动退化贪心。initial_prompt 为 None 时不传，避免无谓偏置解码。
    whisper 内置 vad 刻意不传——上游已切段，开它会双重 VAD 咬字。
    """
    params: dict[str, Any] = {
        "beam_search": {"beam_size": config.beam_size, "patience": -1.0},
        "temperature": config.temperature,
        "temperature_inc": config.temperature_inc,
        "entropy_thold": config.entropy_thold,
        "logprob_thold": config.logprob_thold,
        "no_speech_thold": config.no_speech_thold,
    }
    if config.initial_prompt is not None:
        params["initial_prompt"] = config.initial_prompt
    return validate_param_keys(params)


class WhisperRunner:
    """封装 pywhispercpp，对外只暴露 transcribe_pcm(int16 16k) -> str。"""

    def __init__(self, config: ASRConfig, *, model: Any = None) -> None:
        self._config = config
        self._model = model  # None 时首次 transcribe 懒加载真实模型
        self._language = config.language  # 可运行时切换（set_language）
        self._params: dict[str, Any] | None = None  # 解码调参，首次 transcribe 懒构建并缓存

    @property
    def language(self) -> str:
        return self._language

    @property
    def model_size(self) -> str:
        return self._config.model_size

    def set_language(self, language: str) -> None:
        """运行时切换转录语言（zh/en/auto/…）。下一段转录生效，无需重载模型。"""
        self._language = language

    def _ensure_model(self) -> Any:
        if self._model is None:
            from pywhispercpp.model import Model  # 延迟导入，避免无 pywhispercpp 环境 import 失败

            logger.info(
                "加载 whisper.cpp 模型 size=%s models_dir=%s",
                self._config.model_size,
                self._config.models_dir or "(pywhispercpp 默认)",
            )
            self._model = Model(
                self._config.model_size,
                models_dir=self._config.models_dir,
                n_threads=self._config.n_threads,
                print_realtime=False,
                print_progress=False,
            )
        return self._model

    def warmup(self) -> None:
        """启动时预加载模型（首次含下载，缓存后秒级）。"""
        self._ensure_model()

    def transcribe_pcm(self, pcm_int16: np.ndarray) -> str:
        """16kHz 单声道 int16 → 文字。whisper.cpp 吃 float32 [-1,1]。"""
        model = self._ensure_model()
        if self._params is None:
            self._params = build_transcribe_params(self._config)
        audio_f32 = np.ascontiguousarray(pcm_int16, dtype=np.int16).astype(np.float32) / _INT16_FULL_SCALE
        segments = model.transcribe(audio_f32, language=self._language, **self._params)
        return "".join(seg.text for seg in segments).strip()
