# 实时 ASR + 批量说话人分离 + Voice Note 设计

状态：设计已确认，待写实施计划。日期：2026-06-02。

## 1. 背景与目标

同期录制场景，场记工作台需要：录制中实时看到对白文本流式出现；说话人标签可以晚一点出现；音频全程不落盘（仅内存处理）。

关键约束与已确认决策：
- 文本实时流式出现，**说话人标签允许延迟**（diarization 做成非实时）。
- 不落盘：整条链路不写任何 wav / 临时音频文件。
- diarization 用 **pyannote.audio 4.0**，**不用 diart**（diart 0.9.2 锁死 numpy<2、huggingface_hub<0.20，与主栈硬冲突且需独立进程；pyannote 4.0 依赖现代、可 in-process）。
- in-process 跑在现有 Python 3.14 venv，**不需要降级**（pyannote 4.0 requires-python>=3.10；之前为评估 diart 把下界降到 >=3.12 无害且仍允许 3.14）。

## 2. 通道角色

- **ch1 = 表演声**（boom/主咪，多说话人）：流式 VAD+ASR 出实时文本（speaker 先为 NULL）；take 结束后批量 diarization 回填说话人（说话人N / 演员名）。
- **ch2 = 场记口述 / voice note**（单说话人）：流式 VAD+ASR 出带时间戳文本（ch=2，speaker=NULL）；**不跑 diarization**；隐式标记（ch=2 即 voice note）；收集进独立「note 区」，交给 LLM 仅作归置整理，**不参与 L2 剧本比对的判断逻辑**。

## 3. 方案定位

采用：**按 take 批量回填 + 跨 take 声纹台账**。

已否决的备选：
- 真在线增量聚类（diart 那套）：用户明确不需要实时说话人。
- 滚动窗口批量：会重聚类导致已回填编号回头变，且更费；不需要。

延迟特性：说话人标签在 take 结束后约 30~60s 出现（M1 Max 上 4min/16k 的 pyannote 量级估算，待真机基准）。长 take 录制途中无说话人、停了才补，已确认可接受。

## 4. 整体数据流

```
录制中（实时）
  DeviceSource → AudioChunk(ch1/ch2, int16 16k, 各声道独立不混音)
    ├─ ch1 → VAD+ASR → seg(ch1, spk=NULL) → DAL 写库 + WS 推前端(文本)
    │         └─ TakeAudioBuffer：累积 ch1 PCM（内存，仅本 take）
    └─ ch2 → VAD+ASR → seg(ch2, spk=NULL, voice note) → DAL 写库 + WS 推前端(文本)
              （ch2 不缓音频、不进 diarization）

take.end → fire-and-forget 异步「有序链」（取代现有 take.end 即时触发 _run_l2_async）
    1. 取 TakeAudioBuffer 的 ch1 整段（内存，~8MB/4min）
    2. DiarizationEngine（pyannote 4.0 批量）→ [(start_s, end_s, local_spk, embedding)]
    3. SpeakerRegistry：local_spk 平均声纹 → 全局稳定编号 / 演员名
    4. 对齐：每个 ch1 ASR segment 按最大时间重叠归到某说话人
    5. DAL 批量 UPDATE transcript_segments.speaker（仅 ch1）
    6. 回填完成后才触发 L2（gate）：组装输入 ch1（带说话人+时间戳）+ ch2（note 区，带时间戳）→ 调用 L2 → UPDATE take row（transcript+diff+摘要）
    7. 推前端：发「带 segment 的事件」或令前端 invalidate/refetch GET /takes/{id}（仅发 take.changed 不够，见下）
    8. 释放 TakeAudioBuffer（音频丢弃，不落盘）
```

仅 ch1 跑 diarization（沿用现有约定，ch2 强制 speaker=NULL）。

