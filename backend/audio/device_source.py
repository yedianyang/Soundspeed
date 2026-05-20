"""DeviceSource：实时采集设备作为 AudioSource。"""
from __future__ import annotations

import logging

import numpy as np
import sounddevice as sd

from backend.audio.source import AudioConfig, AudioSource

logger = logging.getLogger(__name__)


class DeviceError(RuntimeError):
    """采集设备无法打开、或采集中途失败时抛出。"""


class DeviceSource(AudioSource):
    """从声卡 / 虚拟声卡 / USB 接口 / 录音机 line-out 实时采集。

    用 sounddevice 阻塞式 read —— 阻塞本身就是实时节奏与拉取语义，
    无后台线程、无队列。
    """

    def __init__(self, device: str | int, config: AudioConfig) -> None:
        super().__init__(config)
        self._device = device
        self._stream: sd.InputStream | None = None
        self._block_frames = 0
        self._overflow_count = 0

    def _open(self) -> tuple[int, int]:
        try:
            info = sd.query_devices(self._device, "input")
        except (ValueError, sd.PortAudioError) as exc:
            available = "\n".join(
                f"  [{i}] {d['name']}"
                for i, d in enumerate(sd.query_devices())
                if d["max_input_channels"] > 0
            )
            raise DeviceError(
                f"找不到输入设备 {self._device!r}。可用输入设备：\n{available}"
            ) from exc
        rate = int(info["default_samplerate"])
        channels = int(info["max_input_channels"])
        self._block_frames = rate * self._config.chunk_ms // 1000
        try:
            self._stream = sd.InputStream(
                device=self._device, samplerate=rate,
                channels=channels, dtype="float32",
            )
            self._stream.start()
        except sd.PortAudioError as exc:
            raise DeviceError(f"打开设备 {self._device!r} 失败：{exc}") from exc
        return rate, channels

    def _read_raw_block(self) -> np.ndarray:
        assert self._stream is not None
        try:
            data, overflowed = self._stream.read(self._block_frames)
        except sd.PortAudioError as exc:
            raise DeviceError(f"设备采集中断：{exc}") from exc
        if overflowed:
            self._overflow_count += 1
            logger.warning(
                "设备输入缓冲溢出（第 %d 次），消费速度跟不上实时",
                self._overflow_count,
            )
        return data

    def _close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
