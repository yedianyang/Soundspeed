# Spec: 语音 note 输入 + NP 收尾（4.x 续）

版本：v0.4
日期：2026-06-05
状态：草稿，待 Lead 评审
owner：境熙

修订：
- v0.5：4.J 实现路线定为**方案 A（一步到位建标准化多模态实例）**——4.J 即按最终形态建那份共享单实例：挂多模态 handler、`n_batch=n_ubatch=2048` + `image_max_tokens=1120`（gemma4 vision non-causal 要 1120 image token 落单 ubatch；audio/text 不需要但无害，只是没用上）、`CHAT_FORMAT` 与 3.x vision 探针对齐。理由：3.x 图像通路已实测验证、仅生产功能未开发；现在把**输入结构标准化**，3.x/4.x 合并时 vision 只接调用、不动实例构造，零冲突（§5.1/§5.2/§5.4）。
- v0.4：架构对齐（与 Lead）——① **单模型实例**确定为硬约束，不开第二实例；② 该实例由**一个多模态 handler 统一**，分**三个内容入口**：**文本**（L2 / 文本 NP / 纯剧本输入 SP **共用同一文本入口**）、**音频**（语音 NP，mtmd audio）、**图像**（剧本拍照 OCR，vision 路径，3.G）；③ 所有产文本任务统一走文本入口，保证文本格式化一致，代价是 L2 离开旧默认 chat_template 路径、输出可能变——**明确接受并重新盖章 L2 golden，不再追求与旧路径逐字一致**；§10-2 de-risk 口径相应从「一致才放行」改为「统一 + 验有效 + 重盖章，仅质量退化才算不过」；④ 记录 **3.x 收敛**：vision 入口已在 `feat/3.x-script-import` 的 `scripts/sp_vision_probe.py` 落原型（**不改它**），两分支合并时 vision + audio + text 收敛到同一 handler + 实例。
- v0.3：48kHz de-risk 实测解除（§3.2 / §5.2 / §10-1）——48kHz WAV 经 mtmd 产出与 16kHz 相同 token 数 + 逐字节相同输出，自动重采样正确；前端直传 48kHz，无需客户端降采样，相应回退方案删除。剩一个实现前置 de-risk：L2 文本 parity（§10-2）。
- v0.2：评审后修订 —— ① L2 / 文本 NP / 语音 NP **管线平行**（独立 async 编排），但共用**单一模型实例**，进模型按 Orchestrator 序列调度（`_lock` + priority）排队——不开第二实例；单实例代价是文本也走多模态 handler，须给 L2 加文本输出 parity 回归；② 48kHz 重采样改标为**待验证假设** + de-risk；③ `asr_unclear` 改为模型自报低置信机制（否则不可检测）；④ 语音 pending 的 `category` 也占位（202 时未知）；⑤ 临时音频 NP 完成后删 + 重试仅限本次会话。
- v0.1：初稿。

依赖 spec（按权威级别排序）：
1. note-input-design v0.1（`docs/specs/2026-06-12-note-input-design.md`） — 打字 note 输入格式、解析器、`take_events`+`takes.notes` 落点、`POST /notes` 契约、前端 memo 框
2. sqlite-schema v0.3.3（`docs/specs/2026-05-27-sqlite-schema.md`） — `takes.notes`、`take_events`、event_type
3. llm-service-design（`docs/specs/2026-05-25-llm-service-design.md`） — `note_struct` task type、LLMService 注入/异步
4. realtime-diarization-voicenote-design（`docs/specs/2026-06-02-realtime-diarization-voicenote-design.md`） — 本 spec **作废**其中 ch2 语音备注部分（见 §9）

覆盖范围：语音 note 输入端到端（前端按住录音 → WAV 直传 → Gemma 4 原生音频 → 结构化 → 写 history note）、音频 API 契约（新端点 `POST /notes/voice`）、后端多模态音频 infer 通路、NP prompt 的场镜次上下文补全（文本/语音共用）、NP 失败兜底（`note.failed` 事件 + 前端失败态）。同时正式作废 ch2 语音备注 / 4.E / 4.F。

---

## 1. 背景与目标

### 1.1 方案转向：ch2 砍掉

