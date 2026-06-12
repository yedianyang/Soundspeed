"""FunAsrOnlineRunner:paraformer-zh-streaming 流式增量转录(2pass 的 online 半边)。

逐 turn 生命周期:start_turn() 重置 cache/缓冲/累计文本 → feed(int16) 按 600ms 凑块
推理、本次有新文本时返回累计全文 → end_turn() 丢弃状态(不调 is_final 冲尾 ——
尾巴由离线 final 全段重解码覆盖,冲尾为最后一条 partial 多付一次推理,纯浪费)。
不挂内置 vad/punc/spk(上游 silero 已切段)。
spec: docs/specs/2026-06-11-funasr-2pass-streaming.md §3 Q4
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

from backend.asr.funasr_runner import FunAsrNotInstalled, normalize_funasr_text

logger = logging.getLogger(__name__)

_INT16_FULL_SCALE = 32768.0
_MODEL_NAME = "paraformer-zh-streaming"
# FunASR 官方推荐值,不开旋钮:10*60ms=600ms 步长 + 5 块 lookahead;encoder/decoder 回看 4/1
_CHUNK_SIZE = [0, 10, 5]
_ENCODER_LOOK_BACK = 4
_DECODER_LOOK_BACK = 1
BLOCK_SAMPLES = _CHUNK_SIZE[1] * 960  # 600ms @ 16k = 9600 样本(spec 常量,稳定接口)


class FunAsrOnlineRunner:
    """封装流式 AutoModel:600ms 凑块、per-turn cache、增量文本累计。"""

    def __init__(self, *, model: Any = None) -> None:
        self._model = model  # None 时懒加载(注入假实例供测试)
        self._cache: dict = {}
        self._buffer = np.empty(0, dtype=np.int16)
        self._acc_text = ""

    def _ensure_model(self) -> Any:
        if self._model is None:
            try:
                from funasr import AutoModel  # 懒 import:未装 funasr 不阻塞模块加载
            except ImportError as e:
                raise FunAsrNotInstalled("FunASR 未安装") from e
            logger.info("加载 FunASR 流式模型 %s(首次从 modelscope 下载 ~860MB)", _MODEL_NAME)
            self._model = AutoModel(model=_MODEL_NAME, disable_update=True)
        return self._model

    def warmup(self) -> None:
        """加载模型 + 600ms 零样本预热(独立 cache,即弃),首段卡顿吃在引擎切换期。"""
        model = self._ensure_model()
        silence = np.zeros(BLOCK_SAMPLES, dtype=np.float32)
        model.generate(
            input=silence, cache={}, is_final=True,
            chunk_size=_CHUNK_SIZE,
            encoder_chunk_look_back=_ENCODER_LOOK_BACK,
            decoder_chunk_look_back=_DECODER_LOOK_BACK,
        )

    def start_turn(self) -> None:
        """新 VAD turn:cache 严格 per-turn(跨 turn 复用会把上一句解码状态咬进下一句)。"""
        self._cache = {}
        self._buffer = np.empty(0, dtype=np.int16)
        self._acc_text = ""

    def end_turn(self) -> None:
        """丢弃 turn 状态。不调 is_final 冲尾(spec §3 Q4)。"""
        self.start_turn()

    def feed(self, pcm_int16: np.ndarray) -> str | None:
        """喂一块 16k int16;每凑满 600ms 推理一次。本次有新文本则返回累计全文,否则 None。"""
        model = self._ensure_model()
        pcm = np.ascontiguousarray(pcm_int16, dtype=np.int16).reshape(-1)
        # 首存必须 copy:ascontiguousarray 对已连续输入返回原数组,直接持有会别名上游 buffer
        self._buffer = np.concatenate([self._buffer, pcm]) if len(self._buffer) else pcm.copy()
        got_new = False
        while len(self._buffer) >= BLOCK_SAMPLES:
            block = self._buffer[:BLOCK_SAMPLES].astype(np.float32) / _INT16_FULL_SCALE
            self._buffer = self._buffer[BLOCK_SAMPLES:].copy()
            res = model.generate(
                input=block, cache=self._cache, is_final=False,
                chunk_size=_CHUNK_SIZE,
                encoder_chunk_look_back=_ENCODER_LOOK_BACK,
                decoder_chunk_look_back=_DECODER_LOOK_BACK,
            )
            piece = str(res[0].get("text", "")) if res else ""
            if piece:
                # _acc_text 存原始增量;normalize 仅在返回时做(逐块 normalize 会吞英文词间空格)
                self._acc_text += piece
                got_new = True
        if got_new:
            return normalize_funasr_text(self._acc_text)
        return None
