# Audio Input Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Soundspeed 的 Audio Input Layer —— 把实时设备或音频文件规整成 16kHz 单声道 int16 的按声道拆分 PCM 流。

**Architecture:** 一个 `AudioSource` 抽象基类用模板方法封装通用流水线（拆声道、顺序过 ChannelProcessor、组装 AudioChunk、seq/start_frame 记账）；`DeviceSource` 和 `FileSource` 只实现 `_open` / `_read_raw_block` / `_close` 三个钩子。每声道一个有状态 `soxr.ResampleStream`，顺序处理。输出 `AudioChunk` 容器，`channels` 是各声道独立的单声道数组。

**Tech Stack:** Python 3.12（Cactus venv）、numpy、soxr（重采样）、soundfile（文件读取）、sounddevice（设备采集）、pytest。

设计依据：`docs/specs/2026-05-20-audio-input-layer.md` v0.3。

---

## 环境与前置

所有命令用 Cactus venv 的 Python。每个 shell 会话先设：

```bash
export PY=/opt/homebrew/Cellar/cactus/1.14_1/libexec/venv/bin/python
```

理由：整条流水线（音频 → Cactus ASR）最终须在 `cactus` 可 import 的环境跑，即 Cactus venv；该 venv 已有 numpy 2.4.4、soxr 1.1.0。本层代码本身不 import cactus。注意该 venv 由 brew 管理，cactus 升级 / 重装会清掉装进去的包 —— 届时按 `backend/requirements.txt` 重装一次即可。

spec 第 12 节「ChannelProcessor 接 int16 还是 float32 内部精度」的待定项，本计划定为：**全程 float32，只在每个 chunk 的最末一步转 int16**。两个源都向 sounddevice / soundfile 请求 `float32`，因此 ChannelProcessor 永远只处理 float32，不存在 int16 输入路径。

所有任务在 worktree `spike/audio-input-layer` 分支内进行。

## File Structure

```
pyproject.toml                      # 新建：pytest / ruff / mypy 配置（仓库根）
backend/
  __init__.py                       # 新建：空，使 backend 成为包
  requirements.txt                  # 新建：运行时依赖（4 个）
  audio/
    __init__.py                     # 新建：公开 API 再导出
    constants.py                    # 新建：OUTPUT_SAMPLE_RATE
    channel.py                      # 新建：ChannelProcessor
    source.py                       # 新建：AudioConfig / AudioChunk / AudioSource 抽象基类
    file_source.py                  # 新建：FileSource
    device_source.py                # 新建：DeviceSource / DeviceError
  tests/
    __init__.py                     # 新建：空
    conftest.py                     # 新建：合成 WAV 夹具
    test_channel_processor.py       # 新建
    test_audio_source.py            # 新建：用 _FakeSource 测基类通用机制
    test_file_source.py             # 新建
    test_device_source.py           # 新建
```

依赖 DAG（无环）：`constants ← channel ← source ← {file_source, device_source}`。

删除：`backend/.gitkeep`（被 `backend/__init__.py` 取代）。

---

## Task 1: backend/ 包骨架、依赖、测试工具链

**Files:**
- Create: `backend/__init__.py`, `backend/audio/__init__.py`, `backend/tests/__init__.py`
- Create: `backend/requirements.txt`, `pyproject.toml`
- Delete: `backend/.gitkeep`

- [ ] **Step 1: 建包目录与空 `__init__.py`**

```bash
rm backend/.gitkeep
touch backend/__init__.py backend/tests/__init__.py
```

`backend/audio/__init__.py` 先留空（Task 8 再填再导出）。

```bash
touch backend/audio/__init__.py
```

- [ ] **Step 2: 装依赖并冻结**

```bash
$PY -m pip install "numpy==2.4.4" "soxr==1.1.0" soundfile sounddevice pytest ruff mypy
$PY -m pip freeze | grep -iE '^(numpy|soxr|soundfile|sounddevice)==' > backend/requirements.txt
```

`numpy` / `soxr` 已在 venv，`soundfile` / `sounddevice` 为本任务新增。`pytest` / `ruff` / `mypy` 是开发工具，不进 `requirements.txt`。

- [ ] **Step 3: 写 `pyproject.toml`（仓库根）**

