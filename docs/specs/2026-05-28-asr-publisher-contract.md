# Spec: ASR Publisher 转换契约（ticket 1.C 补充）

版本：v0.1
日期：2026-05-28
状态：草稿，待 Lead 评审

对接 ticket：1.C · feat: ASR — publisher.py（经纬 owner，尚未实现）

依赖 spec（按权威级别）：
1. `orchestrator-session-state v0.4`（`docs/specs/2026-05-27-orchestrator-session-state.md`）
2. `sqlite-schema v0.3.2`（`docs/specs/2026-05-27-sqlite-schema.md`）
3. `l2-pipeline v0.2`（`docs/specs/2026-05-27-l2-pipeline.md`）
4. `development-plan v0.2`（`docs/specs/2026-05-27-development-plan.md`）

---

## §1 目的与背景

### 1.1 为什么需要本 spec

经纬（ASR owner）侧的 `backend/asr/publisher.py`（ticket 1.C）负责将 ASR + speaker diarization 的离线 JSON 输出转化为 Orchestrator 内部事件（`asr.final.ch1`），写入 contract C1 的 `ASRSegmentPayload`。

本 spec 落定以下决策：

- JSON → contract C1 的字段映射规则（含时间单位转换）
- 繁简归一位置（publisher 侧，不进 L2）
- ch2 缺席的合法性
- speaker 标签透传策略
- take_id 注入责任边界
- 声纹 + 演员名绑定挂起策略

### 1.2 ASR 离线 JSON 样本

样本路径：`/Users/yedianyang/Downloads/diarize_first_20260527T135329Z.json`

基本信息：

| 字段 | 值 |
|---|---|
| `audio` | `artifacts\audio\test_podcast.wav` |
| `audio_seconds` | 87.25 秒 |
| `model` | `medium`（Whisper） |
| `language` | `zh` |
| `device` | `cuda` |
| `speakers` | `["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]`，共 3 个说话人 |
| `turns` 条数 | 15 条 |
| `rtf_end_to_end` | 0.0826（端到端实时率，含模型加载）|

`config` 字段（diarization 参数）：

```json
{
  "num_speakers": 3,
  "min_speakers": 2,
  "max_speakers": 4,
  "min_turn_s": 0.3,
  "merge_gap_s": 0.5
}
```

`timings` 字段（各阶段耗时，秒）：

```json
{
  "load_asr_s": 7.89,
  "load_diar_s": 2.29,
  "diar_s": 1.94,
  "asr_s": 5.27
}
```

每条 `turn` 的字段：

```json
{
  "start": 2.883,
  "end": 5.633,
  "duration": 2.751,
  "speaker": "SPEAKER_02",
  "text": "罗湘老师平时是一个不爱社交的人"
}
```

### 1.3 JSON 样本观察

**时间重叠**：样本中存在多组 turn 之间的时间重叠，例如：
- turn[3]（13.5–24.5 秒）与 turn[4]（21.0–22.0 秒）重叠
- turn[8]（42.7–57.4 秒）与 turn[9]（45.2–46.6 秒）、turn[10]（50.0–50.8 秒）、turn[11]（56.9–58.8 秒）均重叠
- turn[11] 与 turn[12] 重叠

时间重叠是 speaker diarization 的正常输出，publisher 不做去重，原样透传给 DAL。DAL `CHECK (end_frame > start_frame)` 约束以毫秒单位计算，重叠不影响约束合法性。

**繁简混杂**：前半部分 SPEAKER_02 输出简体，后半部分 SPEAKER_00 输出繁体（例：`我覺得反而是理科其實可能80%的理科要被幹掉了`）。publisher 侧负责归一化（见 §4.2）。

**turn 字段缺失**：JSON `turn` 中无 `ch / start_frame / end_frame / take_id / is_partial / event / topic` 字段，均由 publisher 侧补全（见 §4）。

---

## §2 现状与差距

### 2.1 contract C1（ASRSegmentPayload）期望字段

