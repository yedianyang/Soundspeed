"""音频输入设备枚举测试（list_input_devices）。注入假 query，不依赖真实设备。"""
from backend.audio.devices import InputDevice, list_input_devices

# 模拟 sd.query_devices() 返回：含输入与纯输出设备
_FAKE_DEVICES = [
    {"name": "麦克风 A", "max_input_channels": 2, "max_output_channels": 0},
    {"name": "扬声器（纯输出）", "max_input_channels": 0, "max_output_channels": 2},
    {"name": "USB 接口", "max_input_channels": 8, "max_output_channels": 8},
]


def test_lists_only_input_devices():
    devs = list_input_devices(query=lambda: _FAKE_DEVICES, default_device=(0, 3))
    names = [d.name for d in devs]
    assert names == ["麦克风 A", "USB 接口"]  # 纯输出设备被过滤
    assert all(isinstance(d, InputDevice) for d in devs)


def test_index_preserved_from_global_enumeration():
    devs = list_input_devices(query=lambda: _FAKE_DEVICES, default_device=None)
    # USB 接口是全局第 2 个设备（含纯输出），index 应为 2 而非过滤后的 1
    usb = next(d for d in devs if d.name == "USB 接口")
    assert usb.index == 2


def test_default_input_flagged():
    devs = list_input_devices(query=lambda: _FAKE_DEVICES, default_device=(0, 1))
    flagged = [d for d in devs if d.is_default]
    assert len(flagged) == 1
    assert flagged[0].index == 0


def test_default_device_scalar_form():
    devs = list_input_devices(query=lambda: _FAKE_DEVICES, default_device=2)
    assert [d for d in devs if d.is_default][0].index == 2


def test_no_default_when_unset():
    devs = list_input_devices(query=lambda: _FAKE_DEVICES, default_device=(-1, -1))
    assert all(not d.is_default for d in devs)


# 同名设备在多套 host API 下重复枚举
_DUP_DEVICES = [
    {"name": "麦克风 (USB)", "max_input_channels": 2, "max_output_channels": 0},
    {"name": "麦克风 (USB)", "max_input_channels": 2, "max_output_channels": 0},
    {"name": "麦克风 (USB)", "max_input_channels": 2, "max_output_channels": 0},
    {"name": "蓝牙耳机", "max_input_channels": 1, "max_output_channels": 0},
]


def test_dedup_by_name_keeps_one():
    devs = list_input_devices(query=lambda: _DUP_DEVICES, default_device=None)
    assert [d.name for d in devs] == ["麦克风 (USB)", "蓝牙耳机"]


def test_dedup_off_keeps_all():
    devs = list_input_devices(query=lambda: _DUP_DEVICES, default_device=None, dedup_by_name=False)
    assert len(devs) == 4


def test_dedup_default_flag_merged_to_kept():
    # 默认设备是第 2 条重复项（index 1），去重后第一条（index 0）应继承默认标记
    devs = list_input_devices(query=lambda: _DUP_DEVICES, default_device=1)
    usb = next(d for d in devs if d.name == "麦克风 (USB)")
    assert usb.index == 0
    assert usb.is_default is True
