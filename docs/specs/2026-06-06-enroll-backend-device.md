# 声纹录入改走后端现场麦（enroll → backend device）

日期：2026-06-06
分支：`worktree-fix+speaker-ledger`
相关：[2026-06-02-realtime-diarization-voicenote-design.md](2026-06-02-realtime-diarization-voicenote-design.md)、[2026-06-02-segment-speaker-correction.md](2026-06-02-segment-speaker-correction.md)、[2026-06-04-audio-input-device-persist-design.md](2026-06-04-audio-input-device-persist-design.md)

## 1. 问题

说话人台账录声纹（`录制声纹 · 演员名` 弹窗）目前走**前端浏览器麦**：

```
EnrollRecorderDialog.tsx → navigator.mediaDevices.getUserMedia()
  → MediaRecorder(webm/opus) → blobToWav16kMono(OfflineAudioContext 重采样)
  → POST /api/v1/speakers/{id}/enroll (multipart) → engine.extract_embedding → 存库
```

而 Capture（正式录 take）走**后端 sounddevice 设备**：

```
Capture 按钮 → POST /api/v1/take/start → TAKE_START 事件
  → LiveAsrSession.start() → DeviceSource(sounddevice.InputStream 原始 PCM)
  → ChannelProcessor 重采样 16kHz int16 → TakeAudioBuffer → diarization 回填
```

两条链有两层 domain gap：

1. **物理麦不同**：enroll 是操作 admin 页那台设备的浏览器麦；Capture 是后端机器接的现场麦/声卡。
2. **编解码 + 重采样链不同**：enroll 走 MediaRecorder(opus 有损) → decodeAudioData → OfflineAudioContext 重采样；Capture 走原始 PCM → `ChannelProcessor`。

匹配机制（`backend/diarization/registry.py`）：每个 take 的 per-speaker embedding 与**已注册声纹** embedding 做 cosine，`>= threshold`（默认 0.5）判命中。enroll 与 take 落在不同声学域 → cosine 偏低 → 在 0.5 阈值下漏匹配。

`engine.py:38-40` 已明确写了 diarize 与 enroll 必须同一 pipeline、同一 embedding 空间。把 enroll 迁到后端设备，能同时消掉「物理麦」和「编解码/重采样」两层 gap，是 input-path 上的根因修复。

## 2. 目标 / 非目标

**目标**：声纹录入走和 Capture **同一支后端设备、同一条 PCM → ChannelProcessor 链**。

**非目标**：
- cosine 阈值 0.5 调参（另案，本次只消 input-path 的 domain gap）。
- voice note 仍走浏览器麦（`useVoiceRecorder` / `useMicLevel` 不动）。

## 3. 现状确认（代码事实）

- `backend/audio/source.py`：`AudioSource.__next__` 产出的 `AudioChunk.channels[i]` 已是**16kHz 单声道 int16**（`ChannelProcessor(native_rate)` 内部重采样到 `OUTPUT_SAMPLE_RATE`）。
- `backend/asr/live_session.py`：take 用 `process_channels=(0,)`，diarization 回填消费的就是 ch0。
- `backend/diarization/engine.py:173 extract_embedding(pcm_int16)`：吃 int16 16kHz 单声道；`<1s` 返回 None，`<2s` 仅 warning。**只查时长，不查能量。**
- `backend/api/entrypoint.py:145 _source_factory(device)`：闭包，按持久化设置 / `SOUNDSPEED_AUDIO_DEVICE` / 系统默认解析设备，`open_device_with_fallback` 探测后构造 `DeviceSource`。Capture 与 enroll 必须共用这套解析。
- 前端浏览器麦三处用法：`EnrollRecorderDialog.tsx`（迁走）、`useVoiceRecorder.ts`（voice note，保留）、`useMicLevel.ts`（电平条，保留）。

## 4. 架构

### 4.1 `EnrollRecorder`（新增 `backend/diarization/enroll_recorder.py`）

`LiveAsrSession` 的轻量兄弟，跟 engine/registry/backfill 聚在一起：

