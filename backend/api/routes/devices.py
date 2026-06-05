"""音频输入设备端点：枚举真实设备 + 选择下次 take 使用的设备。

GET  /api/v1/devices         列出真实输入设备（标出系统默认）+ 当前选中
POST /api/v1/devices/select  选择设备（写入 LiveAsrSession + 持久化，下次 take 生效）

选中状态：
  session._device 存设备「名字」（str），未选时为 None。
  GET selected 返回的是实际会被采集的设备 index（名字回查当前 index）。
  若持久化设备已拔走，返回 fallback（系统默认）的 index，不返回不可用的设备。

未启用实时 ASR（live_asr=None）时 GET 仍可枚举，POST select 返回 409。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.api.auth import require_admin
from backend.audio.device_resolve import resolve_device_index
from backend.audio.devices import get_default_input_index, list_input_devices

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["devices"])

_AUDIO_INPUT_DEVICE_KEY = "audio_input_device"


class DeviceOut(BaseModel):
    index: int
    name: str
    max_input_channels: int
    is_default: bool


@router.get("/devices")
async def get_devices(
    request: Request,
    _: None = Depends(require_admin),
) -> dict[str, object]:
    """枚举真实输入设备 + 当前选中信息。

    响应字段：
      devices: 设备列表
      selected: int | None — 实际会被采集的设备 index（持久化设备不在场时为 fallback）
      selected_available: bool | None — 持久化/会话设备当前是否在场；live_asr=None 时为 None
      selected_name: str | None — session._device（设备名）；live_asr=None 时为 None
    """
    devices = list_input_devices()
    device_outs = [
        DeviceOut(
            index=d.index,
            name=d.name,
            max_input_channels=d.max_input_channels,
            is_default=d.is_default,
        )
        for d in devices
    ]
    session = getattr(request.app.state, "live_asr", None)

    if session is None:
        return {
            "devices": device_outs,
            "selected": None,
            "selected_available": None,
            "selected_name": None,
        }

    name: str | None = session.device  # session 存名字
    default_index = get_default_input_index()
    selected_index, available = resolve_device_index(name, devices, default_index)

    return {
        "devices": device_outs,
        "selected": selected_index,
        "selected_available": available,
        "selected_name": name,
    }


class SelectBody(BaseModel):
    index: int


@router.post("/devices/select")
async def select_device(
    body: SelectBody,
    request: Request,
    _: None = Depends(require_admin),
) -> dict[str, object]:
    """选择输入设备（写 LiveAsrSession + 持久化）。index 须为可用输入设备，否则 422。

    收到 index → 用 list_input_devices() 反查 name → 持久化 name → session.set_device(name)。
    响应 selected 返回传入的 index（往返验证）。
    """
    session = getattr(request.app.state, "live_asr", None)
    if session is None:
        raise HTTPException(status_code=409, detail="live ASR not enabled")

    devices = list_input_devices()
    device_map = {d.index: d for d in devices}
    if body.index not in device_map:
        raise HTTPException(
            status_code=422,
            detail=f"device index {body.index} not an available input",
        )

    name = device_map[body.index].name

    # 持久化（通过 orchestrator.dal）
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is not None and hasattr(orchestrator, "dal"):
        orchestrator.dal.set_setting(_AUDIO_INPUT_DEVICE_KEY, name)

    # 更新 session（存名字）
    session.set_device(name)
    return {"status": "ok", "selected": body.index}
