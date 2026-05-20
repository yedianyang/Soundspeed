"""Audio Input Layer：源抽象与输出数据结构。"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from backend.audio.constants import OUTPUT_SAMPLE_RATE


@dataclass(frozen=True)
class AudioConfig:
    """源无关的行为配置，所有 AudioSource 子类共用一份。"""

    chunk_ms: int = 200
    max_channels: int = 2


@dataclass(frozen=True)
class AudioChunk:
    """一个时间切片，横跨所有已处理声道。

    channels[i] 是第 i 路独立的单声道 16kHz int16 数组。各声道不交织、
    不混音 —— 它们是分开的信号，打包在一起只为共享 seq / start_frame
    供下游做时间对齐。
    """

    seq: int
    channels: list[np.ndarray]
    n_frames: int
    start_frame: int
    sample_rate: int = OUTPUT_SAMPLE_RATE