原 4.x 设计有三股：①ch2 语音备注（录音师第二声道 → diarization → note 区）②NP 结构化 ③打字候补 note。其中 ① 依赖 diarization 把场记的话从 ch2 切出来，并卡在 4.E 决策（老 NP vs 新 diarization 两套打架）。

**本 spec 起，①ch2 整条砍掉。** 语音 note 不再来自录音师声道 + diarization，而是场记在 admin 端**主动按麦克风按钮说话**。砍掉后果：4.E 决策无意义、4.F（ch2 NP 结构化）无前提，两者作废（§9.1）。

### 1.2 现状

打字 note（②③）已完整落地并验证：`POST /notes` → `parse_note`（正则剥 `@类别`/前缀）→ 202 → fire-and-forget NP（Gemma 文本 infer 判 take + 提类别正文）→ `insert_note`（`take_events` `manual.note` + `takes.notes` 聚合）→ `note.processed` WS（带 `client_id`）→ 前端 pending 转实。

打字链路有两个已知缺口（本 spec 一并收）：
- **NP prompt 的 take 上下文不含 shot。** 现传 `take_id`/`scene_id`（DB 内部 id）+ 历史 take 的 `scene_code`+`take_number`，但**无 shot**。2.x 按（场, 镜）per-shot 计次，同场不同镜各有「第一条第二条」，不带 shot，「第三条」跨镜歧义。
- **NP 失败无兜底。** NP 失败（如 LLM 返回不存在的 `take_id` 撞 `FOREIGN KEY`）只后端 WARNING + 发 idle，**不发 `note.processed`**，前端 pending 永久卡「处理中」，场记不知道这条没存上。

### 1.3 目标

1. 语音 note：场记按住麦克风说话 → 录音 → 上传 → Gemma 4 **原生音频输入**一次完成「听懂 + 定位 take + 结构化」→ 写入对应 take 的 history note。与打字 note 共用下游（落库 / WS / 队列）。
2. 补全 NP prompt 的**场镜次（scene-shot-take）上下文**，文本/语音共用，消除镜次歧义。
3. NP 失败兜底：新增 `note.failed` 事件，前端把卡住的 pending 标失败 + 可重试。

### 1.4 不覆盖

- 真实 ASR（ch1 转录）/ diarization：与本 spec 无关，各自 ticket。
- 语音 note 的多语种 / 说话人识别：MVP 只做单段中文语音 → 结构化。
- 语音端点的鉴权扩展：复用现有 `require_admin`（Bearer token）。

---

## 2. 总体架构

### 2.1 两输入，一管线

```
文本 note ──┐
            ├─→ NP 归置（Gemma 4）─→ insert_note ─→ note.processed / note.failed ─→ 前端队列
语音 note ──┘
   （音频）         （文本路径走文本 infer；语音路径走音频 infer，模型同一个 Gemma 4 E4B）
```

文本与语音在「拿到输入 → 灌 Gemma → 出 `{take_id, category, content}` → 落库 → WS → 队列」这段**完全共用**。唯一分叉在 infer 入口：文本走 `messages=[text]`，语音走 `messages=[text 上下文 + 音频]`（多模态）。

### 2.2 数据流（语音路径）

1. 前端 MemoInput 麦克风按钮**按住** → `getUserMedia` 取麦克风 → AudioWorklet 采集 PCM → 松开停止 → 编码为 WAV（原生采样率，mono）→ 生成 `client_id`（`crypto.randomUUID`）→ `POST /api/v1/notes/voice`（multipart：音频文件 + client_id）。
2. 后端端点：存音频（临时）→ 乐观返回 `202`（与文本对称）→ fire-and-forget **音频 NP**。前端同步插入 pending（队列显示「处理中」，文案可标「🎤 语音」）。
3. 音频 NP（后台异步）：组装场镜次文本上下文（§6）+ 音频 → Gemma 4 多模态 infer（§5）→ `{take_id, category, content}`。
4. 落库 `insert_note`（`take_events` `manual.note` + `takes.notes` 聚合）；成功发 `note.processed`（带 `client_id`），失败发 `note.failed`（带 `client_id` + reason）（§7）。
5. 前端按 `client_id`：`note.processed` → pending 转实 + 刷新队列；`note.failed` → pending 标失败 + 可重试。
6. **临时音频生命周期**：NP 处理完成后删除临时音频（成功落库后删；失败也删——重试由前端重新上传留存的 blob 触发，后端不留底）。不做服务端长期存储（MVP，§8）。