**两条关键时序约束（Codex 评审，必须遵守）：**
- **L2 必须 gate 在回填之后**：现有 orchestrator 在 take.end 会立刻 fire `_run_l2_async`，它在 speaker 回填前就读 ch1 segment。必须把这个即时触发改成「由本异步链第 6 步、即回填完成后触发」，否则 L2 会拿 speaker=None 的 ch1、且无 note 输入，持久化出错误结果。L2 不再由 take.end 直接触发。
- **前端刷新不能只发 take.changed**：`take.changed` 的 WS payload 只带 take 字段、前端只 patch-merge take 字段，而 transcript segment 是另走 `GET /takes/{id}` 加载的。回填的说话人标签 / note 区要刷新，必须发一个带 segment 的事件，或让前端对该 take 详情 invalidate/refetch。

## 5. 核心组件（各自单一职责、可独立测）

1. **TakeAudioBuffer**：take 期间累积 ch1 PCM 的内存缓冲；take.end 提供整段、之后清空。~8MB/4min。不落盘的保证点。仅依赖 numpy。
2. **流式 VAD+ASR 接入**（whisper.cpp，已测但未接入生产）：每通道流式 VAD（挡静音，ch2 尤其需要，避免静音幻觉）+ ASR → 带时间戳 segment。ch1、ch2 两路。本次范围含「把已测的 whisper.cpp 流式 + VAD 接进 orchestrator」。
3. **DiarizationEngine**：包 pyannote.audio 4.0 离线 pipeline。输入一段 int16 16k 单声道 numpy，输出 [(start_s, end_s, local_speaker, embedding)]。in-process，模型懒加载（首次用时才下）。**只吃内存 numpy/tensor，不接收文件路径**。
4. **SpeakerRegistry**：跨 take 声纹台账。输入本 take 各 local cluster 的平均 embedding，按余弦相似度匹配已知全局说话人：命中→沿用全局编号/演员名；未命中→顺位发「说话人N」并存入台账。落在一张小 SQLite 表（按拍摄/会话 scope）。
5. **SpeakerNameBinding**：前端/端点把全局说话人绑定到演员名（如 说话人3=张三），持久化进台账，之后该声纹自动显示演员名。实现用户 Q2 的「已知显示名字 / 新人顺位编号」逻辑。
6. **对齐（Alignment）**：每个 ch1 ASR segment 的 [start,end] 与 diarization turns 求最大重叠，归到对应全局说话人。
7. **DiarizationBackfill**：take.end 异步协调者，串起 buffer→engine→registry→对齐→DAL UPDATE→WS。挂在 orchestrator take.end 异步路径。
8. **VoiceNote 路由**：ch2 segment 收集进 note 区（带时间戳），随 take.end 交付 LLM 仅作归置整理。

## 6. 数据模型

- **transcript_segments（现有，复用）**：ch、speaker、text、start_frame/end_frame(ms)、take_id。ch1 的 speaker 由回填写入；ch2 的 speaker 保持 NULL（ch=2 即 voice note，隐式标记）。
- **speakers 台账（新增表，需 v3 migration）**：建议字段 speaker_id、display_name(nullable)、embedding 质心(blob)、sample_count、scope_key（拍摄/会话）、created_at/updated_at。local→global 的逐 take 映射为临时态，不持久化。
- **note 区**：ch=2 的 segment 即构成 note 区内容（API/前端按 ch=2 取）；LLM 可产出一份归置后的笔记摘要（落在 take 上，字段待定）。

## 7. 依赖与运行

- 新增 **pyannote.audio>=4.0**、**torch>=2.8**（mac 用 CPU/MPS 轮子）到 backend/requirements.txt。
- pyannote 4.0：requires-python>=3.10、huggingface_hub>=0.28.1（兼容主栈 huggingface_hub 1.17.0）、numpy 无上界（兼容主栈 numpy 2.4.4）。→ **可 in-process，跑在现有 3.14 venv，无依赖冲突，不用 diart、不用独立进程、不用降级**。
- 需 HF token（env，gated 模型）：HF 账号需先同意 pyannote 模型使用条款。
- 成本：主后端新增 torch（几百 MB，M1 Max 64G 无压力）。

## 8. 不落盘保证