```toml
[tool.pytest.ini_options]
testpaths = ["backend/tests"]
pythonpath = ["."]

[tool.ruff]
line-length = 100

[tool.mypy]
python_version = "3.12"
ignore_missing_imports = true
```

`pythonpath = ["."]` 让 `import backend.audio...` 在仓库根可解析。`ignore_missing_imports` 因 soxr / sounddevice / soundfile 无类型 stub。

- [ ] **Step 4: 验证工具链**

Run: `$PY -m pytest`
Expected: `no tests ran`，exit 0（还没有测试，正常）。

Run: `$PY -m ruff check backend/`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add backend/ pyproject.toml
git commit -m "chore: 初始化 backend 包骨架与测试工具链"
```

`git add backend/` 会同时暂存新增的 `__init__.py` 等文件和 `backend/.gitkeep` 的删除。

---

## Task 2: AudioConfig / AudioChunk 数据结构 + OUTPUT_SAMPLE_RATE

**Files:**
- Create: `backend/audio/constants.py`
- Create: `backend/audio/source.py`（本任务只放数据结构，基类在 Task 4）
- Test: `backend/tests/test_audio_source.py`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_audio_source.py`：

```python
import numpy as np
import pytest

from backend.audio.constants import OUTPUT_SAMPLE_RATE
from backend.audio.source import AudioChunk, AudioConfig


def test_output_sample_rate_is_16k():
    assert OUTPUT_SAMPLE_RATE == 16000


def test_audio_config_defaults():
    cfg = AudioConfig()
    assert cfg.chunk_ms == 200
    assert cfg.max_channels == 2


def test_audio_chunk_holds_independent_channel_arrays():
    ch0 = np.zeros(3200, dtype=np.int16)
    ch1 = np.ones(3200, dtype=np.int16)
    chunk = AudioChunk(seq=0, channels=[ch0, ch1], n_frames=3200, start_frame=0)
    assert chunk.sample_rate == 16000
    assert len(chunk.channels) == 2
    assert chunk.channels[0] is ch0 and chunk.channels[1] is ch1


def test_audio_chunk_is_frozen():
    import dataclasses

    chunk = AudioChunk(seq=0, channels=[], n_frames=0, start_frame=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        chunk.seq = 1
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `$PY -m pytest backend/tests/test_audio_source.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'backend.audio.constants'`。

- [ ] **Step 3: 提交失败测试**

```bash
git add backend/tests/test_audio_source.py
git commit -m "test: AudioConfig / AudioChunk 数据结构测试"
```

- [ ] **Step 4: 写最小实现**

`backend/audio/constants.py`：

```python
"""Audio Input Layer 契约常量。"""

OUTPUT_SAMPLE_RATE = 16000  # Cactus ASR 硬约束，不可配置
```

`backend/audio/source.py`：

```python
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
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `$PY -m pytest backend/tests/test_audio_source.py -v`
Expected: 4 passed。

- [ ] **Step 6: lint + 类型检查**

Run: `$PY -m ruff check backend/ && $PY -m mypy backend/audio/`
Expected: 均通过。

- [ ] **Step 7: Commit**

```bash
git add backend/audio/constants.py backend/audio/source.py
git commit -m "feat: AudioConfig / AudioChunk 数据结构与契约常量"
```

---

## Task 3: ChannelProcessor

模块化单声道处理单元：soxr 流式重采样到 16k，输出 int16。

**Files:**
- Create: `backend/audio/channel.py`
- Test: `backend/tests/test_channel_processor.py`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_channel_processor.py`：

```python
import numpy as np

from backend.audio.channel import ChannelProcessor


def _sine(freq, rate, n):
    t = np.arange(n, dtype=np.float32) / rate
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_process_outputs_int16():
    proc = ChannelProcessor(in_rate=48000)
    out = proc.process(_sine(440, 48000, 9600))
    assert out.dtype == np.int16


def test_process_resamples_48k_to_16k_length():
    """48k -> 16k：累计输出帧数约为输入的三分之一。"""
    proc = ChannelProcessor(in_rate=48000)
    total_in = 0
    total_out = 0
    for _ in range(10):
        block = _sine(440, 48000, 9600)
        total_in += len(block)
        total_out += len(proc.process(block))
    ratio = total_out / total_in
    assert abs(ratio - 1 / 3) < 0.01