L2 / 文本 NP / 语音 NP 是**独立 async 管线（编排平行）**，但进模型时共用**单一模型实例**、按 Orchestrator 的序列调度（`_lock` + priority）排队（§5.1）。

### 2.3 可行性依据（spike，2026-06-05）

已实证当前 `llama-cpp-python 0.3.25` 不重编不升级即可把音频喂进 Gemma 4 E4B：
- `unsloth/gemma-4-E4B-it-GGUF` 的 `mmproj-F16.gguf` 带 `clip.has_audio_encoder=True` / `projector_type=gemma4a` / `num_mel_bins=128`。
- 包内 `libmtmd.dylib`（含 `mtmd_audio_preprocessor_gemma4a`）+ `mtmd_cpp.py`（`mtmd_support_audio`、`mtmd_bitmap_init_from_audio`、`media_marker`）。
- 接法：子类化 `llama_cpp.llama_chat_format.Gemma4ChatHandler`，把 WAV 字节从 `image_url` content 通道走，override `load_image` 返回音频字节；`_create_bitmap_from_bytes`（继承）用通用 `mtmd_helper_bitmap_init_from_buf`（miniaudio 自动识别 WAV=音频）；eval 循环已处理 `MTMD_INPUT_CHUNK_TYPE_AUDIO`，整套 tokenize/eval/采样复用。
- 结果：音频「第三条结尾很好，可以用」+ 场镜次上下文（Scene_1/Shot1/take 101-103）→ `{"take_id":103,"category":"keeper","content":"结尾很好，可以用"}`。听懂中文 ✓ / 按「第三条」定位 ✓ / keeper 分类 ✓。模型加载 1.9s（热缓存）、音频编码 428ms、推理 1.1s。
- 已知瑕疵：进程退出时 Metal 清理崩 `GGML_ASSERT ggml-metal-device.m:618`（llama.cpp PR #17869），在结果产出之后，不影响功能；长驻后端不每请求退出故生产不触发。

---

## 3. 语音输入（前端）

### 3.1 麦克风按钮交互：按住说话（对讲机式）

MemoInput 现有麦克风按钮（现 placeholder）改为按住录音：
- `pointerdown` / `touchstart`：开始录音，按钮转录制态（红点 + 计时 / 波形可选）。
- `pointerup` / `touchend` / `pointercancel`：停止录音 → 编码 → 上传。
- 录音中可取消：上滑离开按钮 / 时长过短（< 0.5s）视为误触，丢弃不提交。
- 录音中其它输入态：文本输入框可禁用或并存（实现细节，不强制）。

理由：现场场记单手快、不会忘停、符合「按一下记一句」的节奏。

### 3.2 录音 + WAV 编码

- `navigator.mediaDevices.getUserMedia({audio:true})` 取流。
- AudioWorklet（或退化用 ScriptProcessorNode）采集 Float32 PCM。
- 松开后编码为 **WAV（PCM 16-bit, mono, 原生采样率）**。iOS 的 AudioContext 采样率硬件定（常 48kHz）。✅ **已实测（2026-06-05）**：48kHz WAV 喂进同一条 mtmd 通路，`audio_tokens->n_tokens` 与 16kHz 完全一致（67），模型输出逐字节相同——mtmd 读 WAV 头自动重采样到编码器所需采样率，正确。**前端直传 48kHz，无需客户端降采样。**
- 单条语音上限（如 30s / 2MB）防误录长音频；超限提示。

### 3.3 上传

- `POST /api/v1/notes/voice`，`multipart/form-data`：`file`（WAV）+ `client_id`（前端生成）+ 可选 `ts`。
- 复用 `api.ts` 的鉴权（Bearer token）。新增 `postVoiceNote(blob, clientId)`。

### 3.4 pending / 失败态

