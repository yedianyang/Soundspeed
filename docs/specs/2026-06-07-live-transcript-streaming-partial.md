# Spec: 实时转录流式 partial（路线 A：增长窗周期重转）

版本：v0.1（草稿，待 Lead 评审）
日期：2026-06-07
状态：草稿
owner：境熙 / Claude

变更记录：

- v0.1（2026-06-07）：初稿。给实时转录补「边说边渐进出字」的 iOS 听写式体感。现状真机录制全程无 partial，每句 VAD 端点出一条 final 整块上屏。本 spec 定 partial 的产生（增长窗周期重转 whisper）、节流、窗口封顶、自禁用安全阀，以及对 take.end 权威链路的零污染保证。

依赖 spec（按权威级别排序）：

1. voice-activity-layer v0.1（`docs/specs/2026-06-02-voice-activity-layer.md`）—— `ChannelVADSegmenter` 端点切段
2. asr-publisher-contract v0.1（`docs/specs/2026-05-28-asr-publisher-contract.md`）—— `asr.partial.chN` / `asr.final.chN` 事件契约
3. realtime-diarization-voicenote-design（`docs/specs/2026-06-02-realtime-diarization-voicenote-design.md`）—— take.end 后 pyannote 回填

覆盖范围：录制进行中，在 `StreamDriver` 里周期性把当前在制语音段重转 whisper，发 `asr.partial.chN`（`is_partial=True`）供前端就地渐进显示；VAD 端点照旧出 `asr.final.chN`（`is_partial=False`）落库。前端 partial 渲染通路已就绪（`session.ts` 的 `applyAsr` 替换该声道最后一条 partial，`LiveTranscript.tsx` 斜体灰字 + 脉冲光标），现仅被 dev 注入器触发；本 spec 补真后端 partial 源。

不覆盖：(1) take.end 之后的任何行为——pyannote 回填、L2、结构化转录、场记单全不动；(2) 稳定前缀提交 / LocalAgreement（用户已明确 partial 回改可接受，不做）；(3) ch2 voice note 的 partial（`process_channels` 生产仍只 `(0,)`）；(4) whisper 模型选型——large-v3-turbo 的切换由 `ASRConfig.model_size` 独立完成，与本 spec 解耦。

---

## 1. 背景与目标体感

iOS 听写的体感本质是「低延迟 + 渐进」。其中「低延迟」是后端属性：必须在说话人还没说完时就发文字。现状（`stream_driver._emit`）只在 VAD 端点出整段 final，文字要等「整句说完 + whisper 推完」才一次性蹦出，一顿一顿。本 spec 让后端在语音进行中周期性重转、发 partial，把首字延迟从「整句」压到「一个节流周期」。

诚实的体感天花板：whisper 是一锤子非流式模型，每次 `transcribe_pcm` 有固定墙钟开销，medium/large 给的是 **~亚秒到 1 秒粒度的块状渐进上字**，不是 iOS 那种逐词丝滑。这是模型层天花板，本 spec 不跨越。换 large-v3-turbo（解码层远少于 medium）单次重转墙钟显著下降，渐进粒度更细、且降低下文 §4 的采集饿死风险。

---

## 2. 头号安全约束（load-bearing）

用户硬约束，逐字节级：

- **C1**：take.end 之后传给 pyannote 的输入结构与现状**逐字节一致**。
- **C2**：说话 timestamp 的切分（落库 segment 的 `start_frame`/`end_frame`）与现状**统一不变**。

### 2.1 数据通路分离：必要

pyannote 在 take.end 拿两样东西（`backfill.py:_do_backfill`）：

1. `TakeAudioBuffer.get_audio()` —— ch1 PCM。由 `run()` chunk 循环的 `audio_sink(ch1, start_frame)` 喂入，**与 VAD/emit 无关、逐 chunk 全量写**（含静音）。
2. `dal.list_segments(take_id, ch=1)` —— ASR 段。**只由 `_on_asr_final` 落库**（`orchestrator.py:174` `insert_segment`）。orchestrator **没有任何持久化 partial 的订阅**——只订阅 `ASR_FINAL_CH1/CH2`（`orchestrator.py:115-122`）。