def test_process_passthrough_when_already_16k():
    proc = ChannelProcessor(in_rate=16000)
    total_in = 0
    total_out = 0
    for _ in range(10):
        block = _sine(440, 16000, 3200)
        total_in += len(block)
        total_out += len(proc.process(block))
    assert abs(total_out / total_in - 1.0) < 0.01


def test_process_preserves_amplitude():
    """半幅正弦重采样后仍是有信号、量级合理的 int16，不被静默清零。"""
    proc = ChannelProcessor(in_rate=48000)
    out = np.concatenate([proc.process(_sine(440, 48000, 9600)) for _ in range(5)])
    peak = int(np.max(np.abs(out)))
    assert 8000 < peak < 20000  # 半幅 ~16384 附近
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `$PY -m pytest backend/tests/test_channel_processor.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'backend.audio.channel'`。

- [ ] **Step 3: 提交失败测试**

```bash
git add backend/tests/test_channel_processor.py
git commit -m "test: ChannelProcessor 重采样与 int16 转换测试"
```

- [ ] **Step 4: 写最小实现**

`backend/audio/channel.py`：

```python
"""每声道处理单元：流式重采样到 16kHz，转 int16。"""
from __future__ import annotations

import numpy as np
import soxr

from backend.audio.constants import OUTPUT_SAMPLE_RATE


class ChannelProcessor:
    """有状态的单声道处理器，每声道一个实例。

    持有一个 soxr 流式重采样器 —— 必须每声道独立，因为重采样器在块
    之间携带滤波状态。输入恒为 float32 单声道（归一化到 [-1, 1]），
    输出 16kHz int16。
    """

    def __init__(self, in_rate: int) -> None:
        self._resampler = soxr.ResampleStream(
            in_rate, OUTPUT_SAMPLE_RATE, 1, dtype="float32"
        )

    def process(self, mono_block: np.ndarray) -> np.ndarray:
        """重采样一块 float32 单声道到 16kHz，返回 int16 一维数组。"""
        resampled = self._resampler.resample_chunk(
            np.ascontiguousarray(mono_block, dtype=np.float32)
        )
        scaled = np.round(resampled * 32767.0)
        return np.clip(scaled, -32768.0, 32767.0).astype(np.int16)
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `$PY -m pytest backend/tests/test_channel_processor.py -v`
Expected: 4 passed。

- [ ] **Step 6: lint + 类型检查**

Run: `$PY -m ruff check backend/ && $PY -m mypy backend/audio/`
Expected: 均通过。

- [ ] **Step 7: Commit**

```bash
git add backend/audio/channel.py
git commit -m "feat: 实现 ChannelProcessor 单声道重采样单元"
```

---

## Task 4: AudioSource 抽象基类与通用迭代机制

模板方法基类：封装拆声道、顺序处理、组装 chunk、seq/start_frame 记账。用一个测试桩 `_FakeSource` 测全部通用机制，不碰真实 I/O。

**Files:**
- Modify: `backend/audio/source.py`（追加 `AudioSource` 基类）
- Test: `backend/tests/test_audio_source.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_audio_source.py` 末尾追加：

```python
from backend.audio.source import AudioSource


class _FakeSource(AudioSource):
    """测试桩：用合成 float32 数据驱动基类，不碰真实设备 / 文件。"""

    def __init__(self, config, rate, channels, n_blocks, block_frames):
        super().__init__(config)
        self._rate = rate
        self._channels = channels
        self._remaining = n_blocks
        self._block_frames = block_frames
        self.closed = False

    def _open(self):
        return self._rate, self._channels

    def _read_raw_block(self):
        if self._remaining <= 0:
            return None
        self._remaining -= 1
        return np.full(
            (self._block_frames, self._channels), 0.25, dtype=np.float32
        )

    def _close(self):
        self.closed = True


def test_source_yields_chunks_with_truncated_channels():
    """输入 8 声道，max_channels=2 -> 每个 chunk 只有 2 路。"""
    src = _FakeSource(AudioConfig(), rate=48000, channels=8,
                      n_blocks=5, block_frames=9600)
    with src as s:
        chunks = list(s)
    assert len(chunks) == 5
    for chunk in chunks:
        assert len(chunk.channels) == 2