- 上传发起即乐观插入 pending（与文本一致，复用 `addPendingNote`）。**语音 pending 的 `content` 与 `category` 都占位**——文本 note 的 `category` 由 `postNote` 响应带回（正则解析），但语音的类别由模型从音频里听+判，202 时**两者都未知**，只显「🎤 语音备注…处理中」。`PendingNote.category` 需放宽为可空/占位（契约改动点）。
- `note.processed`（client_id 匹配）→ 转实（`category` + 正文换成 Gemma 听+判+归置后的结果）。
- `note.failed`（client_id 匹配）→ pending 标失败态（红 + 「识别失败，点击重试」），重试＝重新上传同一段音频（前端短暂留存该 blob 直到落定）。**重试仅限本次会话**：页面 reload 会丢失未落定的 blob，reload 后该条无法重试（可接受，MVP；失败态在 reload 后消失，场记重录即可）。

### 3.5 iOS 真机前置：HTTPS

`getUserMedia` 仅在安全上下文（HTTPS）或 `localhost` 可用。iPhone/iPad 经局域网 IP 访问 dev server（`http://<lan-ip>:5175`）属不安全上下文，**麦克风会被拒**。真机测试前置：dev 开 HTTPS（vite 自签证书 / mkcert）或走隧道（Tailscale / ngrok）。此限制与音频格式无关，任何麦克风方案皆然。WAV 直传相较 `MediaRecorder` 反而更 iOS-friendly（iOS Safari 的 MediaRecorder 吐 mp4/aac 非 webm/opus）。

---

## 4. 音频 API 契约

### 4.1 POST /api/v1/notes/voice

```
POST /api/v1/notes/voice
Content-Type: multipart/form-data
Authorization: Bearer <token>

form fields:
  file       : WAV 音频（PCM16 mono，原生采样率）  [required]
  client_id  : 前端去重键（uuid）                    [required]
  ts         : 客户端时间戳（秒，float）             [optional]

→ 202 Accepted
  { "status": "processing", "client_id": "<uuid>" }

错误：
  400  音频缺失 / 解码失败 / 超限
  401  鉴权失败
```

与文本 `POST /notes` 对称：均 202 fire-and-forget，归置走后台异步 NP，结果经 WS（`note.processed` / `note.failed`）回灌。差异：语音是 multipart + 音频，文本是 JSON + `text`；语音无 `parse_note` 正则前缀（类别/编号由 Gemma 从语音里听+判）。

### 4.2 与 POST /notes（文本）的关系

两端点各自独立，共用下游 NP + 落库 + WS。文本端点不变（`docs/specs/2026-06-12-note-input-design.md` §5.1）。新增 `note.failed` 对两端点都生效（§7）。

---

## 5. 后端音频 infer 通路

### 5.1 多模态 GemmaClient / LLMService

现 `GemmaClient`（`backend/llm/client.py`）纯文本：`Llama(model_path=...)`（**不挂 chat_handler，走 GGUF 自带 chat_template 的默认 jinja 格式化**）+ `create_chat_completion(messages=text)`。`np_note.py` 与 `l2_take.py` 调**同一个** `LLMService.infer`，service 持**单个** `_client` + `_lock` + priority 序列调度，即 L2 与文本 NP 今天已共用一份模型、按 priority 排队串行进模型。

**单实例 + 统一多模态 handler（架构定调，硬约束）。** 物理上始终**一份** `Llama`，**不开第二实例**。这份实例挂**一个多模态 handler**（`Llama(model_path=GGUF, chat_handler=<多模态 handler>, ...)`，加载 mmproj-F16，含 gemma4v 视觉 + gemma4a 音频两个投影器），由这一个 handler 按 content 类型分**三个入口**：
- **文本入口** —— L2 / 文本 NP / 纯剧本输入（SP，3.B）**共用同一文本入口**，统一格式化。所有产文本的任务走这一条，保证文本输入统一。
- **音频入口** —— 语音 NP，WAV 经 mtmd audio（§5.2、spike）。
- **图像入口** —— 剧本拍照 OCR，vision 路径（3.G，见下「3.x 收敛」）。

