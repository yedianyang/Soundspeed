# 声纹录入改走后端现场麦 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让说话人台账录声纹（enroll）从后端 sounddevice 现场麦采集，与 Capture 同设备同链路，消掉 enroll 与 take embedding 之间的物理麦 + 编解码 domain gap。

**Architecture:** 新增 `EnrollRecorder`（`LiveAsrSession` 轻量兄弟，后台线程迭代 `DeviceSource` 累积 `chunk.channels[0]` 16kHz int16）。`LiveAsrSession` 暴露 `make_source()` 让 enroll 复用 Capture 的设备解析。speakers 路由新增 start/stop/cancel 端点，stop 走共用 `_finalize_enrollment`（静音守卫 + 提 embedding + 存库）。保留 multipart upload 端点作内部原语。前端弹窗从浏览器录音改为后端录音遥控。

**Tech Stack:** Python 3.12 / FastAPI / sounddevice / numpy / pyannote.audio；前端 React + TypeScript + Vite。

**Spec:** [docs/specs/2026-06-06-enroll-backend-device.md](../specs/2026-06-06-enroll-backend-device.md)

**全程基线命令（worktree 根目录运行）：**
```bash
PYTEST="/Users/yedianyang/Documents/GitHub/Soundspeed/.venv/bin/python -m pytest"
```
起点基线：`659 passed, 6 skipped`。

---

### Task 1: `LiveAsrSession.make_source()`

让 enroll 复用 take 的设备解析（同设备、跟随运行时 `set_device`）。

**Files:**
- Modify: `backend/asr/live_session.py`
- Test: `backend/tests/test_live_session.py`

- [ ] **Step 1: Write the failing test**

加到 `backend/tests/test_live_session.py` 末尾：

```python
def test_make_source_uses_current_device():
    seen: list[object] = []
    sentinel = _InfiniteSilenceSource()
    session = LiveAsrSession(
        runner=_FakeRunner(),
        publish=lambda topic, payload: None,
        source_factory=lambda device: (seen.append(device), sentinel)[1],
        vad_config=_vad_cfg(),
        detector_factory=lambda: _AmplitudeVad(),
        default_device="USB Mic",
    )
    src = session.make_source()
    assert src is sentinel
    assert seen == ["USB Mic"]
    # 跟随 set_device
    session.set_device(7)
    session.make_source()
    assert seen == ["USB Mic", 7]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PYTEST backend/tests/test_live_session.py::test_make_source_uses_current_device -v`
Expected: FAIL with `AttributeError: 'LiveAsrSession' object has no attribute 'make_source'`

- [ ] **Step 3: Write minimal implementation**

在 `backend/asr/live_session.py` 的 `LiveAsrSession` 里，`set_device` 方法之后加：

```python
    def make_source(self):
        """按当前设备构造一个 AudioSource（与 take 同设备解析，跟随 set_device）。

        供 EnrollRecorder 复用 —— 保证录声纹和 Capture 永远用同一支后端麦。
        """
        return self._source_factory(self._device)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$PYTEST backend/tests/test_live_session.py -v`
Expected: PASS（含原有 6 个用例）

- [ ] **Step 5: Commit**

```bash
git add backend/asr/live_session.py backend/tests/test_live_session.py
git commit -m "feat(enroll): LiveAsrSession.make_source 复用 take 设备解析"
```

---

### Task 2: `EnrollRecorder` 核心

后台线程迭代 `DeviceSource`，累积 `chunk.channels[0]`，start/stop/abort，60s 上限，capture 互斥。纯采集，不依赖 engine。

**Files:**
- Create: `backend/diarization/enroll_recorder.py`
- Test: `backend/tests/test_enroll_recorder.py`

- [ ] **Step 1: Write the failing test**

新建 `backend/tests/test_enroll_recorder.py`：

