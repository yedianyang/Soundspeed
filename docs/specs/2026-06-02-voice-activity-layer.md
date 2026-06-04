# Spec: Voice Activity Layer（VAD 端点切段）

版本：v0.1（草稿，待 Lead 评审）
日期：2026-06-02
状态：草稿
owner：经纬

变更记录：

- v0.1（2026-06-02）：初稿。补 audio-input-layer v0.3 §11 留下的"独立下游层，另立 spec"窟窿。为实时（段级流式）链路定 `SpeechSegment` 结构、`ChannelVADSegmenter` 接口、端点状态机、silero-vad 选型与可测性边界。

依赖 spec（按权威级别排序）：

1. audio-input-layer v0.3（`docs/specs/2026-05-20-audio-input-layer.md`）
2. asr-service v0.1（`docs/specs/2026-05-28-asr-service.md`）
3. development-plan v0.2（`docs/specs/2026-05-27-development-plan.md`）

覆盖范围：`AudioChunk` 流 → 每声道独立 VAD 端点检测 → 切出 `SpeechSegment`（带 pre/post-roll、绝对帧标注）。这是 audio-input-layer 的直接下游、ASRService 的直接上游。

不覆盖：ASRService 内部的 pyannote 细粒度多说话人切窗（asr-service v0.1 §4，turn 内细分）、enrollment、whisper 推理。VAD 层只做粗粒度"说话 vs 静音"端点。

---

## 1. 背景与职责边界

audio-input-layer v0.3 把 VAD 明确推给"独立下游层，另立 spec"（§11），但那份 spec 一直没写。在批处理形态下可以把整段音频直接喂 pyannote（asr-service §4 把 VAD 折叠进 pyannote），但**实时（段级流式）形态下需要一个轻量端点检测器**来决定"何时一个 turn 说完了、可以触发重型 pyannote+whisper pipeline"，否则只能不停空跑 pyannote。本 spec 补这个层。

**两级切分分工（重要）**：

| 层 | 粒度 | 职责 | 模型 |
|---|---|---|---|
| **Voice Activity Layer（本 spec）** | 粗 | 说话 vs 静音，按静音收尾切 turn | silero-vad（轻量） |
| ASRService 内 pyannote（asr-service §4） | 细 | turn 内多说话人切窗、保 overlap | pyannote 3.1（重） |

VAD 层**不区分说话人**。它把连续语音按静音端点切成 `SpeechSegment`，往下游吐；ch1 链路再用 pyannote 在 segment 内做细分。

**批/实时统一**：audio-input-layer 的 `FileSource`（批）与 `DeviceSource`（实时）在 `AudioChunk` 这一层完全同构。VAD 层做成"逐 chunk 喂入、收尾时吐 segment"的有状态流式组件，**同一份代码批处理和实时都能用**——批处理只是喂得快。

---

## 2. 数据结构：SpeechSegment

```python
@dataclass(frozen=True)
class SpeechSegment:
    """VAD 层 → ASRService 的输入（asr-service §8 transcribe_speech_segment 的 segment 参数）。"""
    ch: int                # 声道号，来自 AudioChunk.channels 的索引（0-based）
    audio: np.ndarray      # 16kHz 单声道 int16，含 pre/post-roll
    start_frame: int       # 16kHz 绝对帧（含 pre-roll 起点），与 AudioChunk.start_frame 同基
    end_frame: int         # 16kHz 绝对帧（含 post-roll 终点）；end_frame > start_frame
```

注意单位是 **16kHz 帧**（与 AudioChunk 一致），不是毫秒。publisher（1.C）在转 contract C1 时再做 `帧 → 毫秒` 换算（asr-publisher-contract §3.2，毫秒 = 帧 / 16 × ... 由 publisher 定），VAD 层不碰毫秒。

---

## 3. 接口：ChannelVADSegmenter

每声道一个实例，持有 VAD 探测器状态 + speech buffer + pre-roll 环形缓冲。

```python
class ChannelVADSegmenter:
    def __init__(self, ch: int, detector: VadDetector, config: VadConfig) -> None: ...

    def push(self, audio: np.ndarray, start_frame: int) -> list[SpeechSegment]:
        """喂一块 16kHz int16 单声道（通常来自 AudioChunk.channels[ch]）。
        返回本次喂入后**已收尾**的 SpeechSegment 列表（可能为空、可能多个）。
        start_frame 为这块音频首帧的绝对 16kHz 帧位置（= AudioChunk.start_frame）。
        """

    def flush(self) -> list[SpeechSegment]:
        """流结束时调用一次。排出仍在进行中的 speech buffer（若时长达标则成段）。"""
```

