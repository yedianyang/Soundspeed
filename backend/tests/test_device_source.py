import pytest

from backend.audio.device_source import DeviceError, DeviceSource
from backend.audio.source import AudioConfig


def test_device_source_unknown_device_raises_with_device_list():
    """不存在的设备名 -> DeviceError，错误信息含可用设备清单。"""
    src = DeviceSource("不存在的设备_xyz_12345", AudioConfig())
    with pytest.raises(DeviceError) as exc:
        with src:
            pass
    assert "可用输入设备" in str(exc.value)