```python
"""EnrollRecorder 线程生命周期 + 守卫测试（注入假源，不碰 PortAudio）。"""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from backend.audio.source import AudioChunk
from backend.diarization.enroll_recorder import (
    CaptureActiveError,
    EnrollBusyError,
    EnrollRecorder,
)


class _FiniteSource:
    """吐 n_chunks 个固定 chunk（每块 chunk_frames 个样本）后耗尽。"""

    def __init__(self, n_chunks: int, value: int = 1000, chunk_frames: int = 1600):
        self._n = n_chunks
        self._value = value
        self._chunk_frames = chunk_frames
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        self.exited = True

    def __iter__(self):
        for sf in range(self._n):
            yield AudioChunk(
                seq=sf,
                channels=[np.full(self._chunk_frames, self._value, dtype=np.int16)],
                n_frames=self._chunk_frames,
                start_frame=sf * self._chunk_frames,
            )


class _InfiniteSource:
    """无限吐 chunk，模拟实时设备流；首次迭代置 started 事件。"""

    def __init__(self, value: int = 1000, chunk_frames: int = 1600):
        self._value = value
        self._chunk_frames = chunk_frames
        self.started = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def __iter__(self):
        sf = 0
        while True:
            self.started.set()
            yield AudioChunk(
                seq=sf,
                channels=[np.full(self._chunk_frames, self._value, dtype=np.int16)],
                n_frames=self._chunk_frames,
                start_frame=sf * self._chunk_frames,
            )
            sf += 1
            time.sleep(0.001)


def test_start_stop_accumulates_channel0():
    src = _FiniteSource(n_chunks=3, value=1000, chunk_frames=1600)
    rec = EnrollRecorder(make_source=lambda: src)
    rec.start()
    # 源有限，线程会自己跑完；stop 拿全部 buffer
    time.sleep(0.1)
    pcm = rec.stop()
    assert pcm.dtype == np.int16
    assert len(pcm) == 3 * 1600
    assert int(pcm[0]) == 1000
    assert src.entered and src.exited  # 上下文管理器正确进出


def test_stop_without_start_returns_empty():
    rec = EnrollRecorder(make_source=lambda: _FiniteSource(0))
    pcm = rec.stop()
    assert pcm.dtype == np.int16
    assert len(pcm) == 0


def test_running_flag_and_stop_joins():
    src = _InfiniteSource()
    rec = EnrollRecorder(make_source=lambda: src)
    assert not rec.running
    rec.start()
    assert src.started.wait(timeout=2.0)
    assert rec.running
    pcm = rec.stop()
    assert not rec.running
    assert len(pcm) > 0


def test_max_seconds_caps_buffer():
    # max_seconds=0.5s @16k = 8000 帧；每块 1600 → 第 5 块越界，截断到 ~8000
    src = _InfiniteSource(chunk_frames=1600)
    rec = EnrollRecorder(make_source=lambda: src, max_seconds=0.5, sample_rate=16000)
    rec.start()
    # 等线程自行触顶停止
    for _ in range(200):
        if not rec.running:
            break
        time.sleep(0.01)
    assert not rec.running  # cap 后线程自停
    pcm = rec.stop()
    assert 8000 <= len(pcm) <= 8000 + 1600  # 触顶那块算进来


def test_start_rejected_while_capture_active():
    rec = EnrollRecorder(make_source=lambda: _FiniteSource(1), is_capture_active=lambda: True)
    with pytest.raises(CaptureActiveError):
        rec.start()


def test_double_start_rejected():
    src = _InfiniteSource()
    rec = EnrollRecorder(make_source=lambda: src)
    rec.start()
    assert src.started.wait(timeout=2.0)
    with pytest.raises(EnrollBusyError):
        rec.start()
    rec.stop()


def test_abort_discards_buffer_and_releases():
    src = _InfiniteSource()
    rec = EnrollRecorder(make_source=lambda: src)
    rec.start()
    assert src.started.wait(timeout=2.0)
    rec.abort()
    assert not rec.running
    # abort 后再 stop 返回空（buffer 已弃）
    assert len(rec.stop()) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PYTEST backend/tests/test_enroll_recorder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.diarization.enroll_recorder'`

- [ ] **Step 3: Write minimal implementation**

新建 `backend/diarization/enroll_recorder.py`：

```python
"""EnrollRecorder：录声纹时从后端现场麦采集（与 Capture 同设备同链路）。

LiveAsrSession 的轻量兄弟：后台 daemon 线程迭代一个 AudioSource，累积
chunk.channels[0]（已是 16kHz 单声道 int16）。纯采集，不跑 VAD/ASR、不提 embedding
（提取在路由的 _finalize_enrollment 里做）。

设备互斥：start() 在 capture 进行中拒绝（CaptureActiveError）；TAKE_START 反向由
entrypoint 的 take-start handler 先 abort 本录音（Capture 优先）。
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import numpy as np

from backend.audio.constants import OUTPUT_SAMPLE_RATE

logger = logging.getLogger(__name__)


class EnrollBusyError(RuntimeError):
    """已有一段录音在进行。"""


class CaptureActiveError(RuntimeError):
    """正在 Capture（take 录制中），不能同时用现场麦录声纹。"""


class EnrollRecorder:
    """单实例、线程化的现场麦声纹录音器。"""

    def __init__(
        self,
        make_source: Callable[[], object],
        is_capture_active: Callable[[], bool] = lambda: False,
        max_seconds: float = 60.0,
        sample_rate: int = OUTPUT_SAMPLE_RATE,
    ) -> None:
        self._make_source = make_source
        self._is_capture_active = is_capture_active
        self._max_seconds = max_seconds
        self._sample_rate = sample_rate
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._chunks: list[np.ndarray] = []
        self._capped = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """起后台录音线程。capture 进行中 → CaptureActiveError；已在录 → EnrollBusyError。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise EnrollBusyError("已有一段声纹录音在进行")
            if self._is_capture_active():
                raise CaptureActiveError("正在 Capture，无法同时用现场麦录声纹")
            self._stop_event.clear()
            self._chunks = []
            self._capped = False
            source = self._make_source()
            self._thread = threading.Thread(
                target=self._run, args=(source,), name="enroll-rec", daemon=True
            )
            self._thread.start()

    def _run(self, source: object) -> None:
        max_frames = int(self._max_seconds * self._sample_rate)
        total = 0
        try:
            with source as s:  # type: ignore[attr-defined]
                for chunk in s:
                    if self._stop_event.is_set():
                        break
                    ch0 = chunk.channels[0]
                    self._chunks.append(ch0)
                    total += len(ch0)
                    if total >= max_frames:
                        self._capped = True
                        logger.info("enroll 录音触顶 %.0fs，自动停止", self._max_seconds)
                        break
        except Exception:
            logger.exception("enroll 录音线程异常退出")

    def stop(self, timeout: float = 5.0) -> np.ndarray:
        """停止并返回累积的 int16 buffer（未起 / 已 abort → 空数组）。"""
        thread = self._join_and_clear_thread(timeout)
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if thread is None or not chunks:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(chunks)

    def abort(self, timeout: float = 5.0) -> None:
        """放弃录音、弃 buffer、释放设备。未运行时安全 no-op。"""
        self._join_and_clear_thread(timeout)
        with self._lock:
            self._chunks = []

    def _join_and_clear_thread(self, timeout: float) -> threading.Thread | None:
        with self._lock:
            thread = self._thread
            self._stop_event.set()
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning("enroll 录音线程 %.1fs 内未收束", timeout)
        with self._lock:
            self._thread = None
        return thread
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$PYTEST backend/tests/test_enroll_recorder.py -v`
Expected: PASS（7 个用例）