contract C1 定义于 `backend/core/events.py`，发布为 `asr.final.ch1` 事件的 payload：

```python
@dataclass(frozen=True)
class ASRSegmentPayload:
    ch: int               # 声道，1 或 2
    speaker: str | None   # 说话人标签
    text: str             # 转录文本（繁简归一后）
    start_frame: int      # 毫秒（秒 × 1000 取整）⚠ 字段名沿用历史命名
    end_frame: int        # 毫秒（秒 × 1000 取整）⚠ 字段名沿用历史命名
    take_id: int | None   # None 时 Orchestrator 回退 session.take_id
    is_partial: bool      # 离线场景为 False
```

### 2.2 JSON turn 字段与 contract C1 对照表

| contract C1 字段 | JSON turn 中是否存在 | 来源或补全方式 |
|---|---|---|
| `ch` | 否 | publisher 判定：单文件默认 ch=1；双声道按文件路径或元数据判定 |
| `speaker` | 是（`speaker`）| 直接透传，如 `"SPEAKER_02"` |
| `text` | 是（`text`）| 经 OpenCC `t2s` 转换后使用 |
| `start_frame` | 否（JSON 有 `start`，秒） | `round(turn["start"] * 1000)` |
| `end_frame` | 否（JSON 有 `end`，秒）| `round(turn["end"] * 1000)` |
| `take_id` | 否 | 离线场景由 caller 注入；生产由 Orchestrator SessionState 提供 |
| `is_partial` | 否 | 离线场景固定为 `False` |

### 2.3 DAL `transcript_segments` 字段对照

| DAL 列 | JSON turn 字段 | 转换 |
|---|---|---|
| `ch` | 无 | publisher 补全 |
| `speaker` | `speaker` | 直接透传 |
| `text` | `text` | OpenCC `t2s` 转换 |
| `start_frame` | `start`（秒）| `round(turn["start"] * 1000)` |
| `end_frame` | `end`（秒）| `round(turn["end"] * 1000)` |
| `take_id` | 无 | 外部注入 |

JSON 顶层字段（`audio / audio_seconds / model / language / device / config / timings / rtf_end_to_end`）当前不写入任何 DAL 表，仅用于 fixture 调试和日志，不在本 spec 定义存储方案。

---

## §3 publisher 输出契约（contract C1 v0.2）

### 3.1 TranscriptSegment 字段定义

publisher 组装出的每条 segment，对应 `ASRSegmentPayload`：

| 字段 | 类型 | 语义 |
|---|---|---|
| `ch` | `int`（1 或 2）| 声道；单文件 podcast 场景默认为 1 |
| `speaker` | `str \| None` | pyannote 原始 ID，如 `"SPEAKER_00"`；未知时 None |
| `text` | `str` | 转录文本，已经过 OpenCC `t2s` 繁转简 |
| `start_frame` | `int` | **毫秒**（秒 × 1000 取整）⚠ 字段名沿用历史命名，实际语义为毫秒 |
| `end_frame` | `int` | **毫秒**（秒 × 1000 取整）⚠ 字段名沿用历史命名，实际语义为毫秒 |
| `take_id` | `int \| None` | 离线场景由 caller 显式传入；生产场景由外部注入，publisher 不查 DAL |
| `is_partial` | `bool` | 离线整批为 `False` |

### 3.2 时间单位决策（Lead 拍板）

⚠ `transcript_segments.start_frame / end_frame` 字段名保留，**语义从「16 kHz 帧」改为「毫秒（秒 × 1000 取整）」**。不起 migration，字段名保留不变。后续若需要高精度时间对齐（16 kHz 帧），另开 ticket 升级字段名和语义。

示例：`21.023 秒 → start_frame = 21023`。

取整用 `round()`（Python 内置），不用 `int()`（截断有误差），不用 `math.ceil()`。

### 3.3 is_partial 语义