管线仍平行：L2 / 文本 NP / 语音 NP 是三条独立 async 编排，互不等待；进模型时都经 LLMService 的**序列调度**（`_lock` + priority）排进同一队列，串行喂同一实例——这就是 Orchestrator 的调度。
- LLMService 增音频 infer 入口（如 `infer_voice(text_context, audio_bytes, task_type, priority, ...)`），与 `infer` 共用同一 `_client` + `_lock` + priority 队列；同步推理用 `asyncio.to_thread` 包。
- **mmproj 运行时自动下载（部署态修复）**：`get_service()` lazy、startup 不预热 LLM，mmproj 是 4.J 才加的新依赖且 `ensure_model_ready` 只在 base 缺失时跑——故 base 已缓存的现存安装（升级路径）不会自动补 mmproj。修法：`_ensure_client`（所有路径建 client 的唯一咽喉，在 worker thread 内）发现 mmproj 未缓存时 `resolve_mmproj_path(download=True)` **自动下载**再建多模态，保证单实例永远升多模态（方案 A），不论 L2/文本 NP/语音 NP 哪条先触发。下载失败（离线）→ 退纯文本，音频路径由 `note.failed(model_unavailable)` 兜底（§7.2），前端不卡。已知限界：若首次建 client 时离线建成纯文本并缓存，网络恢复后语音仍需重启进程才生效（MVP 可接受）。
- **L2 离开旧默认模板路径（统一的代价，明确接受）**：文本统一走 handler 后，L2 的格式化从 GGUF 默认 chat_template 换成 handler 的 `CHAT_FORMAT`，prompt 字符串可能变。典型差异：gemma 无 system role，system 怎么折进 user turn 两套实现不一定逐字一致——3.x 的 `CHAT_FORMAT` 把 system 与 user 拼成**两个连续 user turn**，GGUF 默认模板**合并成一个**。**口径：不追求与旧路径逐字一致，而是统一后跑一遍 L2、确认输出仍合法（`script_diff` 结构/摘要有效），漂了就重新盖章 L2 golden；仅当质量真的退化（摘要错位 / diff 对不上）才算 de-risk 不过。** 统一 > 保留旧 L2 格式化。
- **3.x 收敛（方案 A：4.J 即建最终形态）**：vision 入口已在 `feat/3.x-script-import` 的 `scripts/sp_vision_probe.py` 落原型并**实测通路打通**（子类 `Llava15ChatHandler`，override gemma `CHAT_FORMAT` + `_init_mtmd_context` 设 image token 档位 1120 给 OCR），只是生产功能未开发——**本 spec 不改它**。**4.J 直接按最终标准化形态建那份共享单实例**：一份 `Llama` 挂多模态 handler，一个 mtmd context 同时支持 vision + audio（mmproj-F16 两投影器都在），三入口（text/audio/image）共用同一 `CHAT_FORMAT`。vision-ready 的构造参数现在就位（见 §5.2）：`n_batch=n_ubatch=2048`（gemma4 vision non-causal 要 1120 image token 落单 ubatch；audio/text 用不上但无害）、`image_max_tokens=1120`。这样 3.x/4.x 合并时 vision 只需把调用接进来，**不动实例构造，零冲突**。版本差（3.x `0.3.23` 手搓 / 4.x `0.3.25` 内置 `Gemma4ChatHandler`）在合并到 `0.3.25` 一侧时统一到 4.J 这份实现。
- 显存：单实例 + mmproj 增量（投影器较小），**不翻倍**（§5.4）。

### 5.2 mtmd 音频 handler（spike 接法产品化）