- [ ] **Step 5: Commit**

```bash
git add backend/diarization/enroll_recorder.py backend/tests/test_enroll_recorder.py
git commit -m "feat(enroll): EnrollRecorder 现场麦采集核心（start/stop/abort/cap/互斥）"
```

---

### Task 3: 共用 `_finalize_enrollment` + 静音守卫

抽 upload handler 尾巴为共用函数，加静音/能量守卫；改 upload endpoint 调它；更新受影响的旧测试。

**Files:**
- Modify: `backend/api/routes/speakers.py`
- Test: `backend/tests/test_speakers_route.py`

- [ ] **Step 1: Write the failing tests**

改 `backend/tests/test_speakers_route.py`：把 `_pcm_bytes` 改成静音，新增 `_audible_pcm_bytes`，并更新两个旧用例 + 加一个静音用例。

替换原 `_pcm_bytes` 定义块（第 93-94 行附近）为：

```python
def _silent_pcm_bytes(seconds: float) -> bytes:
    return np.zeros(int(16000 * seconds), dtype=np.int16).tobytes()


def _audible_pcm_bytes(seconds: float, amp: int = 2000) -> bytes:
    # 恒定幅度方波，RMS=amp，远高于静音守卫阈值
    n = int(16000 * seconds)
    return np.full(n, amp, dtype=np.int16).tobytes()
```

把原先调用 `_pcm_bytes(...)` 的三处旧用例按下面改（`test_enroll_without_engine_503` 用静音即可，因为 engine=None 在守卫前就 503）：

```python
def test_enroll_without_engine_503(tmp_dal: DAL, monkeypatch):
    with _client(tmp_dal, monkeypatch, engine=None) as c:
        sid = c.post("/api/v1/speakers", json={"display_name": "张三"}, headers=_HEADERS).json()["speaker_id"]
        r = c.post(
            f"/api/v1/speakers/{sid}/enroll",
            files={"file": ("a.pcm", _silent_pcm_bytes(3), "application/octet-stream")},
            params={"sample_rate": 16000},
            headers=_HEADERS,
        )
        assert r.status_code == 503


def test_enroll_too_short_400(tmp_dal: DAL, monkeypatch):
    with _client(tmp_dal, monkeypatch, engine=_FakeEngine()) as c:
        sid = c.post("/api/v1/speakers", json={"display_name": "张三"}, headers=_HEADERS).json()["speaker_id"]
        r = c.post(
            f"/api/v1/speakers/{sid}/enroll",
            files={"file": ("a.pcm", _audible_pcm_bytes(1.0), "application/octet-stream")},  # 有声但 <2s
            params={"sample_rate": 16000},
            headers=_HEADERS,
        )
        assert r.status_code == 400


def test_enroll_silent_400(tmp_dal: DAL, monkeypatch):
    with _client(tmp_dal, monkeypatch, engine=_FakeEngine()) as c:
        sid = c.post("/api/v1/speakers", json={"display_name": "张三"}, headers=_HEADERS).json()["speaker_id"]
        r = c.post(
            f"/api/v1/speakers/{sid}/enroll",
            files={"file": ("a.pcm", _silent_pcm_bytes(3), "application/octet-stream")},  # 够长但静音
            params={"sample_rate": 16000},
            headers=_HEADERS,
        )
        assert r.status_code == 400


def test_enroll_success_sets_embedding(tmp_dal: DAL, monkeypatch):
    engine = _FakeEngine()
    with _client(tmp_dal, monkeypatch, engine=engine) as c:
        sid = c.post("/api/v1/speakers", json={"display_name": "张三"}, headers=_HEADERS).json()["speaker_id"]
        r = c.post(
            f"/api/v1/speakers/{sid}/enroll",
            files={"file": ("a.pcm", _audible_pcm_bytes(3), "application/octet-stream")},
            params={"sample_rate": 16000},
            headers=_HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["has_enrollment"] is True
        assert body["sample_count"] == 1
        assert engine.calls == [16000 * 3]
```

`test_enroll_missing_speaker_404` 里 `_pcm_bytes(3)` 改为 `_audible_pcm_bytes(3)`。`test_enroll_empty_file_400` 不变（空文件）。

- [ ] **Step 2: Run tests to verify they fail**

Run: `$PYTEST backend/tests/test_speakers_route.py -k enroll -v`
Expected: `test_enroll_silent_400` FAIL（当前无静音守卫，返回 200 而非 400）；其余可能仍过（旧逻辑）。这一步确认静音守卫缺失。