故只要满足：

- partial 一律走 `ASR_PARTIAL_CH1/CH2` topic（绝不走 FINAL）；
- `_emit_partial` **绝不调** `audio_sink`、**绝不落库**；
- 读在制段的 `peek_pending()` 对 `ChannelVADSegmenter` **纯只读**（不动状态机），

则 partial 与「采集→buffer」「VAD final→DB segment」两条权威链路在数据通路上零交叉。这是必要条件，已在代码核实成立。

### 2.2 时序通路：充分性的真正缺口

**数据通路分离不充分。** `DeviceSource` 是 sounddevice 阻塞式 `stream.read()`、无后台线程、无队列（`device_source.py:54-83`）；采集与转录在同一条 `live-asr` 线程上（`live_session.py:101`，`run()` 里 `for chunk in src` 和 `transcribe_pcm` 同线程）。partial 重转阻塞 `run()` 期间不读 source → PortAudio 内部环形缓冲若被填满 → 下次 `read()` 置 `overflowed=True` 且**采样静默丢失**。丢帧同时污染 TakeAudioBuffer 的 PCM（C1 破）和 VAD 看到的音频（段端点漂移，C2 破）。

**两条约束因此塌成同一个条件：partial 重转绝不能把采集饿到丢帧。** 数据分离免费、时序不免费。

重要现状基线：**final 转录今天就内联阻塞采集**（`_emit` 同步调 `transcribe_pcm`）。`_overflow_count` 警告就是为此存在。partial 不是引入新问题，是加重既有问题。large-v3-turbo 降单次墙钟，与多出的 partial 解码相抵；净溢出暴露不必然高于现状。

### 2.3 安全阀：反应式自禁用，失败即回退到现状

不声称「架构上免费守住」，也不声称「逐字节保证」。安全阀是**反应式**的：`overflow_count` 在 `_read_raw_block` 里 PortAudio **已经丢了采样之后**才 +1。等 `run()` 读到溢出再停 partial，那一帧的窟窿已经进了 TakeAudioBuffer、已经移了当条 take 的 VAD 切分。**故首次溢出会先污染当条 take，阀门只是止血（阻止本 take 内 partial 反复丢帧），不能预防第一次。** 且 `run()` 每 take 起始重新启用 partial，持续慢的机器可能每 take 丢一次。

因此诚实的保证是**条件式 + 安全降级**：

> 不溢出时（正常情形，large-turbo 下几乎必然）：take.end 输出逐字节一致。
> 一旦 partial 饿到采集：当条 take 丢帧——与今天 final 内联阻塞就可能触发的是**同一类失败**（partial 只是抬高概率）——且 partial 即时自我关闭，回退 final-only。

这就是为什么核心测试 T-starvation 断言的是「阀门跳闸」而非「逐字节一致」。把过渡期风险压到零的办法见 §4：**默认关 partial，等 large-turbo 落地再开**——medium-q8 解码最慢、阻塞窗口最宽、溢出概率最高，正是反应式阀门那个缝最会咬 C1/C2 的工况。

---

## 3. 设计：增长窗周期重转（路线 A）

单实例、单线程、串行。复用 `_emit` 那个全仓唯一的 `transcribe_pcm` 调用点（`stream_driver.py:109`），**不加锁、不加线程、不加模型**——已确认现状是单一调用方串行，不触发 SIGABRT 另案的多并发条件；新增 partial 仍在同一 `live-asr` 线程内顺序调，不破该不变量。

### 3.1 `ChannelVADSegmenter.peek_pending()`（纯只读）

```
def peek_pending(self) -> tuple[np.ndarray, int] | None:
    # SPEECH 态且在制语音跨度未超 partial 窗口封顶 → 返回 (concat(_seg_frames), _seg_start_abs)
    # 否则（SILENCE，或超封顶→冻结）→ None
```