把 spike 的子类化 handler 收进 `backend/llm/`：
- 子类 `Gemma4ChatHandler`，override `load_image`：当 content 是音频哨兵（约定 url 前缀，如 `soundspeed://audio/<id>`）时返回该次请求的 WAV 字节，否则走父类。
- 音频 content 走 `messages` 的 `{"type":"image_url","image_url":{"url": <哨兵>}}`（复用 image_url 通道；mtmd 的 `media_marker` 对音频/图像通用，tokenize/eval 不区分）。
- 采样率：✅ **已实测** mtmd 的 miniaudio + gemma4a 预处理器吃任意采样率 WAV 并内部重采样到模型所需——16kHz 与 48kHz 产出相同 token 数 + 相同输出（§3.2）。后端不做采样率转换，原样喂。
- 启动自检：加载后断言 `mtmd_support_audio(ctx) is True`，否则 fail-fast（模型/mmproj 不匹配早暴露）。
- handler 的 `CHAT_FORMAT`（gemma 模板）即「文本入口」的统一格式化层（§5.1）——文本/音频/图像三入口共用同一 `CHAT_FORMAT`，与 3.x vision handler 收敛时保持一致。
- **`GemmaClient` 构造参数（方案 A，vision-ready）**：`Llama(model_path=GGUF, chat_handler=<多模态 handler>, n_ctx=8192, n_batch=2048, n_ubatch=2048, n_gpu_layers=-1, seed=42)`，handler 持 `clip_model_path=<mmproj-F16>`，`_init_mtmd_context` 设 `image_min_tokens=image_max_tokens=1120`。`n_batch=n_ubatch=2048` 与 `image_max_tokens=1120` 是 gemma4 vision OCR 的硬要求（3.x 实测），4.J 现在就位以便 3.x 零冲突合并；音频/文本不依赖这两项但不受其害。mmproj 路径解析仿 `resolve_model_path`：env `GEMMA_MMPROJ_PATH` > HF cache（`unsloth/gemma-4-E4B-it-GGUF` 的 `mmproj-F16.gguf`）> 下载。**注意：本 handler 的 vision 入口（`image_max_tokens=1120` 实际效果）4.J 未跑过——只验了 audio + text（§4.J-5 smoke），vision 由 3.x 探针在它自己那份 handler 上验；本 handler 的 vision「就位」属 vision-ready，真效果待 3.x 合并时验。**

### 5.3 NP 音频 runner

`np_note.py` / orchestrator 的 NP 通路加音频分支：
- 文本 NP：现状不变（`run_np_async(raw_text, ...)`）。
- 音频 NP：`run_np_voice_async(audio, client_id, ts)` → 组装场镜次文本上下文（§6）+ 音频 → 音频 infer → 同样解析 `{take_id, category, content}` → `insert_note` → `note.processed`/`note.failed`。
- 复用现有 `_np_done_callback` 的 idle 发射、失败 WARNING；失败分支补发 `note.failed`（§7）。
- `client_id` 透传链与文本一致（已实现）。

### 5.4 模型常驻 / 显存

- **单实例 + mmproj 增量，不翻倍**：只一份 E4B base gguf（文本/音频共用），外加常驻的 `mmproj-F16.gguf`（视觉 gemma4v + 音频 gemma4a 投影器，体量远小于 base）。相比现状纯文本 NP，仅多 mmproj 的占用。
- 目标机（M 系列）实测：E4B + mmproj + whisper/pyannote 同跑的显存峰值；`SOUNDSPEED_LLM_GPU_LAYERS` 可降 GPU 层缓解（现有机制）。增量小，风险低于双实例。
- `n_batch=n_ubatch=2048`（方案 A，vision-ready）比默认 512 多一块 compute buffer，是固定常量增量（与 batch 上限成正比，非每 token 累积），相对 base + mmproj 占比小。✅ **实测（2026-06-05，4.J-5 smoke）**：base(Q4_K_M) + mmproj-F16 + `n_batch=2048` 全 GPU 卸载，RSS 峰值 **~6746 MB**，与纯文本 ~6.5GB 基本持平（不翻倍，符合预期）。

---

## 6. 场镜次 prompt 补全（文本 / 语音共用）

### 6.1 现状缺口

`np_note.py::_build_user_message` + orchestrator 组装的 `take_context`：当前给 `current_take_id`/`current_scene_id`（DB 内部 id）+ 历史 take 的 `scene_code`+`take_number`，**无 shot**。

### 6.2 改造

NP 上下文统一升级为人类可读的**完整场-镜-次**：
- 当前上下文：`当前场=<scene_code>  当前镜=<shot>  当前活跃 take=<scene_code>/<shot>/第N条`（无活跃 take 时显式注明）。
- 历史 take 列表每条带 `shot`：`take_id=<id>  <scene_code>/<shot>/第<take_number>条  [L2 摘要]`。
- orchestrator 组 `take_context` 时补 `shot` 字段（DAL 已有 take.shot）。
- 解析规则提示对齐 per-shot 语义：「第三条」= 当前场当前镜的第 3 条；跨镜/跨场需显式带镜次/场次。