**驱动循环**（FileSource / DeviceSource 通用）：

```python
segmenters = [ChannelVADSegmenter(ch=i, detector=SileroVad(), config=cfg)
              for i in range(n_channels)]
with source as src:
    for chunk in src:
        for i, ch_audio in enumerate(chunk.channels):
            for seg in segmenters[i].push(ch_audio, chunk.start_frame):
                handle(seg)      # → ASRService.transcribe_speech_segment
for sm in segmenters:
    for seg in sm.flush():
        handle(seg)
```

---

## 4. 探测器抽象：VadDetector（可测性边界）

切段状态机（buffering / 端点）是确定性纯逻辑；VAD 模型是带状态、需加载权重的外部依赖。两者解耦：

```python
class VadDetector(Protocol):
    def speech_prob(self, frame: np.ndarray) -> float:
        """输入一个固定长度 16kHz int16 帧，返回语音概率 [0,1]。"""
    def reset(self) -> None:
        """重置内部状态（一段流开始/结束时调用）。"""
```

- **生产**：`SileroVad(VadDetector)` —— 包 silero-vad onnx session，帧长 512 样本（32ms @ 16k，silero v5 硬要求）。薄封装，留 smoke / `[手动测试]`。
- **测试**：`_AmplitudeVad`（fixture 内）—— 按帧 RMS 是否超阈值返回 1.0/0.0，确定性、零依赖、零下载。状态机所有分支都用它测。

这与 audio-input-layer 的哲学一致（DeviceSource 纯逻辑用合成数据测，真实 InputStream 层手动测）。

---

## 5. 端点状态机

每声道两态：`SILENCE` / `SPEECH`。输入按 `frame_samples`（512）切帧逐帧判定（跨 push 调用的不足一帧的尾巴留 buffer）。

- **SILENCE 态**：持续把帧填进 pre-roll 环形缓冲（容量 `pre_roll_frames`）。某帧 `speech_prob ≥ threshold` → 转 SPEECH：开新 segment buffer，**预置 pre-roll 内容**，记 `start_frame = 当前帧绝对位置 − pre-roll 长度`，清尾静音计数。
- **SPEECH 态**：帧追加进 segment buffer。语音帧 → 清尾静音计数；静音帧 → 尾静音计数累加，达 `min_silence_frames` → **收尾**：附 `post_roll` 后定 `end_frame`，若 `时长 ≥ min_speech_frames` 则成段 emit（否则丢弃），转 SILENCE。segment buffer 长度 ≥ `max_segment_frames` → **强切** emit，立即续开新 segment（仍 SPEECH，防长独白把 buffer 撑爆 / 超 whisper 上限）。
- **flush()**：若处于 SPEECH 态且时长达标，按当前位置收尾 emit。

**绝对帧记账**：segmenter 内部维护游标 `_abs_pos`（下一个待消费样本的绝对帧），首次 push 用传入 `start_frame` 初始化，之后按消费样本数推进；连续 chunk 的 start_frame 应与游标对齐（不对齐记 warning，按内部游标为准）。

---

## 6. 配置：VadConfig（默认值）

```python
@dataclass(frozen=True)
class VadConfig:
    frame_samples: int = 512           # silero v5 @ 16k 硬要求，不要改
    threshold: float = 0.5             # speech_prob 阈值
    min_silence_ms: int = 600          # 静音多久算 turn 收尾
    min_speech_ms: int = 250           # 短于此的语音段丢弃（噪声/咔哒）
    pre_roll_ms: int = 200             # 段首回补，防吃掉起音
    post_roll_ms: int = 200            # 段尾延伸，防切掉收音
    max_segment_ms: int = 30000        # 超长强切（whisper 单次上限 + buffer 保护）
```

ms → 帧换算用 16kHz（`frames = ms * 16`）。`min_silence_ms` 直接决定 turn 收尾延迟，是"实时手感"的主旋钮；现场如多自然停顿误切，调大到 800。

---

## 7. 跨平台与依赖

运行环境：Python **3.12**（与 asr-service §11.1 锁版矩阵一致；torch 2.5.1+cu124 / onnxruntime / whisperx 在 cp312 wheel 齐全）。