- `start()`：起 daemon 线程，迭代一个 `DeviceSource`，把 `chunk.channels[0]` 追加进内部 buffer（list[np.ndarray]）。**锁死 channel 0**，与 take 的 `process_channels=(0,)` 一致。不跑 VAD、不跑 Whisper，纯采集。
- `stop() -> np.ndarray`：置停止标志、join 线程、拼接返回 int16 buffer。
- `abort()`：丢弃 buffer、释放设备、不返回。
- 注入点：
  - `make_source: Callable[[], AudioSource]` —— 由 `LiveAsrSession.make_source()` 提供（见 4.3），测试注入假源避免 PortAudio。
  - `is_capture_active: Callable[[], bool]` = `lambda: session.running`，用于互斥判断。
  - `max_seconds: float = 60.0` —— 安全上限（见 5.2）。
- 线程模型与 `LiveAsrSession` 对齐：`threading.Lock` 守 start/stop、daemon 线程、`running` 属性。`stop`/`abort` 对未运行安全 no-op。
- 异常：源打不开（`DeviceError`）在 `start()` 同步路径抛出，由路由转 503；采集中途失败线程内 log（buffer 已有的部分仍可用，但若几乎为空交给静音守卫拦）。

### 4.2 设备互斥（双向）

同一物理设备不允许两个 `InputStream` 并存（跨平台不保证可并存，一律互斥）：

- **enroll/start**：若 `is_capture_active()` 为真（take 正在 Capture）→ 路由返回 **409**，不开流。
- **TAKE_START 反向**：`TAKE_START` 处理器在 `session.start()` 之前先 `enroll_recorder.abort()`。规则：**Capture 优先，任何进行中的 enroll 让位**。改 entrypoint 里 `orchestrator.subscribe(TAKE_START, ...)` 的 lambda，先 abort enroll 再 start session。

不引入额外 lease 抽象；两个单点检查 + 一条「Capture 优先」规则即可覆盖 on-set 实际场景（不会在录 take 时开声纹弹窗录音）。

### 4.3 `LiveAsrSession.make_source()`

`_source_factory` 现在是 entrypoint 里的闭包。新增方法：

```python
def make_source(self) -> AudioSource:
    """按当前设备构造一个 AudioSource（与 take 同设备，跟随 set_device）。"""
    return self._source_factory(self._device)
```

`EnrollRecorder` 注入 `session.make_source`，于是 enroll 永远跟 Capture 同设备，运行时 `set_device` 切换也跟得上。

### 4.4 装配

entrypoint 在 session + diarization engine 接好后创建 `EnrollRecorder(make_source=session.make_source, is_capture_active=lambda: session.running)`，挂 `app.state.enroll_recorder`。路由从 `app.state` 取。engine 为 None（无 HF token）时 enroll 路由直接 503，与现有 upload 端点一致。

## 5. 端点

```
POST /api/v1/speakers/{id}/enroll/start    开始后端录音
POST /api/v1/speakers/{id}/enroll/stop     停止 → 提声纹 → 存库 → SpeakerOut
POST /api/v1/speakers/{id}/enroll/cancel   放弃并释放设备（弹窗关闭/出错）
POST /api/v1/speakers/{id}/enroll          保留：multipart 上传（测试/批量导入原语）
```

### 5.1 共用尾巴 `_finalize_enrollment`

把现有 upload handler 的尾巴抽成共用函数，upload 和 stop 两条都调它：

```python
def _finalize_enrollment(dal, engine, speaker_id, pcm: np.ndarray) -> SpeakerOut:
    # 1. 静音/能量守卫（见 5.3）—— 不过则 raise → 400
    # 2. 时长守卫（< MIN_ENROLL_SECONDS）—— 400
    # 3. engine.extract_embedding(pcm) —— None 则 500
    # 4. dal.update_speaker_embedding(blob, sample_count=1)
    # 5. 返回 _spk_to_out(dal.get_speaker(speaker_id))
```

（`run_in_executor` 包 `extract_embedding` 维持现状，避免阻塞 event loop。）

### 5.2 状态码

| 场景 | 码 |
|---|---|
| take 正在 Capture（互斥） | 409 |
| 已在录（重复 start） | 409 |
| 无 diarization engine | 503 |
| 设备打不开（DeviceError） | 503（带可用设备列表） |
| 静音 / 太短 | 400 |
| extract_embedding 返回 None | 500 |
| speaker 不存在 | 404 |

### 5.3 静音守卫（新增）