离线场景整批 final 输出，每条 turn 对应一条已完成的 segment，`is_partial` 固定为 `False`。

生产流式场景（实时 ASR）的 `is_partial` 逻辑由 1.C 经纬实现，本 spec 不重复定义。

### 3.4 take_id 注入策略

**离线场景（fixture / smoke test）**：caller（测试脚本或 CLI 工具）显式传入 `take_id`，整批 JSON 内所有 turn 挂同一个 take_id。publisher 函数不查 DAL，不产生 take_id。

**生产场景**：publisher 从 Orchestrator SessionState 取当前活跃 take_id，或由 take.start / take.end handler 注入。具体注入机制由 1.H 与 1.C 对齐，publisher 不自行查 DAL 生成 take_id（见 §10 Q1）。

---

## §4 publisher 转换规则

### 4.1 秒 → 毫秒

```python
start_frame = round(turn["start"] * 1000)
end_frame   = round(turn["end"] * 1000)
```

`round()` 返回 `int`（Python 3 中对 float 使用 `round()` 返回 int），可直接写库。

### 4.2 繁简归一（OpenCC `t2s`）

在 ASR text → contract C1 装包前，调一次 OpenCC `t2s` 把繁体转简体：

```python
from opencc import OpenCC
converter = OpenCC("t2s")

text = converter.convert(turn["text"])
```

**依赖选型**：`opencc-python-reimplemented`（pip 安装，约 3 MB，无需 C++ 编译）或 `OpenCC`（官方绑定）。两者 API 一致，`OpenCC("t2s")` 初始化即用。

**归一位置**：publisher 侧（确定性字符级映射，零 LLM），不在 L2 Pipeline 侧。L2 只做错别字修正（LLM 推理，见 §7）。

**`converter` 复用**：`OpenCC("t2s")` 初始化有模型加载开销，应在 module 级或 publisher 实例初始化时创建一次，不要在每条 turn 的循环里反复 `OpenCC(...)`。

### 4.3 speaker 透传

直接透传 pyannote 原始 ID，不做演员名绑定：

```python
speaker = turn.get("speaker")   # str | None，如 "SPEAKER_00"
```

### 4.4 ch 来源

| 场景 | ch 值 | 判定方式 |
|---|---|---|
| 单文件（podcast、单声道录音）| `1` | 默认 |
| 双声道（对白 ch1 + 录音师备注 ch2）| `1` 或 `2` | 按文件路径命名规约或元数据字段判定，由 1.C 实现 |

本 spec 的离线 fixture 场景只涉及单声道 ch1，ch2 处理见 §5。

### 4.5 is_partial

```python
is_partial = False   # 离线整批 final
```

### 4.6 完整转换伪代码

```python
from opencc import OpenCC

_converter = OpenCC("t2s")   # module 级，初始化一次

def json_turns_to_segments(
    turns: list[dict],
    take_id: int | None,
    ch: int = 1,
) -> list[ASRSegmentPayload]:
    segments = []
    for turn in turns:
        text = _converter.convert(turn["text"])
        segments.append(ASRSegmentPayload(
            ch=ch,
            speaker=turn.get("speaker"),
            text=text,
            start_frame=round(turn["start"] * 1000),
            end_frame=round(turn["end"] * 1000),
            take_id=take_id,
            is_partial=False,
        ))
    return segments
```

---

## §5 ch2 缺席处理

### 5.1 合法性

实际拍摄数据可能只有 ch1（对白），ch2 缺席是合法状态。

**规则**：
- 该 take 内不 publish `asr.final.ch2`，DAL `list_segments(take_id, ch=2)` 返回空列表。
- 空列表是合法的，不视为错误。
- Orchestrator、L2 Pipeline、前端 UI 均不应假设 ch2 必然存在。

### 5.2 下游影响

**DAL 层**：`list_segments(take_id, ch=2)` 返回 `[]`，调用方正常处理空列表即可。