def test_source_mono_input_yields_one_channel():
    src = _FakeSource(AudioConfig(), rate=16000, channels=1,
                      n_blocks=3, block_frames=3200)
    with src as s:
        chunks = list(s)
    assert all(len(c.channels) == 1 for c in chunks)


def test_source_seq_and_start_frame_accumulate():
    src = _FakeSource(AudioConfig(), rate=16000, channels=2,
                      n_blocks=4, block_frames=3200)
    with src as s:
        chunks = list(s)
    assert [c.seq for c in chunks] == [0, 1, 2, 3]
    expected = 0
    for chunk in chunks:
        assert chunk.start_frame == expected
        expected += chunk.n_frames


def test_source_channels_are_independent_arrays():
    src = _FakeSource(AudioConfig(), rate=48000, channels=2,
                      n_blocks=1, block_frames=9600)
    with src as s:
        chunk = next(iter(s))
    assert chunk.channels[0] is not chunk.channels[1]


def test_source_closes_on_context_exit():
    src = _FakeSource(AudioConfig(), rate=16000, channels=2,
                      n_blocks=1, block_frames=3200)
    with src as s:
        list(s)
    assert src.closed is True
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `$PY -m pytest backend/tests/test_audio_source.py -v`
Expected: 新增 5 个测试 FAIL —— `ImportError: cannot import name 'AudioSource'`（旧 4 个仍 pass）。

- [ ] **Step 3: 提交失败测试**

```bash
git add backend/tests/test_audio_source.py
git commit -m "test: AudioSource 基类通用迭代机制测试"
```

- [ ] **Step 4: 写最小实现**

在 `backend/audio/source.py` 顶部 import 区改为：

```python
"""Audio Input Layer：源抽象与输出数据结构。"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from backend.audio.channel import ChannelProcessor
from backend.audio.constants import OUTPUT_SAMPLE_RATE

logger = logging.getLogger(__name__)
```

`AudioConfig` / `AudioChunk` 定义不动。在文件末尾追加：

```python
class AudioSource(ABC):
    """拉取式音频源：上下文管理器 + AudioChunk 迭代器。

    子类只实现三个钩子：_open / _read_raw_block / _close。通用机制
    （声道截断、顺序逐声道处理、组装 chunk、seq/start_frame 记账）在
    本基类，对每个源完全一致。
    """

    def __init__(self, config: AudioConfig) -> None:
        self._config = config
        self._seq = 0
        self._start_frame = 0
        self._n_channels = 0
        self._processors: list[ChannelProcessor] = []

    # ---- 子类钩子 ----
    @abstractmethod
    def _open(self) -> tuple[int, int]:
        """打开源。返回 (原生采样率, 源声道总数)。"""

    @abstractmethod
    def _read_raw_block(self) -> np.ndarray | None:
        """读一块原始音频，二维 float32 数组 (帧, 源声道数)。
        源耗尽时返回 None。"""

    @abstractmethod
    def _close(self) -> None:
        """释放底层设备流 / 文件句柄。"""

    # ---- 通用机制 ----
    def __enter__(self) -> "AudioSource":
        native_rate, source_channels = self._open()
        self._n_channels = min(source_channels, self._config.max_channels)
        if source_channels > self._n_channels:
            logger.info(
                "输入 %d 声道，处理前 %d 路，丢弃 %d 路",
                source_channels, self._n_channels,
                source_channels - self._n_channels,
            )
        self._processors = [
            ChannelProcessor(native_rate) for _ in range(self._n_channels)
        ]
        return self

    def __exit__(self, *exc: object) -> None:
        self._close()

    def __iter__(self) -> "AudioSource":
        return self

    def __next__(self) -> AudioChunk:
        raw = self._read_raw_block()
        if raw is None:
            raise StopIteration
        out_channels = [
            self._processors[i].process(raw[:, i])
            for i in range(self._n_channels)
        ]
        n_frames = len(out_channels[0])
        chunk = AudioChunk(
            seq=self._seq,
            channels=out_channels,
            n_frames=n_frames,
            start_frame=self._start_frame,
        )
        self._seq += 1
        self._start_frame += n_frames
        return chunk
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `$PY -m pytest backend/tests/test_audio_source.py -v`
Expected: 9 passed。

- [ ] **Step 6: lint + 类型检查**

Run: `$PY -m ruff check backend/ && $PY -m mypy backend/audio/`
Expected: 均通过。

- [ ] **Step 7: Commit**

```bash
git add backend/audio/source.py
git commit -m "feat: 实现 AudioSource 抽象基类与通用迭代机制"
```

---

## Task 5: 测试夹具（合成 WAV）

用 conftest fixture 在 `tmp_path` 临时生成 WAV，不把二进制提交进 git。

**Files:**
- Create: `backend/tests/conftest.py`

- [ ] **Step 1: 写 conftest fixture**

`backend/tests/conftest.py`：

```python
"""测试夹具：用 soundfile 合成临时 WAV。"""
import numpy as np
import pytest
import soundfile as sf