后端麦用户听不到，麦克静音/没插/没对准会录到一片静音，等匹配失败才发现。`_finalize_enrollment` 在时长校验后加能量校验：算 buffer 的 RMS（或 peak），低于阈值 → 400「现场麦没收到声音（检查设备 / 是否静音 / 是否对准）」。阈值取保守值（如 int16 RMS < ~50，约 -56 dBFS），只拦近乎纯静音，不误伤小声。这是迁移的直接产物——浏览器原来用 getUserMedia 隐式告诉用户麦在工作，迁后端后这个隐式信号没了。

### 5.4 最大时长自动收尾

`EnrollRecorder` 录到 `max_seconds`（60s）自动停止追加、标记 capped、释放设备。防 start 不 stop（tab 关、网断）线程永久占设备。stop 端点拿到的是截断 buffer，正常 finalize。

## 6. 数据流

```
按「开始录制」
  → POST enroll/start
  → 互斥检查（capture active? already running?）→ EnrollRecorder.start()
  → daemon 线程开 DeviceSource（与 Capture 同设备）→ 追加 chunk.channels[0]
  → 前端本地 elapsed 计时器（纯展示）+「正在通过现场麦录音」
按「停止」
  → POST enroll/stop
  → recorder.stop() 返回 int16 buffer
  → _finalize_enrollment：静音守卫 → 时长守卫 → extract_embedding → 存库
  → SpeakerOut → 弹窗「声纹已录入 ✓」
弹窗在 recording 态被关 → POST enroll/cancel → recorder.abort() 释放设备
```

## 7. 前端改动（`EnrollRecorderDialog.tsx`）

- 删 `getUserMedia` / `MediaRecorder` / `blobToWav16kMono` 那套。
- 换成 `enrollStart(id)` / `enrollStop(id) -> SpeakerDTO` / `enrollCancel(id)` 三个 API（`lib/api.ts` 新增）。
- 保留本地计时器与 idle/recording/saving/done/error 状态机。
- 文案：「对着麦克风」→「对着**现场麦克风**」，recording 态加「正在通过现场麦录音」。
- 弹窗在 recording 态卸载/关闭 → 调 `enrollCancel` 释放设备。
- `lib/wav.ts` **保留**（`useVoiceRecorder` 仍用同款 16k 编码）。

## 8. 测试（TDD）

**后端单测（`EnrollRecorder`，注入假 source 不碰 PortAudio）**：
- start/stop 累积 channels[0]，拼接长度/内容正确。
- finalize：假 engine 提 embedding、存库、`sample_count=1`。
- 静音守卫：全静音 buffer → 400。
- 时长守卫：< 2s → 400。
- 60s cap：假源喂超长 → 截断到 ~60s。
- 互斥：`is_capture_active()` 真 → start 拒绝。
- abort：释放后 `running` 为假，buffer 清空。

**路由测（假 recorder + 假 engine）**：
- start/stop happy path → 200 + SpeakerOut（`has_enrollment=True`）。
- take 占用 → 409。
- 静音 / 太短 → 400。
- 无 engine → 503。
- cancel → 释放。
- 现有 upload enroll 测试保持绿（共用 `_finalize_enrollment`）。

**前端**：手测为主——弹窗 → 后端录音 → 存库 → 台账列表 `has_enrollment` 刷新；recording 态关弹窗设备释放。

## 9. 考虑过但否掉的方案

- **复用正在跑的 take 流**：enroll 通常在 take 之间做，不是录 take 时，场景不匹配。
- **固定时长录制**：放弃「念完自己停」手感；已选 start/stop。

## 10. 影响面 / 改动清单

| 文件 | 改动 |
|---|---|
| `backend/diarization/enroll_recorder.py` | 新增 `EnrollRecorder` |
| `backend/asr/live_session.py` | 新增 `make_source()` |
| `backend/api/routes/speakers.py` | 新增 start/stop/cancel 端点；抽 `_finalize_enrollment`；静音守卫 |
| `backend/api/entrypoint.py` | 装配 `EnrollRecorder` 挂 app.state；`TAKE_START` 先 abort enroll |
| `frontend/src/components/admin/EnrollRecorderDialog.tsx` | 改走后端录音 API |
| `frontend/src/lib/api.ts` | 新增 `enrollStart/enrollStop/enrollCancel` |
| `backend/tests/` | 新增 enroll_recorder + 路由测试 |