**L2 Pipeline**：L2 输入只依赖 ch1（`dal.list_segments(take_id, ch=1)`），ch2 缺席不影响 L2 运行。已在 l2-pipeline v0.2 中确立，本 spec 引用不重复定义。

**前端 UI**：ch2 字段为空或缺席时，UI 渲染「无数据」（具体样式由 1.L 前端 ticket 实现，本 spec 不定义）。

### 5.3 生产场景识别

publisher 在生产场景识别 ch2 缺席的方式：检测是否有 ch2 音频输入（文件路径或流）；无 ch2 输入时直接不发 `asr.final.ch2`，无需发一个「空」事件。

---

## §6 离线 fixture 使用指引

### 6.1 适用场景

`/Users/yedianyang/Downloads/diarize_first_20260527T135329Z.json` 这类离线 JSON 文件用于：
- 1.G（L2 Pipeline）smoke test：验证 L2 错别字修正路径
- 1.H（Orchestrator take handler）集成测试：验证全链路（ASR → publisher → Orchestrator → L2 → 写库）
- 无剧本场景验证：`script_lines=[]` 时 L2 仍运行，输出 `corrected_segments`

### 6.2 整批挂一个 take

离线场景下，整段 JSON 的所有 15 条 turn 挂同一个 take_id：

```python
# smoke test 示例
with open("diarize_first_20260527T135329Z.json") as f:
    data = json.load(f)

take_id = dal.start_take(scene_id=1, take_number=1, start_ts=time.time())
segments = json_turns_to_segments(data["turns"], take_id=take_id, ch=1)

for seg in segments:
    orchestrator.publish("asr.final.ch1", seg)

dal.end_take(take_id, end_ts=time.time(), status="tbd")
```

### 6.3 无剧本场景验证

用上述 JSON 跑 1.H smoke 时，若未上传剧本（`dal.get_latest_script(scene_id) is None`），L2 以 `script_lines=[]` 运行：

- `line_matches` 输出 `[]`
- `corrected_segments` 仍正常输出（对 15 条 turn 做错别字检查）
- `script_diff_summary` 为 `null`

验证路径：检查 `takes.script_diff` JSON 中 `corrected_segments` 字段非空（或为 `[]`，视 turn 是否有错别字）。

---

## §7 与 L2 Pipeline 的分工

| 职责 | publisher（确定性）| L2 Pipeline（LLM 推理）|
|---|---|---|
| 繁简归一 | ✓ OpenCC `t2s`，字符级映射，零 LLM | — |
| 错别字修正 | — | ✓ LLM prompt 职责（l2-pipeline v0.2 §3）|
| speaker 标签透传 | ✓ 直接传 pyannote 原始 ID | — |
| 时间单位转换 | ✓ 秒 → 毫秒 | — |
| script diff 检测 | — | ✓ line_matches 输出 |
| 偏差摘要 | — | ✓ script_diff_summary |

分工原则：确定性、可逆的字符级变换放 publisher；需要上下文推断的语言处理放 L2。

---

## §8 未来扩展

### 8.1 声纹 + 演员名绑定（第一阶段挂起）

当前 publisher 透传 pyannote 原始 ID（`SPEAKER_00 / SPEAKER_01 / SPEAKER_02`），不绑定演员名。

后续可能的扩展方案（等 ASR 输出形态稳定后开新 ticket）：

- 新表 `speaker_voiceprints`：存 speaker_id → 演员名映射，含声纹向量（可选）
- 或在 `scenes` / `recordings` 表的 metadata JSON 字段里存映射
- 前端设置页面：录音师手动确认「SPEAKER_00 是 A 演员」
- publisher 查映射表，输出 `speaker` 字段改为演员名（或同时输出两个字段）

**当前状态**：不在第一阶段 scope，本 spec 只做透传，不实现绑定。

### 8.2 16 kHz 帧时间精度

当前 `start_frame / end_frame` 语义为毫秒（精度 1 ms）。如需高精度时间对齐（例如与音频帧精确同步、做精确剪辑点标注），可升级为 16 kHz 帧（精度 62.5 μs）：

