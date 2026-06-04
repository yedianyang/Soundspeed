"""真实音频输入设备枚举（供 GET /api/v1/devices）。

薄封装 sounddevice，只列有输入通道的设备并标出系统默认输入。
枚举逻辑可注入 query/default 以便测试（不依赖真实 PortAudio 设备）。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    max_input_channels: int
    is_default: bool


def _default_input_index(default_device: object) -> int | None:
    """从 sd.default.device 解析默认输入索引。

    sounddevice 的 default.device 可能是 (input, output) 或单值；-1/None 表示未设。
    """
    di = default_device
    if isinstance(di, (list, tuple)):
        di = di[0] if di else None
    if isinstance(di, int) and di >= 0:
        return di
    return None


def list_input_devices(
    query: Callable[[], object] | None = None,
    default_device: object = None,
    dedup_by_name: bool = True,
) -> list[InputDevice]:
    """枚举有输入通道的设备。query/default_device 缺省走 sounddevice。

    dedup_by_name=True 时按设备名去重（同一物理设备在 MME/DirectSound/WASAPI
    三套 host API 下会重复枚举）。保留第一条；若重复项中有默认设备，则该名标默认。
    """
    if query is None:
        import sounddevice as sd

        query = sd.query_devices  # type: ignore[assignment]
        default_device = sd.default.device

    devices = query()  # type: ignore[misc]
    default_idx = _default_input_index(default_device)

    out: list[InputDevice] = []
    seen: dict[str, int] = {}  # name → out 列表下标
    for i, d in enumerate(devices):  # type: ignore[arg-type]
        max_in = int(d["max_input_channels"])
        if max_in <= 0:
            continue
        name = str(d["name"])
        is_default = i == default_idx
        if dedup_by_name and name in seen:
            # 已有同名：若当前这条是默认设备，把默认标记并到保留的那条
            if is_default:
                kept = out[seen[name]]
                out[seen[name]] = InputDevice(kept.index, kept.name, kept.max_input_channels, True)
            continue
        seen[name] = len(out)
        out.append(InputDevice(index=i, name=name, max_input_channels=max_in, is_default=is_default))
    return out