def _sine(freq, rate, seconds):
    t = np.arange(int(rate * seconds), dtype=np.float32) / rate
    return 0.5 * np.sin(2 * np.pi * freq * t)


def _write(path, data, rate):
    sf.write(str(path), data.astype(np.float32), rate, subtype="FLOAT")
    return str(path)


@pytest.fixture
def stereo_48k_wav(tmp_path):
    """1.0s 立体声 48kHz：两路不同频率。"""
    left = _sine(440, 48000, 1.0)
    right = _sine(880, 48000, 1.0)
    data = np.stack([left, right], axis=1)
    return _write(tmp_path / "stereo_48k.wav", data, 48000)


@pytest.fixture
def mono_16k_wav(tmp_path):
    """1.0s 单声道 16kHz。"""
    return _write(tmp_path / "mono_16k.wav", _sine(440, 16000, 1.0), 16000)


@pytest.fixture
def odd_rate_wav(tmp_path):
    """1.0s 立体声 44.1kHz：非常规采样率。"""
    data = np.stack([_sine(440, 44100, 1.0), _sine(660, 44100, 1.0)], axis=1)
    return _write(tmp_path / "odd_44k.wav", data, 44100)


@pytest.fixture
def four_channel_wav(tmp_path):
    """1.0s 四声道 48kHz：超过 max_channels。"""
    chans = [_sine(f, 48000, 1.0) for f in (440, 550, 660, 770)]
    data = np.stack(chans, axis=1)
    return _write(tmp_path / "four_ch_48k.wav", data, 48000)
```

- [ ] **Step 2: 验证 fixture 可被收集**

Run: `$PY -m pytest backend/tests/ --fixtures -q | grep -E 'stereo_48k_wav|four_channel_wav'`
Expected: 两个 fixture 名都列出。

- [ ] **Step 3: lint**

Run: `$PY -m ruff check backend/`
Expected: 通过。

- [ ] **Step 4: Commit**

```bash
git add backend/tests/conftest.py
git commit -m "test: 新增合成 WAV 测试夹具"
```

---

## Task 6: FileSource

实现文件源的三个钩子。通用机制已在 Task 4 测过，本任务用真实夹具 WAV 做端到端验证。

**Files:**
- Create: `backend/audio/file_source.py`
- Test: `backend/tests/test_file_source.py`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_file_source.py`：

```python
import numpy as np

from backend.audio.file_source import FileSource
from backend.audio.source import AudioConfig


def test_file_source_stereo_48k(stereo_48k_wav):
    with FileSource(stereo_48k_wav, AudioConfig()) as src:
        chunks = list(src)
    assert len(chunks) > 0
    for chunk in chunks:
        assert len(chunk.channels) == 2
        assert chunk.sample_rate == 16000
        assert all(c.dtype == np.int16 for c in chunk.channels)
    assert [c.seq for c in chunks] == list(range(len(chunks)))


def test_file_source_total_frames_match_duration(stereo_48k_wav):
    """1.0s 文件 -> 累计输出帧数约 16000（容差含重采样器延迟）。"""
    with FileSource(stereo_48k_wav, AudioConfig()) as src:
        total = sum(c.n_frames for c in src)
    assert abs(total - 16000) < 200


def test_file_source_mono(mono_16k_wav):
    with FileSource(mono_16k_wav, AudioConfig()) as src:
        chunks = list(src)
    assert all(len(c.channels) == 1 for c in chunks)


def test_file_source_truncates_four_channels(four_channel_wav):
    """4 声道文件 -> 只输出前 2 路。"""
    with FileSource(four_channel_wav, AudioConfig()) as src:
        chunks = list(src)
    assert all(len(c.channels) == 2 for c in chunks)


def test_file_source_odd_rate_resamples(odd_rate_wav):
    with FileSource(odd_rate_wav, AudioConfig()) as src:
        chunks = list(src)
    assert all(c.sample_rate == 16000 for c in chunks)
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `$PY -m pytest backend/tests/test_file_source.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'backend.audio.file_source'`。

- [ ] **Step 3: 提交失败测试**

```bash
git add backend/tests/test_file_source.py
git commit -m "test: FileSource 文件源端到端测试"
```

- [ ] **Step 4: 写最小实现**

`backend/audio/file_source.py`：

```python
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
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `$PY -m pytest backend/tests/test_file_source.py -v`
Expected: 5 passed。