- 纯读 `_state` / `_seg_frames` / `_seg_start_abs` / `_first_speech_abs` / `_last_speech_end`，**不 append、不消费 leftover、不改状态机**。final 段切分由不变的 `_process_frame`/`_close_segment` 出，§2.1 的 C2 成立。
- 窗口封顶：在制语音跨度 `_last_speech_end - _first_speech_abs > partial_max_window_ms*16` 时返回 None（冻结），不再发该 turn 的 partial。封顶语义见 §3.4。
- 另加只读属性 `in_speech -> bool`（`self._state == _SPEECH`），供 §3.3 区分「turn 结束」与「冻结」。

### 3.2 `StreamDriver` 节流触发

`run()` chunk 循环里，对每个 `process_channels` 命中的声道，**先**跑既有 `segmenters[i].push()`（出 final 走 `_emit`，路径不动），**再**做 partial 节流：

```
self._since_partial[i] += 1
if partials_enabled and self._since_partial[i] >= K:
    pend = segmenters[i].peek_pending()
    if pend is not None:
        self._emit_partial(pend, ch=i)
        self._partial_active[i] = True
    self._since_partial[i] = 0
```

- `K = VadConfig.partial_every_chunks`（默认 4，即 ~800ms @ chunk_ms=200）。`K<=0` 关闭 partial（静态开关）。
- 节流用 chunk 计次而非墙钟：partial 重转阻塞期间没有新 chunk 进来、计数不涨，partial 自动拉疏（防 backlog 堆积）。注意：这只约束 partial 频率，**不防采集溢出**（溢出由 §3.5 安全阀管）。

### 3.3 turn 收尾：清 partial 计数 + 清前端悬挂 partial

每个声道每轮 push 后：

- **若 push 返回了 SpeechSegment（final 出了）**：`_since_partial[i]=0`、`_partial_active[i]=False`。前端 `applyAsr` 收 final 自然用 final 替换最后那条 partial（`session.ts:188-191`），无需额外清。
- **否则若 `_partial_active[i]` 且 `not segmenters[i].in_speech`**（turn 结束但没出 final——语音跨度低于 `min_speech_ms=250` 被门掉）：发一条 **partial-clear**（见 §3.6），`_partial_active[i]=False`。否则前端会留一条幽灵斜体 + 光标行直到下一句覆盖。
- `flush()`（流结束）路径同样在收尾后清这两个标志。

### 3.4 `_emit_partial`

```
def _emit_partial(self, pend, ch):
    pcm, win_start_abs = pend
    text = self._runner.transcribe_pcm(pcm, audio_ctx=self._cfg_partial_audio_ctx)
    text = _normalize_to_simplified(text)          # 与 final 同一 t2s 管线，partial/final 同形
    if _is_hallucination_partial(text):            # 见下，放宽门
        return
    win_end_abs = win_start_abs + len(pcm)
    payload = AsrPartialPayload(
        text=text,
        start_frame=round(win_start_abs / 16),     # 16k 帧 → ms（与 final 同 contract C1）
        end_frame=round(win_end_abs / 16),
        speaker=None, take_id=None, is_partial=True,
    )
    self._publish(ASR_PARTIAL_CH1 if ch == 0 else ASR_PARTIAL_CH2, payload)
```

- **幻觉门放宽**：partial 跳过 `_is_hallucination` 里的 `len<=2` 短文本门（那条会吃掉每句开头 1-2 字、正砸首字延迟），保留 `_HALLUCINATION_PATTERNS` 模式过滤（防「谢谢观看」类整句幻觉上屏）。final 路径的过滤一字不动。
- **窗口封顶 = 增长窗 + 上限**：peek 在跨度 ≤ `partial_max_window_ms` 时返回完整在制段（短句即整句，常见情形），超过则冻结（停发）。如此单次 partial 解码量上界 = `partial_max_window_ms` 的音频，最坏单次墙钟可控。long-turn 尾部由不变的 final（最长 `max_segment_ms=30s`，与今天同）兜底覆盖，partial 冻结期间零额外算力。

