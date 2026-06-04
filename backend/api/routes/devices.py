"""音频输入设备端点：枚举真实设备 + 选择下次 take 使用的设备。

GET  /api/v1/devices         列出真实输入设备（标出系统默认）+ 当前选中
POST /api/v1/devices/select  选择设备（写入 LiveAsrSession，下次 take 生效）

选中状态存在 app.state.live_asr（LiveAsrSession）。未启用实时 ASR
（SOUNDSPEED_LIVE_ASR≠1，app.state.live_asr 为 None）时 GET 仍可枚举，
POST select 返回 409。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.api.auth import require_admin
from backend.audio.devices import list_input_devices

router = APIRouter(prefix="/api/v1", tags=["devices"])


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
    """枚举真实输入设备 + 当前选中（None=系统默认/未选）。"""
    devices = list_input_devices()
    session = getattr(request.app.state, "live_asr", None)
    selected = session.device if session is not None else None
    return {
        "devices": [
            DeviceOut(
                index=d.index,
                name=d.name,
                max_input_channels=d.max_input_channels,
                is_default=d.is_default,
            )
            for d in devices
        ],
        "selected": selected,
    }


class SelectBody(BaseModel):
    index: int


@router.post("/devices/select")
async def select_device(
    body: SelectBody,
    request: Request,
    _: None = Depends(require_admin),
) -> dict[str, object]:
    """选择输入设备（写 LiveAsrSession）。index 须为可用输入设备，否则 422。"""
    session = getattr(request.app.state, "live_asr", None)
    if session is None:
        raise HTTPException(status_code=409, detail="live ASR not enabled")
    valid = {d.index for d in list_input_devices()}
    if body.index not in valid:
        raise HTTPException(status_code=422, detail=f"device index {body.index} not an available input")
    session.set_device(body.index)
    return {"status": "ok", "selected": body.index}