此改动文本/语音两路共用（同一 `_build_user_message`），改一处两边受益。spike 已证带完整场镜次时 Gemma 能正确定位「第三条」。

---

## 7. NP 失败兜底

### 7.1 现状

NP 失败（LLM 返回不存在 take_id 撞 FK、解析失败、超时）→ `_np_done_callback` 记 WARNING + 发 idle，**不发任何 note 结果事件**。前端 pending 因等不到 `note.processed` 永久卡「处理中」。

### 7.2 note.failed 事件

新增 WS 事件 `note.failed`：

```
NoteFailedPayload:
  client_id : str | None    # 前端去重键，定位要标失败的 pending
  reason    : str           # 失败原因（见下）
  ts        : float
```

reason 取值（**只列机制上可检测的**）：
- `take_not_found` —— LLM 返回的 take_id 不存在（`insert_note` 撞 FK，可捕获）。
- `parse_error` —— LLM 输出非合法 JSON / 字段缺失（解析时可捕获）。
- `timeout` —— infer 超时（可捕获）。
- `model_unavailable` —— 多模态 client 未就绪：mmproj 缺失且自动下载失败（离线）→ 单实例退纯文本 → 音频推理撞 `RuntimeError`（无 handler）/ mtmd 初始化 `ValueError` / audio 非 bytes `TypeError`。`_finalize_np` 归类发此 reason，**否则命中 `else: raise` 静默退出 → 前端 pending 永久卡（复活 4.I 的 bug）**。前端文案「模型未就绪」+ 重试。
- `asr_unclear` —— **仅当采用模型自报机制时才发**。NP 拿到的是模型吐的 JSON，模型听岔了也是吐合法 JSON，后端**无法直接判定「音频不清」**。要支持这个 reason，需在音频 prompt 约定：没听清时输出特定标记（如 `{"take_id": null, "category": "", "content": ""}` 或 `unclear: true`），后端检出该标记 → 映射成 `asr_unclear`。**MVP 可不实现 asr_unclear**，听岔就归成普通 note（内容可能错，由场记肉眼发现并改）；要做再按此机制加。

发射点：`_run_np_async` / `run_np_voice_async` 的失败路径（捕获 FK / 解析 / 超时；asr_unclear 走模型自报检出）→ `publish(NOTE_FAILED, ...)`，随后照常 idle。成功路径不变（发 `note.processed`）。文本/语音两路共用（文本无 asr_unclear）。

### 7.3 前端失败态

- `note.failed`（client_id 匹配）→ 对应 pending 标失败态（红 + reason 文案 + 「重试」）。
- 文本重试＝重新 `POST /notes` 同文本；语音重试＝重新上传留存的 blob。
- client_id 缺失（异常/旧链路）时不误标，仅记日志。

---

## 8. 落库与显示（复用，不变）

- `insert_note`：写 `take_events`（`manual.note`，payload 含 category/content/raw_text/ts）+ 原子重建 `takes.notes` 聚合（现状，§ note-input-design v0.1 §4）。语音 note 的 `raw_text` 存 Gemma 转写归置后的文本（无独立原始转写存储，MVP）。
- 显示：队列浮层（NoteList）+ take 详情读 `GET /takes/{take_id}/notes`（不变）。`note.processed` bump `notesVersion` 触发刷新（已实现）。

---

## 9. 边界与作废

### 9.1 作废 ch2 / 4.E / 4.F

音频输入职责划分见 GitHub #19（1.n 音频输入职责重划）：**后端采集设备**（现场麦/调音台）= 录演员对白（ASR + diarization + 声纹注册，同信号链）；**前端浏览器麦**（场记 iPad/笔记本，`getUserMedia`）= 只管语音 note（本 spec 4.x）。本 spec 的语音 note 即 #19 的 part 3「语音 note 用前端麦」。#19 的 part 1（声纹注册迁后端）/ part 2（电平统一后端）属 1.n，独立 workstream，与本 spec 无耦合。