- [ ] **Step 3: Write minimal implementation**

在 `backend/api/routes/speakers.py`：

(a) 顶部常量区（`MIN_ENROLL_SECONDS = 2.0` 之后）加：

```python
MIN_ENROLL_RMS = 30.0  # int16 RMS 静音阈值（≈ -60dBFS）；低于此判定现场麦没收到声音
```

(b) `_parse_wav` 之后、端点之前，加共用 finalize：

```python
def _rms_int16(pcm: np.ndarray) -> float:
    if len(pcm) == 0:
        return 0.0
    return float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))


async def _finalize_enrollment(
    dal: DAL, engine, speaker_id: int, pcm: np.ndarray
) -> SpeakerOut:
    """enroll 共用尾巴：静音守卫 → 时长守卫 → 提 embedding → 存库 → SpeakerOut。

    upload 端点与现场麦 stop 端点共用，保证两条路径一致。
    """
    import asyncio

    if _rms_int16(pcm) < MIN_ENROLL_RMS:
        raise HTTPException(
            status_code=400,
            detail="现场麦没收到声音（检查设备 / 是否静音 / 是否对准）",
        )
    duration_s = len(pcm) / SAMPLE_RATE
    if duration_s < MIN_ENROLL_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=f"音频时长 {duration_s:.1f}s 太短（最短 {MIN_ENROLL_SECONDS}s）",
        )

    loop = asyncio.get_running_loop()
    try:
        embedding = await loop.run_in_executor(None, engine.extract_embedding, pcm)
    except Exception as exc:
        logger.exception("enrollment 提取 embedding 失败 speaker_id=%d", speaker_id)
        raise HTTPException(status_code=500, detail=f"embedding 提取失败: {exc}")

    if embedding is None:
        raise HTTPException(status_code=500, detail="embedding 模型未能返回结果")

    dal.update_speaker_embedding(
        speaker_id=speaker_id,
        embedding_blob=embedding.astype(np.float32).tobytes(),
        sample_count=1,
    )
    logger.info(
        "说话人 %d 声纹已录入（时长=%.1fs, 维度=%d）",
        speaker_id, duration_s, embedding.shape[0],
    )
    return _spk_to_out(dal.get_speaker(speaker_id))  # type: ignore[arg-type]
```

(c) 把现有 `enroll_speaker`（upload 端点）从 `duration_s = len(pcm) / SAMPLE_RATE` 起到 `return _spk_to_out(...)` 结尾整段，替换为：

```python
    return await _finalize_enrollment(dal, engine, speaker_id, pcm)
```

（即保留前面的 `dal`/`engine`/`data`/`_parse_audio_bytes`/空文件检查，把时长校验之后的尾巴交给 `_finalize_enrollment`。）

- [ ] **Step 4: Run tests to verify they pass**

Run: `$PYTEST backend/tests/test_speakers_route.py -v`
Expected: PASS（含新 `test_enroll_silent_400`）

- [ ] **Step 5: Commit**

```bash
git add backend/api/routes/speakers.py backend/tests/test_speakers_route.py
git commit -m "feat(enroll): 抽 _finalize_enrollment 共用尾巴 + 静音守卫"
```

---

### Task 4: enroll start/stop/cancel 端点

现场麦录音三端点，从 `app.state.enroll_recorder` 取录音器。

**Files:**
- Modify: `backend/api/routes/speakers.py`
- Test: `backend/tests/test_speakers_route.py`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_speakers_route.py` 顶部 `_FakeEngine` 之后加假录音器：

```python
from backend.diarization.enroll_recorder import CaptureActiveError, EnrollBusyError


class _FakeRecorder:
    """假录音器：start/stop/abort 记账；stop 返回预置 pcm。"""

    def __init__(self, pcm: np.ndarray | None = None, capture_active: bool = False):
        self._pcm = pcm if pcm is not None else np.full(16000 * 3, 2000, dtype=np.int16)
        self._capture_active = capture_active
        self.running = False
        self.events: list[str] = []

    def start(self):
        if self._capture_active:
            raise CaptureActiveError("capture active")
        if self.running:
            raise EnrollBusyError("busy")
        self.running = True
        self.events.append("start")

    def stop(self):
        self.running = False
        self.events.append("stop")
        return self._pcm

    def abort(self):
        self.running = False
        self.events.append("abort")
```

把 `_client` 改为可注入 recorder：

```python
def _client(tmp_dal: DAL, monkeypatch, engine=None, recorder=None) -> TestClient:
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    app = create_app(create_orchestrator(tmp_dal))
    app.include_router(speakers_router)
    app.state.diarization_engine = engine
    app.state.enroll_recorder = recorder
    return TestClient(app)