- 公式：`start_frame = round(turn["start"] * 16000)`
- 需要同步更新字段名（`start_ms / end_ms` 更准确）、DAL 签名、下游 pipeline 输入
- migration 必须起：现有毫秒值无法无损转换为帧（精度不同，值域差 16 倍）

**当前状态**：Lead 决策毫秒够用，不在第一阶段升级。

### 8.3 多语言混合处理

当前 OpenCC `t2s` 只处理繁简转换，不处理其他语言。若 ASR 输出包含英语、粤语等，`t2s` 模式对非汉字内容无影响（直接透传），不会引入错误。多语言场景的特殊处理留后续评估。

---

## §9 测试矩阵

| 测试用例 | 验证行为 |
|---|---|
| `test_publisher_turns_to_segments_basic` | 3 条 turn → 3 条 segment，字段映射正确（ch=1, is_partial=False, take_id 透传）|
| `test_publisher_start_frame_ms_conversion` | `start=21.023` → `start_frame=21023`；`start=0.5005` → `start_frame=501`（round 取整验证）|
| `test_publisher_traditional_to_simplified` | 繁体 turn text → 简体 text；简体 text 保持不变 |
| `test_publisher_speaker_transparent` | `turn["speaker"]="SPEAKER_00"` → `segment.speaker="SPEAKER_00"`；`turn["speaker"]=None` → `segment.speaker=None` |
| `test_publisher_is_partial_false` | 离线 JSON 所有 segment 的 `is_partial` 均为 False |
| `test_publisher_opencc_reuse` | `OpenCC("t2s")` 只初始化一次（module 级或实例级），不在循环内重复创建 |
| `test_publisher_take_id_injected` | caller 传入 `take_id=42` → 所有 segment 的 `take_id=42` |
| `test_publisher_ch2_absent_legal` | ch2 场景缺席时，不发布 `asr.final.ch2`；`dal.list_segments(take_id, ch=2)` 返回空列表，不抛错 |
| `test_publisher_overlapping_turns` | turn 之间时间重叠（如 [3]vs[4]）不影响 segment 生成；`start_frame < end_frame` 约束在各自 segment 内部成立 |
| `test_publisher_fixture_15_turns` | 用 `diarize_first_20260527T135329Z.json` 生成 15 条 segment，无错误 |

---

## §10 待澄清问题

**Q1（take_id 注入生产链路）**：生产场景 publisher 如何从 Orchestrator SessionState 取当前 take_id？是通过回调、依赖注入，还是由 1.C 直接订阅 `take.start` / `take.end` 事件？需要 1.C（经纬）与 1.H（境熙）对齐，确保 publisher 不自行查 DAL 生成 take_id。本 spec 第一阶段只定转换规则，不钉生产注入机制。

**Q2（OpenCC 依赖版本锁定）**：`opencc-python-reimplemented` 还是 `OpenCC`？前者 pip 纯 Python，后者需要系统 libOpenCC。建议在 `requirements.txt` / `pyproject.toml` 锁定，避免跨平台安装差异。由 backend agent 实现时选定，本 spec 不强制。

**Q3（turn 时间重叠写库）**：DAL `CHECK (end_frame > start_frame)` 约束只检查单条 segment 内部，不检查跨条重叠。多条重叠 segment 全部入库，是否影响 L2 prompt 的 transcript 语义（重叠段可能导致文本重复）？建议 L2 侧按 start_frame 升序排列后直接使用，不做去重，与 diarization 的实际输出保持一致。若发现重叠导致 L2 输出质量下降，再评估 publisher 侧去重策略。

**Q4（ch2 双声道识别）**：双声道场景 publisher 如何识别 ch1 / ch2 文件？当前 1.C spec 未定义识别规约（文件名规约、元数据字段、或配置）。需要 1.C 与 1.H 对齐后写入 §4.4。