| 包 | 用途 | 备注 |
|---|---|---|
| onnxruntime | silero onnx 推理 | cp312 wheel 齐全 |
| silero-vad | 神经 VAD 模型封装 | pip 包**硬依赖 `torch>=1.12.0`**（即便 onnx=True 也照拽）。在已装 torch 2.5.1 的 ASR venv 里，该约束被满足、不会升级 torch；但在 torch-free 的裸 venv 里会拉最新 torch |

**venv 归属**：

- 切段状态机（`segmenter.py` / `models.py`）零重依赖（纯 numpy），放主 backend venv。
- **实现回写（2026-06-03）**：项目最终为**单一 backend venv**（Python 3.12），torch（2.11.0+cu128，随 pyannote.audio 4.0 一并装）已在主 venv，故 `silero-vad==6.2.1` + `onnxruntime==1.26.0` 直接进 `backend/requirements.txt` 与切段状态机同栈，不再分 venv（v0.1 此处"不要加进主 requirements"的约束因 venv 合并而作废）。
- torch-free 主 venv 方案（onnxruntime 直驱 silero `.onnx`）不再需要，作废。

模型权重随 silero-vad 包内置，无需 HF 下载。

**放置位置**：新建 `backend/vad/` 包（自成一层，与 audio-input-layer 的"独立下游层"定位一致；与重 ASR 栈解耦）。dev-plan v0.2 §6 1.A 子任务里把 `vad.py` 暂列在 `backend/asr/` 下，本 spec 偏离为独立包 `backend/vad/`——dev-plan 的路径本就是"暂定"，此处定死。

---

## 8. 文件布局

```
backend/vad/
  __init__.py
  models.py        # SpeechSegment, VadConfig
  detector.py      # VadDetector Protocol + SileroVad 封装
  segmenter.py     # ChannelVADSegmenter（纯逻辑状态机）
backend/tests/
  test_vad_segmenter.py    # 状态机全分支，用 _AmplitudeVad
```

`SileroVad` 的真实模型 smoke 测试留 `[手动测试]`（喂 0.G 的 test_long.wav，肉眼核 segment 数与边界），对应 commit 标记，GitHub issue 记人工验证步骤——与 audio-input-layer DeviceSource 手动测试惯例一致。

---

## 9. 测试矩阵（TDD 红先行）

| 测试用例 | 验证行为 |
|---|---|
| `test_single_burst_one_segment` | 静音-语音-静音 → 1 段，`end_frame > start_frame` |
| `test_two_bursts_long_gap_two_segments` | 两段语音间隔 > min_silence → 2 段 |
| `test_two_bursts_short_gap_merged` | 间隔 < min_silence → 合 1 段 |
| `test_all_silence_no_segment` | 全静音 → 空列表 |
| `test_short_speech_dropped` | 语音 < min_speech → 丢弃 |
| `test_long_speech_force_split` | 语音 > max_segment → 强切多段 |
| `test_flush_emits_trailing_segment` | 流末未收尾语音 → flush 吐出 |
| `test_pre_roll_extends_start` | 段 start_frame 早于首语音帧约 pre_roll |
| `test_frames_absolute_and_monotonic` | 多段 start/end 绝对、单调、跨 push 对齐 |
| `test_chunk_not_frame_multiple` | push 块长非 512 整数倍 → 跨 push 帧边界正确 |
| `test_two_channels_independent` | 两 segmenter 实例独立切，互不影响 |

---

## 10. 开放问题

1. **silero 真实精度 / 实时开销**：状态机已用 `_AmplitudeVad` 跑通；`SileroVad` 在 ASR venv 接真模型后，需 smoke 验证切段质量（喂 0.G 的 test_long.wav 肉眼核 turn 数与边界）+ 实时回压。
2. **ch2 是否也走 VAD**：录音师备注通道。asr-service §2 说 ch2 跳 diarization 直接 whisper，但仍需端点切段（whisper 不吃无限长流）。本 spec 默认 ch2 也走 VAD 切段，只是下游 ASR 不做 enrollment。
3. **DeviceSource 实时回压**：silero 每帧 onnx 推理开销 × 200ms chunk 是否跟得上实时，DeviceSource 接入时实测；跟不上则降帧率或换更轻探测器。
4. **段级 partial**：本 spec 只产 final 段（turn 收尾才吐）。词级 partial（is_partial=True 流式）是独立 ticket，不在本层。
```