### 3.5 安全阀：溢出自禁用

`DeviceSource` 暴露只读 `overflow_count`（已有 `self._overflow_count`，补 property）。`StreamDriver.run()` 每轮：

```
cur = getattr(src, "overflow_count", 0)
if cur > self._last_overflow:
    partials_enabled = False        # 本 take 永久停发 partial
    logger.warning("采集溢出 → 停发 partial，回退到 final-only（C1/C2 优先）")
    self._last_overflow = cur
```

- 单次溢出即跳闸，本 take 不再发 partial → 回退到今天逐字节一致的行为。`FileSource` 无 `overflow_count`，`getattr` 取 0，安全阀对批处理无副作用。
- 这是 C1/C2 的兜底防线：即便 §3.4 的封顶估错、large-turbo 比预期慢，丢帧也只会让 partial 停掉、不会污染 final。

### 3.6 partial-clear 契约

turn 结束未出 final 时需清前端悬挂 partial。方案：发 `AsrPartialPayload(text="")`（`is_partial=True`、空文本）作为清除信号。前端 `applyAsr` 增补语义：**收到 `isPartial && text===""` → 移除该声道最后一条 partial（不 push 空段）**。非空 partial 行为不变（替换最后一条 partial）。

---

## 4. 配置旋钮（全部挂现有 config）

| 旋钮 | dataclass 落点 / 默认 | 生产 env（entrypoint 显式传）/ 默认 | 含义 |
|---|---|---|---|
| `partial_every_chunks` (K) | `VadConfig` / **0（关，兜底）** | `SOUNDSPEED_PARTIAL_CHUNKS` / **4（开）** | partial 节流周期（chunk 数）。`<=0` 关闭 |
| `partial_max_window_ms` | `VadConfig` / 12000 | —（暂不暴露 env） | 在制语音超此长度冻结 partial（封顶单次解码量） |
| `partial_audio_ctx` | `ASRConfig` / None | `SOUNDSPEED_PARTIAL_AUDIO_CTX` / None | partial 重转的 whisper `audio_ctx`（砍 encoder 墙钟，换边界精度）。None=满窗 |

dataclass 默认与生产默认分离，沿用项目惯例（entrypoint 总是显式传，dataclass 默认只在非生产路径兜底，见 `entrypoint.py` model_size 注释）。**dataclass K=0** 保证测试/直接构造默认无 partial（不打扰既有 final-only 测试）；**生产 entrypoint env 默认 K=4** 开启。large-turbo 已落地，按 §2.3 默认开是安全的；若回退到 medium-q8，设 `SOUNDSPEED_PARTIAL_CHUNKS=0` 关掉。

`transcribe_pcm` 加可选 `audio_ctx: int | None = None`，仅非 None 时透传 `model.transcribe(..., audio_ctx=...)`，final 调用不传 → 行为不变。**实现时须先验证 pywhispercpp `Model.transcribe` 接受 `audio_ctx`**（调研指向 `pywhispercpp constants.py` PARAMS_SCHEMA 合法，落地前实测一次）。

---

## 5. 改动清单

后端：

- `backend/vad/segmenter.py`：加 `peek_pending()`（纯只读，含窗口封顶判定）+ `in_speech` property。状态机零改。
- `backend/vad/models.py`：`VadConfig` 加 `partial_every_chunks` / `partial_max_window_ms`。
- `backend/asr/stream_driver.py`：`StreamDriver` 加 `_since_partial` / `_partial_active` / `_last_overflow` / `partials_enabled` 状态；`run()` 循环加节流触发 + turn 收尾清理 + 溢出安全阀；加 `_emit_partial`；加 `_is_hallucination_partial`（放宽门）。`_emit`（final）一字不动。
- `backend/asr/whisper_runner.py`：`transcribe_pcm` 加可选 `audio_ctx` 透传。
- `backend/asr/config.py`：`ASRConfig` 加 `partial_audio_ctx`。
- `backend/asr/live_session.py`：`start()` 把 `partial_audio_ctx`（getattr 兜底）透传给 `StreamDriver`。
- `backend/audio/device_source.py`：暴露 `overflow_count` property。
- `backend/api/entrypoint.py`：读 `SOUNDSPEED_PARTIAL_CHUNKS`（默认 4）/ `SOUNDSPEED_PARTIAL_AUDIO_CTX` env，显式传入 `VadConfig` / `ASRConfig`。