- [ ] **Step 6: lint + 类型检查**

Run: `$PY -m ruff check backend/ && $PY -m mypy backend/audio/`
Expected: 均通过。

- [ ] **Step 7: Commit**

```bash
git add backend/audio/file_source.py
git commit -m "feat: 实现 FileSource 文件音频源"
```

---

## Task 7: DeviceSource

实现设备源的三个钩子。处理逻辑全在基类（Task 4 已测）。本任务可自动化测试的是错误路径；真实采集留手动测试。

**Files:**
- Create: `backend/audio/device_source.py`
- Test: `backend/tests/test_device_source.py`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_device_source.py`：

```python
import pytest

from backend.audio.device_source import DeviceError, DeviceSource
from backend.audio.source import AudioConfig


def test_device_source_unknown_device_raises_with_device_list():
    """不存在的设备名 -> DeviceError，错误信息含可用设备清单。"""
    src = DeviceSource("不存在的设备_xyz_12345", AudioConfig())
    with pytest.raises(DeviceError) as exc:
        src.__enter__()
    assert "可用输入设备" in str(exc.value)
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `$PY -m pytest backend/tests/test_device_source.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'backend.audio.device_source'`。

- [ ] **Step 3: 提交失败测试**

```bash
git add backend/tests/test_device_source.py
git commit -m "test: DeviceSource 未知设备错误路径测试"
```

- [ ] **Step 4: 写最小实现**

`backend/audio/device_source.py`：

```python
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
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `$PY -m pytest backend/tests/test_device_source.py -v`
Expected: 1 passed。

- [ ] **Step 6: lint + 类型检查**

Run: `$PY -m ruff check backend/ && $PY -m mypy backend/audio/`
Expected: 均通过。

- [ ] **Step 7: 手动测试真实采集**

把以下片段存为临时文件 `/tmp/smoke_device.py` 并运行（不进 git）：

```python
import sys
sys.path.insert(0, ".")
from backend.audio.device_source import DeviceSource
from backend.audio.source import AudioConfig

with DeviceSource(0, AudioConfig()) as src:  # 0 = 默认输入设备
    for chunk in src:
        print(f"seq={chunk.seq} 声道={len(chunk.channels)} "
              f"n_frames={chunk.n_frames} start_frame={chunk.start_frame} "
              f"peak={max(int(abs(c).max()) for c in chunk.channels)}")
        if chunk.seq >= 10:
            break
```

注：`0` 是设备索引，未必是输入设备。若报 `DeviceError`，按错误信息列出的可用输入设备清单，把 `0` 换成真实设备名（如 `"Babyface Pro"`）。

Run: `$PY /tmp/smoke_device.py`
Expected: 打印 11 行，`seq` 0→10 递增，`n_frames` 约 3200，对着麦克风说话时 `peak` 明显上升。

人工验证通过后，开一个 GitHub Issue 记录手动验证步骤（设备型号、上述命令、观察结果），供队友复现。

- [ ] **Step 8: Commit**

```bash
git add backend/audio/device_source.py
git commit -m "feat: 实现 DeviceSource 实时采集源 [手动测试]"
```

---

## Task 8: 公开 API 再导出与全量校验

**Files:**
- Modify: `backend/audio/__init__.py`

- [ ] **Step 1: 写 `__init__.py` 再导出**

`backend/audio/__init__.py`：

```python
"""Audio Input Layer 公开 API。"""
from backend.audio.channel import ChannelProcessor
from backend.audio.constants import OUTPUT_SAMPLE_RATE
from backend.audio.device_source import DeviceError, DeviceSource
from backend.audio.file_source import FileSource
from backend.audio.source import AudioChunk, AudioConfig, AudioSource