整条 take 的 ch1 PCM 只活在 TakeAudioBuffer 内存对象里，diarization 用完即释放。pyannote 4.0 吃内存里的 numpy/tensor，不走文件接口。ch2 不缓音频。全程无 wav / 临时文件写盘。实现时盯住：不要误用接收「文件路径」的 ASR/diarization API。

## 9. 集成点（现有代码锚点）

- 音频源 / AudioChunk：backend/audio/source.py（AudioChunk :25 含 channels、start_frame、seq）、backend/audio/device_source.py、backend/audio/constants.py（OUTPUT_SAMPLE_RATE=16000）。
- Orchestrator take.end：backend/core/orchestrator.py:183（_on_take_end）；异步范式 :274（_run_l2_async）可照搬。
- ASR 事件：backend/core/events.py（ASR_FINAL_CH1/CH2、TAKE_*）；orchestrator 订阅 :88-95（ch1 保留 speaker，ch2 force_speaker_none）。
- DAL：backend/db/dal.py（insert_segment :372、TranscriptSegment :45）；orchestrator :294 list_segments(ch=1)；需新增批量 update_segment_speaker、registry 读写。
- DB migration：**新增 v3**（v2 已被 v2_scene_heading.sql 占用，见 runner.py 的 MIGRATION_FILES）建 speakers 表 `v3_speakers.sql`，并在 `backend/db/migrations/runner.py` 的 `MIGRATION_FILES` 注册 `3: "v3_speakers.sql"`。
- WS：backend/api/ws.py（broadcast）。回填完成后**不能只发 take.changed**（只带 take 字段、前端 patch-merge）——需发带 segment 的事件或触发前端 refetch `GET /takes/{id}`，否则说话人标签 / note 区不刷新。

## 10. 风险 / 未决

1. **实时 ASR 测过但没接上**——本设计输入接口依赖它，是范围里最大的一块新增工作；需先确认已测 whisper.cpp 流式接口的形态。
2. **跨 take 声纹匹配准确度**——余弦阈值要调，相近音色 / 短发言可能错配，导致同一演员跨 take 编号跳。场记人工复核兜底（标签做参考，准确度门槛低）。
3. **HF token + gated 条款**——模型下载前置条件。
4. **pyannote 4.0 在 M1 Max 上 4min/16k 的真机耗时**——估算 30~60s，需用户给 token 后实测基准。
5. **台账 scope**——按会话还是按整个拍摄（同一批演员复用）？默认按拍摄/项目，需与现有 session/project 模型对齐。
6. **note 区的最终落地形态 + LLM 归置笔记的输出字段**——待细化。

## 11. 范围边界

- 范围内：whisper.cpp 流式 VAD+ASR 接入（ch1+ch2）、TakeAudioBuffer、DiarizationEngine（pyannote 4.0）、SpeakerRegistry + 演员名绑定、对齐+回填、voice note 路由进 note 区、v3 DB migration、WS 更新。
- 范围外：实时/在线 diarization、diart、云 API、L2 剧本比对逻辑本身（其输入新增 note 区，但比对逻辑不变）、复杂重叠语音处理。

## 12. 实施阶段建议

- Phase 0：确认并接入已测 whisper.cpp 流式 + VAD（ch1+ch2）→ 实时文本跑通。
- Phase 1：TakeAudioBuffer + DiarizationEngine（pyannote 4.0 in-process），单 take 回填（本地编号），真机基准耗时。
- Phase 2：SpeakerRegistry 跨 take + 演员名绑定。
- Phase 3：voice note 路由进 note 区 + LLM 交付。

## 13. 评审修正（Codex review 2026-06-02 已并入）

- **migration 版本**：speakers 表用 v3（v2 已被 v2_scene_heading.sql 占用）。见 §6 / §9。
- **L2 时序**：L2 由 diarization 回填完成后触发，取代现有 take.end 即时 _run_l2_async。见 §4。
- **前端刷新**：回填后发带 segment 的事件或令前端 refetch，不能只发 take.changed。见 §4 / §9。