- ch2 语音备注（录音师第二声道 + diarization → note 区）整条**作废**。语音 note 改由 admin 端主动录音（本 spec）。
- Notion 4.E（ch2 归属决策，blocked 等经纬）→ 作废，无需决策。
- Notion 4.F（ch2 NP 结构化，gate 在 4.E）→ 作废。
- `docs/specs/2026-06-02-realtime-diarization-voicenote-design.md` 的 ch2→note 区部分被本 spec 取代（diarization 用于 ch1 speaker 回填的部分不受影响）。

### 9.2 不覆盖

见 §1.4。另：语音 note 不做实时流式转写（一段录完整体送）；不做语音命令（仅备注）；不改 status（沿用 note-only + 2.x Mark，§ note-input-design v0.1）。

---

## 10. 风险与技术注意

1. ✅ **48kHz 重采样已实测解除**（2026-06-05，§3.2 / §5.2）——48kHz WAV 经 mtmd 产出与 16kHz 完全相同的 token 数（67）+ 逐字节相同的输出，自动重采样正确。前端直传 48kHz，无需降采样，地基已夯。
2. **L2 文本 parity：统一文本入口的代价（已接受，重盖章而非追平）**（§5.1）——文本统一走多模态 handler 后，L2 离开 GGUF 默认 chat_template，格式化可能漂移。**不追求逐字一致**：跑一遍确认输出仍合法，漂了重新盖章 golden；仅质量退化才算 de-risk 不过。
   - ✅ **文本路径基础 parity 已验（2026-06-05，4.J-5 smoke）**：同一多模态实例跑纯文本 `note_struct` infer，产出合法结构化 JSON（`{"take_id":103,"category":"note","content":"…"}`），统一 handler **不破坏文本推理**。0.3.25 内置 `Gemma4ChatHandler` 用官方 gemma 模板（system 合并进首个 user turn），比 3.x 手搓版（两个 user turn）更接近 GGUF 默认模板，漂移面更小。
   - ⬜ **完整 L2 golden 回归待办**：上面只证「文本经 handler 仍合法」，尚未跑真 L2 pipeline（take.end → `run_l2_take` → `script_diff` 摘要）对比 golden。L2 端到端被触发时（真 take + 转录 + 剧本）跑一遍，摘要质量不退化即过，漂则重盖 golden（§11）。风险已大幅降低（文本推理经 handler 证实连贯）。
   - 显存仅多 mmproj 增量（单实例不翻倍，§5.4 实测 ~6746 MB）。
3. **iOS 真机麦克风需 HTTPS**（§3.5）——真机验收前置，非代码问题。
4. **Metal teardown 崩**（§2.3）——长驻后端不触发；若做 CLI/一次性脚本需注意。
5. **小模型音频鲁棒性**——E4B 对口音/噪声/长句的转写准确率需真机场景验。听岔（吐合法但错的 JSON）后端检测不到，归普通 note 由场记肉眼改；`asr_unclear` 需模型自报机制才能兜（§7.2，MVP 可不做）。
6. **NP 仍依赖有效 take 上下文**——无任何 take 时语音/文本都可能无处可归；`note.failed`（take_not_found）兜底 + 前端提示，不再静默卡死。

---

## 11. 验收

- 语音：admin 按住麦克风说一句 → 浮层 pending（🎤 处理中）→ Gemma 音频归置 → 转实，正文为转写归置文本，绑到正确 take。Playwright + 真机各一轮。
- 场镜次：NP prompt 含完整 scene-shot-take；「第三条」在多镜场景定位正确（契约测试 + 手测）。
- 失败兜底：构造 NP 失败（如无有效 take）→ 前端 pending 标失败可重试，不再永久卡。契约测试覆盖 `note.failed` 发射 + client_id 匹配。
- 文本 note 回归不破（现有 pytest 全绿 + A/B/C）。
- **L2 统一文本入口 parity**（§5.1、§10-2）：L2 切到统一 handler 后跑一遍，输出仍合法（`script_diff` 结构有效、摘要非空）；漂移则重新盖章 golden 并记录前后差异；质量不退化即通过（非逐字一致门）。
- ruff + mypy + tsc 全过；新增音频通路有契约测试（音频 infer 可用 stub handler 隔离，端到端真模型 smoke 一条）。
- Lead 评审。