__all__ = [
    "OUTPUT_SAMPLE_RATE",
    "AudioConfig",
    "AudioChunk",
    "AudioSource",
    "ChannelProcessor",
    "FileSource",
    "DeviceSource",
    "DeviceError",
]
```

- [ ] **Step 2: 写导出测试**

`backend/tests/test_audio_source.py` 末尾追加：

```python
def test_public_api_reexported():
    import backend.audio as audio

    for name in ("AudioConfig", "AudioChunk", "AudioSource", "ChannelProcessor",
                 "FileSource", "DeviceSource", "DeviceError", "OUTPUT_SAMPLE_RATE"):
        assert hasattr(audio, name), name
```

- [ ] **Step 3: 全量校验**

Run: `$PY -m pytest`
Expected: 全部 passed（Task 2–8 累计约 20 个测试）。

Run: `$PY -m ruff check backend/ && $PY -m mypy backend/`
Expected: 均通过。

- [ ] **Step 4: Commit**

```bash
git add backend/audio/__init__.py backend/tests/test_audio_source.py
git commit -m "feat: 导出 Audio Input Layer 公开 API"
```

---

## 已知限制（实现时不处理，留作记录）

- 流式重采样器在文件末尾有约几毫秒的滤波尾巴未 flush（`resample_chunk` 的 `last` 参数未用）。对 ASR 喂入可忽略。若日后发现文件末尾词被截，再在 `ChannelProcessor` 加 flush 路径。
- DeviceSource 的真实采集只有手动测试覆盖；自动化测试仅覆盖未知设备错误路径。
- Task 3 / 6 测试里的容差（帧数、比例、峰值区间）是估计值，非实测。soxr 在 48k→16k 的滤波延迟可能令实际帧数偏移几十帧。若某测试仅因容差临界失败，应放宽容差，而非追查不存在的 bug。

## Self-Review

**Spec 覆盖：** spec v0.3 各节 → 任务对应：
- 第 4 节组件（AudioSource / DeviceSource / FileSource / ChannelProcessor / AudioChunk / AudioConfig）→ Task 2/3/4/6/7。
- 第 5 节 AudioChunk / AudioConfig 字段 → Task 2。
- 第 6 节声道规则（前 2 路、>2 丢弃不报错 + info 日志、顺序处理）→ Task 4 `__enter__` 截断 + 日志、`__next__` 顺序循环；Task 6 四声道测试。
- 第 7 节重采样（soxr 显式、原生率开流、float32 内部）→ Task 3、Task 6/7 的 `_open`。
- 第 8 节错误处理（设备找不到列清单、采集中断 DeviceError、溢出告警、文件 EOF StopIteration）→ Task 7 实现与测试、Task 4 `__next__` 的 None→StopIteration。
- 第 9 节测试策略（ChannelProcessor 纯单元、FileSource 夹具、DeviceSource 纯逻辑在基类 + 薄层手动）→ Task 3/4/6/7。
- 第 10 节文件布局 → File Structure（增 `constants.py` 拆环，已说明）。
- 第 12 节待定项 int16/float32 → 「环境与前置」定为全程 float32。

**占位符扫描：** 无 TBD / TODO；每步含完整代码或确切命令。

**类型一致性：** `AudioSource._open` 返回 `(int, int)`、`_read_raw_block` 返回 `np.ndarray | None`、`_close` 返回 `None` —— Task 4 定义，Task 6/7 实现签名一致。`ChannelProcessor(in_rate)` / `.process(mono_block)`、`AudioChunk(seq, channels, n_frames, start_frame, sample_rate)`、`AudioConfig(chunk_ms, max_channels)` 全计划一致。`DeviceError` 在 Task 7 定义并被其测试引用。

## Execution Handoff

Plan complete and saved to `docs/plans/2026-05-20-audio-input-layer.md`. Two execution options:

1. **Subagent-Driven (recommended)** — 每个任务派一个全新 subagent，任务间审查，迭代快。
2. **Inline Execution** — 在当前 session 内分批执行，带检查点。

Which approach?
