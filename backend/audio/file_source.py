"""FileSource：音频文件作为 AudioSource（尽快批处理）。"""
from __future__ import annotations

import numpy as np
import soundfile as sf

from backend.audio.source import AudioConfig, AudioSource


class FileSource(AudioSource):
    """从音频文件读取。soundfile 支持 WAV / BWF / AIFF / FLAC 等。"""

    def __init__(self, path: str, config: AudioConfig) -> None:
        super().__init__(config)
        self._path = path
        self._sf: sf.SoundFile | None = None
        self._block_frames = 0

    def _open(self) -> tuple[int, int]:
        self._sf = sf.SoundFile(self._path)
        self._block_frames = self._sf.samplerate * self._config.chunk_ms // 1000
        return self._sf.samplerate, self._sf.channels

    def _read_raw_block(self) -> np.ndarray | None:
        assert self._sf is not None
        block = self._sf.read(self._block_frames, dtype="float32", always_2d=True)
        if len(block) == 0:
            return None
        return block

    def _close(self) -> None:
        if self._sf is not None:
            self._sf.close()
            self._sf = None
