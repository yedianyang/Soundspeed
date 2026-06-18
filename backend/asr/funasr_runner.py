"""FunAsrRunner:一段 16kHz int16 PCM → 文字(FunASR paraformer-zh)。

与 WhisperRunner 同形鸭子接口(transcribe_pcm / set_language / language / model_size /
warmup),LiveAsrSession 可无差别持有。funasr 懒 import:未安装(uv sync 跳过 funasr 组)
时抛 FunAsrNotInstalled,API 层转「FunASR 未安装」。
不挂内置 vad/punc/spk —— 上游 silero 已切段,双重 VAD 咬字(见 2026-06-10 决策文档)。
"""
from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_INT16_FULL_SCALE = 32768.0
_MODEL_NAME = "paraformer-zh"

# CJK 统一表意 + CJK 标点 + 全角形;paraformer 输出按字带空格("你 好"),
# 仅去两侧都是 CJK 的空格,保留英文词间空格("我用 iPhone 打电话")。
_CJK = r"[㐀-鿿豈-﫿　-〿＀-￯]"
_CJK_GAP = re.compile(rf"(?<={_CJK})\s+(?={_CJK})")


class FunAsrNotInstalled(RuntimeError):
    """funasr 包不可用(未随 uv sync 安装)。"""


def select_funasr_device() -> str:
    """FunASR 推理设备:Apple Silicon 上用 MPS,否则 CPU。

    FunASR AutoModel 默认 device="cuda",Mac 上回退 CPU(不会自动用 MPS),paraformer
    落 CPU 慢约 10x(实测 1.5s/段 → MPS 0.15s/段,输出逐字一致;流式更甚 RTF 2.4→0.36)。
    torch 是 funasr 的依赖,import 放函数体内,保持本模块顶层无 torch 依赖。
    """
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001 - 无 torch / 无 MPS 一律回退 CPU
        pass
    return "cpu"


def normalize_funasr_text(text: str) -> str:
    """去除 CJK 字符间空格(paraformer 按字分词),保留英文词间空格。"""
    return _CJK_GAP.sub("", text).strip()


class FunAsrRunner:
    """封装 funasr AutoModel,对外只暴露 transcribe_pcm(int16 16k) -> str。"""

    def __init__(self, *, model: Any = None) -> None:
        self._model = model  # None 时首次使用懒加载(注入假实例供测试)

    @property
    def language(self) -> str:
        return "zh"

    @property
    def model_size(self) -> str:
        return _MODEL_NAME

    def set_language(self, language: str) -> None:
        """paraformer-zh 仅中文;非 zh 忽略并告警(接口形状与 WhisperRunner 对齐)。"""
        if language != "zh":
            logger.warning("FunASR paraformer-zh 仅支持中文,忽略语言切换 %s", language)

    def _ensure_model(self) -> Any:
        if self._model is None:
            try:
                from funasr import AutoModel  # 懒 import:未装 funasr 不阻塞模块加载
            except ImportError as e:
                raise FunAsrNotInstalled("FunASR 未安装") from e
            device = select_funasr_device()
            logger.info("加载 FunASR 模型 %s on %s(首次从 modelscope 下载 ~1GB)",
                        _MODEL_NAME, device)
            # 不挂 vad/punc/spk:上游 silero 已切段
            self._model = AutoModel(model=_MODEL_NAME, disable_update=True, device=device)
        return self._model

    def warmup(self) -> None:
        """切换引擎时预加载模型,避免首段转录卡管线。"""
        self._ensure_model()

    def transcribe_pcm(self, pcm_int16: np.ndarray) -> str:
        """16kHz 单声道 int16 → 文字。paraformer 吃 float32 [-1,1] 直喂。"""
        model = self._ensure_model()
        audio_f32 = (
            np.ascontiguousarray(pcm_int16, dtype=np.int16).astype(np.float32)
            / _INT16_FULL_SCALE
        )
        result = model.generate(input=audio_f32)
        if not result:
            return ""
        return normalize_funasr_text(str(result[0].get("text", "")))