前端：

- `frontend/src/store/session.ts`：`applyAsr` 增补「空文本 partial = 移除该声道最后一条 partial」。
- 其余零改——`AsrPartialPayload` / `app.py` 转发 / `useLiveConnection` 解析 / `LiveTranscript` 斜体灰字+脉冲光标，partial 通路已就绪。

不改：`backfill.py`、`orchestrator._on_asr_final`、`TakeAudioBuffer`、L2、结构化转录、场记单。

---

## 6. 测试计划（TDD，按权威性排序）

C1/C2 是硬约束，测试必须打到**真正会破它的时序路径**，不能只测数据通路。

- **T-peek（纯读不变量，单元）**：构造 SPEECH 态 segmenter，快照全部内部字段，连调 `peek_pending()` N 次，断言字段全不变；且后续 `push()` 产出的 `SpeechSegment`（`audio`/`start_frame`/`end_frame`）与从不 peek 的对照完全一致。证 C2 的切分不被 peek 扰动。
- **T-datapath（数据分离，必要不充分）**：FileSource + 假 runner，同一 take 跑 partial 开/关两遍。断言 (a) `audio_sink` 调用序列（PCM + start_frame）逐项一致；(b) 落库 ch1 segment（text/start_frame/end_frame）一致。**显式标注**：FileSource 不会丢帧，此测对溢出路径平凡通过，只证「partial 逻辑不碰权威逻辑」，不证 C1/C2 在实时下成立。
- **T-starvation（真不变量，核心）**：写一个**实时节奏 + 有损**的假源 harness——按 chunk_ms 墙钟节奏吐 chunk、内部有限缓冲、未及时读取即丢帧并置 overflow——配一个 `transcribe` 可注入 sleep 的假 runner。partial 开，驱动一个 take。断言二选一：(a) 无溢出 且 PCM+segment 与基线逐字节一致；或 (b) 发生溢出则安全阀已跳闸、partial 已停、final 链路不受影响。这是 FileSource 测不到的饿死路径。
- **T-killswitch（安全阀）**：注入 `overflow_count` 上涨 → 断言本 take 后续不再发 partial，final 照常。
- **T-dangling（悬挂清除）**：语音跨度 < `min_speech_ms` 的 turn → 发了 partial 但无 final → 断言发出 partial-clear。
- **T-store（前端单元）**：`applyAsr` 空文本 partial 移除最后一条 partial；非空 partial 替换；partial→final 替换仍正确。

`[手动测试]`：真机长 take，盯 `overflow_count` 日志保持平、partial 边说边渐进、停录后 diarization 回填与场记单与现状一致。

---

## 7. 取舍与已知边界

- 体感是 ~亚秒到 1s 块状渐进，不是 iOS 逐词丝滑（whisper 一锤子模型固定墙钟，模型层天花板）。要更细：调小 `partial_audio_ctx`，或后续挂独立 small/base 第二 runner 专跑 live（独立 context 可真并发不撞 SIGABRT，代价 ~500MB 常驻）——本 spec 不做。
- partial 文本会被后文整句改写（增长窗重转，无稳定前缀提交）。用户已接受。
- 超 `partial_max_window_ms` 的长 turn partial 冻结，文字停更到 final 落定。set 上对白多为短句，封顶罕触发。
- partial 纯显示、不落库；下游永久转录仍用端点整段 final（满精度），C1/C2 不受 partial 边界精度影响。
- ch2 voice note 不在范围。