```

加端点测试：

```python
def test_enroll_start_then_stop_success(tmp_dal: DAL, monkeypatch):
    engine = _FakeEngine()
    rec = _FakeRecorder()
    with _client(tmp_dal, monkeypatch, engine=engine, recorder=rec) as c:
        sid = c.post("/api/v1/speakers", json={"display_name": "张三"}, headers=_HEADERS).json()["speaker_id"]
        r = c.post(f"/api/v1/speakers/{sid}/enroll/start", headers=_HEADERS)
        assert r.status_code == 202
        assert rec.events == ["start"]
        r = c.post(f"/api/v1/speakers/{sid}/enroll/stop", headers=_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["has_enrollment"] is True
        assert rec.events == ["start", "stop"]
        assert engine.calls == [16000 * 3]


def test_enroll_start_409_when_capture_active(tmp_dal: DAL, monkeypatch):
    rec = _FakeRecorder(capture_active=True)
    with _client(tmp_dal, monkeypatch, engine=_FakeEngine(), recorder=rec) as c:
        sid = c.post("/api/v1/speakers", json={"display_name": "张三"}, headers=_HEADERS).json()["speaker_id"]
        r = c.post(f"/api/v1/speakers/{sid}/enroll/start", headers=_HEADERS)
        assert r.status_code == 409


def test_enroll_start_503_without_recorder(tmp_dal: DAL, monkeypatch):
    with _client(tmp_dal, monkeypatch, engine=_FakeEngine(), recorder=None) as c:
        sid = c.post("/api/v1/speakers", json={"display_name": "张三"}, headers=_HEADERS).json()["speaker_id"]
        r = c.post(f"/api/v1/speakers/{sid}/enroll/start", headers=_HEADERS)
        assert r.status_code == 503


def test_enroll_stop_silent_400_releases_device(tmp_dal: DAL, monkeypatch):
    rec = _FakeRecorder(pcm=np.zeros(16000 * 3, dtype=np.int16))  # 静音
    with _client(tmp_dal, monkeypatch, engine=_FakeEngine(), recorder=rec) as c:
        sid = c.post("/api/v1/speakers", json={"display_name": "张三"}, headers=_HEADERS).json()["speaker_id"]
        r = c.post(f"/api/v1/speakers/{sid}/enroll/stop", headers=_HEADERS)
        assert r.status_code == 400
        assert "stop" in rec.events  # 即使 400 也已 stop 释放设备


def test_enroll_start_missing_speaker_404(tmp_dal: DAL, monkeypatch):
    rec = _FakeRecorder()
    with _client(tmp_dal, monkeypatch, engine=_FakeEngine(), recorder=rec) as c:
        r = c.post("/api/v1/speakers/999/enroll/start", headers=_HEADERS)
        assert r.status_code == 404
        assert rec.events == []  # 不存在的 speaker 不开录音


def test_enroll_cancel_aborts(tmp_dal: DAL, monkeypatch):
    rec = _FakeRecorder()
    with _client(tmp_dal, monkeypatch, engine=_FakeEngine(), recorder=rec) as c:
        sid = c.post("/api/v1/speakers", json={"display_name": "张三"}, headers=_HEADERS).json()["speaker_id"]
        c.post(f"/api/v1/speakers/{sid}/enroll/start", headers=_HEADERS)
        r = c.post(f"/api/v1/speakers/{sid}/enroll/cancel", headers=_HEADERS)
        assert r.status_code == 204
        assert rec.events == ["start", "abort"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$PYTEST backend/tests/test_speakers_route.py -k "enroll_start or enroll_stop or enroll_cancel" -v`
Expected: FAIL with 404/405（端点未定义）

- [ ] **Step 3: Write minimal implementation**

`backend/api/routes/speakers.py`：

(a) 顶部 import 区加：

```python
from backend.diarization.enroll_recorder import CaptureActiveError, EnrollBusyError
```

(b) `_dal` helper 旁加：

```python
def _enroll_recorder(request: Request):
    rec = getattr(request.app.state, "enroll_recorder", None)
    if rec is None:
        raise HTTPException(status_code=503, detail="实时采集未启用，无法用现场麦录声纹")
    return rec
```

(c) 文件末尾（upload 端点之后）加三个端点：

```python
@router.post("/{speaker_id}/enroll/start", status_code=202)
async def enroll_start(
    speaker_id: int,
    request: Request,
    _: str = Depends(require_admin),
):
    """开始用后端现场麦录声纹（与 Capture 同设备）。"""
    dal = _dal(request)
    if dal.get_speaker(speaker_id) is None:
        raise HTTPException(status_code=404, detail="说话人不存在")
    rec = _enroll_recorder(request)
    try:
        rec.start()
    except CaptureActiveError:
        raise HTTPException(status_code=409, detail="正在 Capture，无法同时用现场麦录声纹")
    except EnrollBusyError:
        raise HTTPException(status_code=409, detail="已有一段声纹录音在进行")
    return {"status": "recording", "speaker_id": speaker_id}


@router.post("/{speaker_id}/enroll/stop", response_model=SpeakerOut)
async def enroll_stop(
    speaker_id: int,
    request: Request,
    _: str = Depends(require_admin),
):
    """停止现场麦录音，提声纹存库。无论成败都先 stop 释放设备。"""
    rec = _enroll_recorder(request)
    pcm = rec.stop()  # 总是先释放设备
    dal = _dal(request)
    if dal.get_speaker(speaker_id) is None:
        raise HTTPException(status_code=404, detail="说话人不存在")
    engine = getattr(request.app.state, "diarization_engine", None)
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="diarization 引擎未启用（未设置 SOUNDSPEED_HF_TOKEN）",
        )
    return await _finalize_enrollment(dal, engine, speaker_id, pcm)


@router.post("/{speaker_id}/enroll/cancel", status_code=204)
async def enroll_cancel(
    speaker_id: int,
    request: Request,
    _: str = Depends(require_admin),
):
    """放弃现场麦录音并释放设备（弹窗关闭 / 出错时）。"""
    rec = getattr(request.app.state, "enroll_recorder", None)
    if rec is not None:
        rec.abort()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$PYTEST backend/tests/test_speakers_route.py -v`
Expected: PASS（全部，含 CRUD + upload enroll + start/stop/cancel）

- [ ] **Step 5: Commit**

```bash
git add backend/api/routes/speakers.py backend/tests/test_speakers_route.py
git commit -m "feat(enroll): start/stop/cancel 现场麦录音端点"
```

---

### Task 5: entrypoint 装配（recorder + Capture 优先互斥）

把 TAKE_START/TAKE_END 订阅移进 build_app，wire「abort enroll → start session」；创建 EnrollRecorder 挂 app.state。

**Files:**
- Modify: `backend/api/entrypoint.py`
- Test: `backend/tests/test_entrypoint_enroll_wiring.py`

- [ ] **Step 1: Write the failing test**

新建 `backend/tests/test_entrypoint_enroll_wiring.py`（测可单元化的 take-start handler 工厂：保证 abort 在 start 之前）：

```python
"""take-start handler 顺序：必须先 abort enroll 再 start session（Capture 优先，防设备抢占）。"""
from backend.api.entrypoint import _make_take_start_handler


class _Rec:
    def __init__(self, log): self._log = log
    def abort(self): self._log.append("abort")


class _Session:
    def __init__(self, log): self._log = log
    def start(self): self._log.append("start")


def test_take_start_aborts_enroll_before_session_start():
    log: list[str] = []
    handler = _make_take_start_handler(_Session(log), _Rec(log))
    handler(None)
    assert log == ["abort", "start"]


def test_take_start_handler_without_recorder():
    log: list[str] = []
    handler = _make_take_start_handler(_Session(log), None)
    handler(None)
    assert log == ["start"]  # 无 recorder 时只 start，不崩
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PYTEST backend/tests/test_entrypoint_enroll_wiring.py -v`
Expected: FAIL with `ImportError: cannot import name '_make_take_start_handler'`

- [ ] **Step 3: Write minimal implementation**

`backend/api/entrypoint.py`：

(a) 在 `_maybe_wire_live_asr` 里**删除**这两行（约 217-218）：

```python
    orchestrator.subscribe(TAKE_START, lambda _p: session.start())
    orchestrator.subscribe(TAKE_END, lambda _p: session.stop())
```

注意：该函数顶部 `from backend.core.events import TAKE_END, TAKE_START` 这个 import 现在没用了，删掉它（移到 build_app 用）。

(b) 模块级（`build_app` 之前）加 handler 工厂：

```python
def _make_take_start_handler(live_asr, enroll_recorder):
    """TAKE_START 处理器：Capture 优先 —— 先 abort 任何进行中的 enroll 录音（释放
    设备），再 start live ASR。单 handler 保证顺序，不依赖事件订阅次序。"""
    def handler(_payload):
        if enroll_recorder is not None:
            enroll_recorder.abort()
        live_asr.start()
    return handler
```

(c) `build_app` 里，`diarization_engine = _maybe_wire_diarization(...)`（约 84 行）之后、`app.state.live_asr = live_asr`（约 87 行）之前，插入：

```python
    enroll_recorder = None
    if live_asr is not None:
        from backend.core.events import TAKE_END, TAKE_START
        from backend.diarization.enroll_recorder import EnrollRecorder

        enroll_recorder = EnrollRecorder(
            make_source=live_asr.make_source,
            is_capture_active=lambda: live_asr.running,
        )
        orchestrator.subscribe(TAKE_START, _make_take_start_handler(live_asr, enroll_recorder))
        orchestrator.subscribe(TAKE_END, lambda _p: live_asr.stop())
```

(d) 在 `app.state.diarization_engine = diarization_engine` 之后加：

```python
    app.state.enroll_recorder = enroll_recorder  # None 表示实时采集未启用
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$PYTEST backend/tests/test_entrypoint_enroll_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Run full backend suite (no regression)**

Run: `$PYTEST backend/tests -q`
Expected: 全绿（原 659 + 新增；0 failures）。重点确认 take start/end 集成测试仍过（订阅已移到 build_app，行为不变）。

- [ ] **Step 6: Commit**

```bash
git add backend/api/entrypoint.py backend/tests/test_entrypoint_enroll_wiring.py
git commit -m "feat(enroll): build_app 装配 EnrollRecorder + Capture 优先 take-start handler"
```

---

### Task 6: 前端弹窗改走后端录音

`api.ts` 加三个调用；`EnrollRecorderDialog.tsx` 从浏览器录音改为后端遥控。

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/admin/EnrollRecorderDialog.tsx`

- [ ] **Step 1: api.ts 加 postNoBody 辅助 + 三个 enroll 调用**

`frontend/src/lib/api.ts` 的 `requestMultipart` 之后加：

```typescript
// 无 body 的 POST（enroll start/stop/cancel）：带 token，错误体解析 detail → ApiError，
// 让弹窗能显示「正在 Capture」「没收到声音」等后端原因。
async function postNoBody<T>(path: string): Promise<T> {
  const token = useSessionStore.getState().token
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) {
    let detail = `POST ${path} → ${res.status}`
    try {
      const j = await res.json()
      if (j?.detail) detail = String(j.detail)
    } catch {
      /* 忽略非 JSON 错误体 */
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}
```

`enrollSpeaker` 之后加：

```typescript
// 现场麦录声纹（后端设备，与 Capture 同源）。start → stop 提声纹存库 → 返回 SpeakerDTO；
// cancel 放弃并释放设备。enrollSpeaker（上传）保留作内部原语。
export function enrollStart(speakerId: number): Promise<{ status: string; speaker_id: number }> {
  return postNoBody(`/api/v1/speakers/${speakerId}/enroll/start`)
}

export function enrollStop(speakerId: number): Promise<SpeakerDTO> {
  return postNoBody<SpeakerDTO>(`/api/v1/speakers/${speakerId}/enroll/stop`)
}

export function enrollCancel(speakerId: number): Promise<void> {
  return postNoBody<void>(`/api/v1/speakers/${speakerId}/enroll/cancel`)
}
```

- [ ] **Step 2: 重写 EnrollRecorderDialog.tsx 为后端遥控**

整文件替换 `frontend/src/components/admin/EnrollRecorderDialog.tsx` 为：

```tsx
import { useEffect, useRef, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { enrollStart, enrollStop, enrollCancel } from "@/lib/api"
import type { SpeakerDTO } from "@/types/api"
import { Loader2, Mic, Square } from "lucide-react"

type Phase = "idle" | "recording" | "saving" | "done" | "error"

// 录声纹时照着念的样例对白（内容中性，约 15–25 秒，音素覆盖均衡）。
const SAMPLE_SCRIPT =
  "大家好，我现在正在录制我的声音样本。这段话没有特别的含义，" +
  "只是为了让系统记住我说话的声音和语调。我会用平时聊天的语气，" +
  "自然地把这几句话念完。今天天气不错，适合出门走走，" +
  "也适合安静地待在房间里看书。好，就到这里，谢谢。"

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  speaker: SpeakerDTO | null
  onEnrolled: () => void
}

// 录制声纹：后端现场麦录音（与 Capture 同设备）→ stop 提声纹存库（覆盖该演员旧声纹）。
// 浏览器麦只用于 take note，不在此使用。
export default function EnrollRecorderDialog({ open, onOpenChange, speaker, onEnrolled }: Props) {
  const [phase, setPhase] = useState<Phase>("idle")
  const [elapsed, setElapsed] = useState(0)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const recordingRef = useRef(false) // 卸载时判断是否需要 cancel 释放后端设备
  const speakerRef = useRef<SpeakerDTO | null>(speaker)
  speakerRef.current = speaker

  const clearTimer = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }

  // 卸载时若仍在录音，cancel 释放后端设备。父组件用 key 在每次打开时重挂，状态从 idle 复位。
  useEffect(() => {
    return () => {
      clearTimer()
      if (recordingRef.current && speakerRef.current) {
        void enrollCancel(speakerRef.current.speaker_id)
      }
    }
  }, [])

  const startRecording = async () => {
    if (!speaker) return
    setErrorMsg(null)
    try {
      await enrollStart(speaker.speaker_id)
      recordingRef.current = true
      setPhase("recording")
      setElapsed(0)
      timerRef.current = setInterval(() => setElapsed((e) => e + 0.2), 200)
    } catch (e) {
      setPhase("error")
      setErrorMsg(e instanceof Error ? e.message : "无法启动现场麦录音")
    }
  }

  const stopRecording = async () => {
    clearTimer()
    if (!speaker) return
    recordingRef.current = false
    setPhase("saving")
    try {
      await enrollStop(speaker.speaker_id)
      setPhase("done")
      onEnrolled()
    } catch (e) {
      setPhase("error")
      setErrorMsg(e instanceof Error ? e.message : "声纹录入失败")
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>录制声纹{speaker ? ` · ${speaker.display_name}` : ""}</DialogTitle>
          <DialogDescription>
            对着<b>现场麦克风</b>念下面这段话，自然语气即可（建议 15–30 秒，<b>请勿超过 30 秒</b>）。
            停止后自动保存，覆盖该演员旧声纹。
          </DialogDescription>
        </DialogHeader>

        {(phase === "idle" || phase === "recording") && (
          <div className="rounded-xl bg-muted/60 px-4 py-3 text-sm leading-relaxed text-foreground max-h-40 overflow-y-auto">
            {SAMPLE_SCRIPT}
          </div>
        )}

        <div className="flex flex-col items-center gap-4 py-4">
          {phase === "recording" ? (
            <>
              <div
                className={
                  "flex items-center gap-2 " +
                  (elapsed > 30 ? "text-amber-600" : "text-destructive")
                }
              >
                <span
                  className={
                    "size-2.5 rounded-full animate-pulse " +
                    (elapsed > 30 ? "bg-amber-500" : "bg-destructive")
                  }
                />
                <span className="font-mono text-2xl tabular-nums">{elapsed.toFixed(1)}s</span>
              </div>
              <span className="text-xs text-muted-foreground">正在通过现场麦录音…</span>
              {elapsed > 30 && (
                <span className="text-xs text-amber-600">已超过 30 秒，建议停止</span>
              )}
              <Button variant="destructive" size="lg" className="gap-2 rounded-full" onClick={() => void stopRecording()}>
                <Square className="size-4" />停止并保存
              </Button>
            </>
          ) : phase === "saving" ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />正在提取声纹…
            </div>
          ) : phase === "done" ? (
            <>
              <div className="text-sm text-green-600">声纹已录入 ✓</div>
              <Button variant="secondary" onClick={() => onOpenChange(false)}>完成</Button>
            </>
          ) : (
            <>
              <Button variant="default" size="lg" className="gap-2 rounded-full" onClick={() => void startRecording()}>
                <Mic className="size-5" />开始录制
              </Button>
              {errorMsg && <div className="text-xs text-destructive text-center">{errorMsg}</div>}
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 3: Typecheck**

Run: `pnpm -C frontend exec tsc -b`
（若报缺依赖：先 `pnpm -C frontend install`。`tsc -b` 是 build 脚本用的同款类型检查。）
Expected: 无类型错误。`blobToWav16kMono`/`MediaRecorder` 引用已从本文件移除；`lib/wav.ts` 仍被 `useVoiceRecorder` 引用，保留。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/components/admin/EnrollRecorderDialog.tsx
git commit -m "feat(enroll): 前端弹窗改走后端现场麦录音（start/stop/cancel）"
```

---

### Task 7: 全量验证 + 收尾

**Files:** 无新增，仅验证。

- [ ] **Step 1: 后端全量**

Run: `$PYTEST backend/tests -q`
Expected: 全绿，0 failures（基线 659 + 本次新增用例）。

- [ ] **Step 2: 前端构建**

Run: `pnpm -C frontend build`
Expected: 构建通过（tsc + vite）。

- [ ] **Step 3: 手动验证（需真设备 + HF token）**

1. 起后端（设 `SOUNDSPEED_HF_TOKEN`）+ 前端 dev。
2. /admin → 说话人台账 → 某演员「录制声纹」。
3. 点「开始录制」→ 确认前端无浏览器麦克风权限弹窗（不再用 getUserMedia），显示「正在通过现场麦录音」。
4. 对现场麦念样例 → 「停止并保存」→ 台账该演员 `has_enrollment` 变真。
5. 录音中关弹窗 → 后端日志确认 enroll abort、设备释放。
6. 正在 Capture 时点录声纹「开始」→ 弹窗显示「正在 Capture，无法同时…」（409）。
7. 麦静音/拔掉时录 → 停止后显示「现场麦没收到声音…」（400）。

- [ ] **Step 4: 用 verify skill 跑真实验证**

按需调用 `/verify` 或 run skill 起 app 实测上面的手动清单。

---

## Self-Review

**1. Spec coverage：**
- §4.1 EnrollRecorder → Task 2 ✓
- §4.2 双向互斥（start 拒绝 / TAKE_START abort）→ Task 2（start 守卫）+ Task 5（take-start handler）✓
- §4.3 make_source → Task 1 ✓
- §4.4 装配 app.state → Task 5 ✓
- §5 端点 start/stop/cancel + 保留 upload → Task 4 ✓
- §5.1 _finalize_enrollment 共用尾巴 → Task 3 ✓
- §5.3 静音守卫 → Task 3 ✓
- §5.4 60s cap → Task 2（test_max_seconds_caps_buffer）✓
- §6 数据流 → Task 4 + Task 6 ✓
- §7 前端 → Task 6 ✓
- §8 测试 → 各 Task 的 TDD 步骤 ✓

**2. Placeholder scan：** 无 TBD/TODO；每个改码步骤都有完整代码块。

**3. Type consistency：**
- `EnrollRecorder(make_source, is_capture_active, max_seconds, sample_rate)` 在 Task 2 定义，Task 5 用 `make_source=`/`is_capture_active=` 关键字调用 ✓
- `CaptureActiveError`/`EnrollBusyError` 在 Task 2 定义，Task 4 路由 import 并 catch ✓
- `_finalize_enrollment(dal, engine, speaker_id, pcm)` 在 Task 3 定义，Task 4 stop 端点调用 ✓
- `make_source()` 无参（Task 1）；`EnrollRecorder` 调 `self._make_source()` 无参（Task 2）；entrypoint 传 `live_asr.make_source`（Task 5）✓
- `enrollStart/enrollStop/enrollCancel`（Task 6 api.ts）与组件调用一致 ✓
- 后端 stop 端点 `response_model=SpeakerOut`，前端 `enrollStop` 返回 `SpeakerDTO` ✓

**注意事项（实现者必读）：**
- Task 3 改动会让旧 `test_enroll_success_sets_embedding` 的全静音 PCM 失败 —— 已在 Task 3 Step 1 把它和 `test_enroll_too_short_400` 改成 audible PCM，并新增 `test_enroll_silent_400`。务必同步改，否则回归。
- Task 5 删除 `_maybe_wire_live_asr` 内的 TAKE_START/TAKE_END 订阅后，行为搬到 build_app；`from backend.core.events import TAKE_END, TAKE_START` 这个 import（约第 127 行，函数内局部 import）在该函数里变成未用，一并删掉保持整洁。注：`test_import_hygiene.py` 只用子进程查冷启动循环 import（`backend.llm.config`/`service`），**不**检查未用 import，所以漏删不会让测试失败，但仍应删。
