# L2 无剧本纯纠错 + 繁简转换

版本：v0.2
日期：2026-06-06
状态：已批准（Lead）
负责 agent：backend-agent

---

## 0. 评审记录（Lead, v0.2）

以下评审意见已整合进 v0.2，逐条标注处置结果：

1. **无剧本独立 task_type 方案**：接受。新增 `l2_take_no_script`，不改现有 `l2_take` 路径，分叉点在 `run_l2_take`，有剧本路径零改动。
2. **grammar 删字段抑制假数据**：接受。`report_corrections_only` 只含 `corrected_segments` 一个字段，grammar 层面约束模型不能生成 `script_diff_summary`/`line_matches`，比 prompt 改动更硬。
3. **繁简放 `_emit` 幻觉过滤前**：接受。`stream_driver._emit` 中 `transcribe_pcm` 之后、`_is_hallucination` 之前插入 `_normalize_to_simplified`，繁体幻觉（如「謝謝」）也能被过滤，理由充分。
4. **依赖包名修正**：修改。将 3.2.2 和 8.4 中的 `opencc-python-reimplemented>=0.1.7` 改为 `OpenCC==1.3.1`（C++ 绑定，cp312 macOS arm64 + win_amd64 预编译 wheel 已实测存在，无编译负担，词典权威）。Python API 仍是 `import opencc; opencc.OpenCC('t2s').convert(text)`，与原 spec 描述一致。
5. **前端「无偏差」语义遗留**：接受，列为后续 UX 细化。无剧本且无纠错时前端显示「无偏差」，与「无剧本」语义有轻微混淆，但不引入假数据，可接受。本次不改前端，后续可在 `ScriptDiffView` 判断 `!diff.script_diff_summary && matches.length === 0` 且 diff 存在时显示「无剧本 / 无纠错」。

---

## 1. 现状问题

无剧本（`script_lines` 为空）时，L2 Pipeline 照样走有剧本路径：

- `tool_choice` 强制调 `report_script_analysis`，parameters.required 包含 `script_diff_summary`（类型 `string`，非 nullable）和 `line_matches`。
- grammar 约束下模型必须吐非空 string，于是编造「无剧本，所有转录内容均被标记为插入」。
- user message 的任务说明（「①找出 insertion ②逐行比对」）进一步推动模型填满 `line_matches`（全部 `line_no=-1, diff_type=insertion`）。
- 模型把所有转录段列入 `corrected_segments` 但一字未改（`original == corrected`），纯占位填充。
- 前端 ScriptDiffView 忠实渲染全部假数据：满屏「原==改」+「加词 null」。

根因：**不是 prompt 问题，是被 forced 的 tool schema 三字段全 required，grammar 不产 None**。

---

## 2. 目标

无剧本时，L2 **只做转录纠错，不做剧本比对，不做总结**：

- `line_matches` = `[]`（空列表，无剧本对比无意义）
- `script_diff_summary` = `None`（无剧本无摘要）
- `corrected_segments` 只包含真正有修改的段（`original != corrected`）

同时全局将转录文本中的繁体字确定性转换为简体，不依赖模型。

---

## 3. 四件事的实现方案

### 3.1 无剧本纯纠错分支（核心）

**方案：新增 task_type `l2_take_no_script`，不改现有 `l2_take` 路径。**

理由：`LLMService.infer_tool` 通过 task_type 从 `TASK_CONFIG` 取 tools/tool_choice，没有 per-call override 机制。新增 task_type 是唯一不改 service 层的分叉方式，现有有剧本路径零改动。

#### 3.1.1 新 tool schema

新增 `backend/llm/tools/script.py::build_l2_no_script_tool()`，只含 `corrected_segments` 一个字段：

```
function name: report_corrections_only
parameters.required: ["corrected_segments"]
不含 script_diff_summary / line_matches
```

#### 3.1.2 新 task_type 注册

`backend/llm/config.py` 的 `TASK_CONFIG` 加 `"l2_take_no_script"` 条目：

```python
"l2_take_no_script": {
    "max_tokens": 2048,      # 无剧本只纠错，输出更短
    "temperature": 0.2,
    "priority": 2,
    "system": "<纯纠错 system prompt，见 3.1.3>",
    "tools": [build_l2_no_script_tool()],
    "tool_choice": {"type": "function", "function": {"name": "report_corrections_only"}},
}
```

#### 3.1.3 纯纠错 system prompt

去掉剧本偏差检测职责，只保留错别字修正：

```
整合 take 信息，修正转录错别字。

职责：
检查转录文本中的明显错别字（同音字、形近字误识别），输出修正结果。

输出格式要求（严格遵守）：
- 只调用工具，不额外输出文字。
- corrected_segments 只列出真正有修改的 segment，未改动的不出现；无需修正时输出空列表 []。
- corrected 必须是修正后的字符串，禁止为 null；无法确认修正时直接不输出该条。
- idx 是转录记录列表的下标（从 0 开始），对应 user message 中转录记录前的序号。
```

#### 3.1.4 user message 分叉

`l2_take.py::_build_user_message` 按 `script_lines == []` 走不同分支：

**有剧本路径**（现有逻辑，不动）：
```
## 剧本台词（场次 {scene_id}）
{script_block}

## Take {take_number} 转录记录（含下标索引）
{transcript_block}

任务：①找出转录中剧本完全没有对应的内容标为 insertion...②逐行比对...③识别 ASR 错别字...
直接输出 JSON。
```

**无剧本路径（新）**：
```
## Take {take_number} 转录记录（含下标索引）

{transcript_block}

任务：识别转录文本中的 ASR 错别字（同音字、形近字误识别），输出到 corrected_segments。
```

去掉剧本块、去掉任务①②、只保留纯纠错任务。`previous_notes` 在无剧本时同样省略（无 summary 可参考）。

#### 3.1.5 `run_l2_take` 分叉逻辑

```python
async def run_l2_take(input_data, llm_service, timeout=60.0):
    system_prompt = _build_system_prompt(no_script=not input_data.script_lines)
    user_message = _build_user_message(input_data)

    task_type = "l2_take_no_script" if not input_data.script_lines else "l2_take"
    # ... infer_tool(messages, task_type=task_type, ...) ...
    # ... _validate_data_dict(data, strict=task_type == "l2_take") ...
```

#### 3.1.6 `_validate_data_dict` 放宽

现在严格要求三字段必须存在。放宽：当字段缺失时给默认值（不抛错），新 tool 的精简输出（只有 `corrected_segments`）可以正常通过解析：

```python
script_diff_summary = data.get("script_diff_summary")          # 缺失 → None（不抛）
raw_matches = data.get("line_matches", [])                     # 缺失 → []（不抛）
raw_corrections = data.get("corrected_segments")               # 仍要求必须存在
if raw_corrections is None:
    raise L2ParseError("missing 'corrected_segments'")
```

有剧本路径沿用现有严格校验（三字段都要求存在），避免削弱有剧本场景的解析护栏。可通过 `strict` 参数控制，或根据缺失字段的组合判断。

---

### 3.2 繁简转换（opencc t2s）

#### 3.2.1 集成层：ASR 输出侧，`stream_driver._emit` 幻觉过滤之前

**推荐位置：`_emit` 中调 `transcribe_pcm` 后、`_is_hallucination` 检测前**，即：

```python
text = self._runner.transcribe_pcm(seg.audio)
text = _normalize_to_simplified(text)          # 繁→简，新增
if not text.strip():
    return
if _is_hallucination(text):
    ...
```

**理由（按权重排序）**：

1. **幻觉过滤提前受益**：幻觉模式表（「谢谢」「谢谢观看」）是简体。若繁体转录「謝謝」不被过滤而入库，后续展示异常。放转换在幻觉检测之前，繁体幻觉也能被过滤。

2. **全链路只见简体**：入库（`insert_segment.text`）、L2（`transcript_segments[*].text`）、前端实时显示、diarization 回填读文本（仅用于整合 speaker，不依赖字形，但统一更干净）均消费简体。

3. **繁简转换与有无剧本无关**，是全局确定性清洗，放 ASR 侧比放 L2 侧层次更准确。

4. opencc `t2s` 对已是简体和英文完全幂等，无需按 language 分支。

**副作用评估**：

- 已入库的繁体旧记录不回溯转换。dev 数据库可能存在混字（部分旧 take 繁体、新 take 简体）。影响：前端展示风格不一，L2 input 里旧 take 的 `corrected_segments` 若引用旧行则仍是繁体——可接受，只要新数据一致即可。建议 dev 数据库清空重建（清 `transcript_segments` 表），不需要迁移脚本。
- diarization 回填：读 `segment.text` 仅用于整合展示（`structured_transcript`），不重新转录，繁简转换对帧对齐无副作用。
- 现有测试 fixtures：搜索 `backend/tests/` 确认无 fixture 期望繁体透传（任务实施前确认，目前调研未见繁体文本 fixture）。

**ownership 边界提醒**：`stream_driver.py` / `whisper_runner.py` 属于 backend-asr 文件 ownership。本 spec 给出设计，实现必须由 backend-asr agent 执行或 Lead 协调。backend-agent 只实现 L2 侧（3.1 和 3.3）。

#### 3.2.2 opencc 依赖

在 `pyproject.toml` `dependencies` 加：

```
"OpenCC==1.3.1",
```

两平台（macOS arm64 + win32）的 cp312 预编译 wheel 已验证存在（约 1.6–2.1 MB，无编译负担，C++ 绑定，词典权威）。

Python API：

```python
import opencc
_CC = opencc.OpenCC("t2s")   # 模块级单例，避免重复初始化

def _normalize_to_simplified(text: str) -> str:
    return _CC.convert(text)
```

---

### 3.3 过滤无效纠错段（`original == corrected`）

**位置：`l2_take.py::_validate_data_dict`**，解析 `corrected_segments` 时过滤：

```python
cs = CorrectedSegment(idx=idx, original=original, corrected=corrected)
if cs.original == cs.corrected:
    continue   # 跳过无效段，不加入 corrected_segments 列表
```

理由：后端解析层统一过滤，前端和 orchestrator 无感知，blast radius 最小。无剧本和有剧本路径均受益（兜底 4B 模型在有剧本时也可能乱填相等的 corrected）。

---

### 3.4 detail 归一化（次要兜底）

**位置：`l2_take.py::_validate_data_dict`** 中 `detail` 取值处：

```python
raw_detail = item.get("detail")
# 归一化：字符串 "null" / "none" / 空白字符串 → Python None
if isinstance(raw_detail, str):
    stripped = raw_detail.strip().lower()
    if stripped in ("null", "none", ""):
        detail = None
    else:
        detail = raw_detail
else:
    detail = raw_detail   # None 直接保留
```

无剧本路径 `line_matches=[]`，此段代码路径不执行。对有剧本路径给模型乱填的假空值兜底。

---

## 4. 数据流与存储路径

### 4.1 无剧本路径完整数据流

```
take.end
  → orchestrator._run_l2_async
      → script_lines = []（dal.get_latest_script 返回 None 时）
      → L2Input(script_lines=[])
      → run_l2_take(input_data)
          → task_type = "l2_take_no_script"
          → infer_tool(messages, "l2_take_no_script")
              → report_corrections_only tool（只含 corrected_segments）
          → L2Output(
                script_diff_summary=None,
                line_matches=[],
                corrected_segments=[...只有真正有修改的段...]
            )
      → script_diff_dict = {
            "script_diff_summary": None,
            "line_matches": [],
            "corrected_segments": [...]
          }
      → dal.update_take_l2_output(take_id, script_diff_dict)
      → take_line_matches 表：line_matches=[] → 零写入（原有 orchestrator 逻辑，if matches_for_dal 守卫）
      → publish TakeChangedPayload(script_diff=script_diff_dict)
```

### 4.2 两条存储路径

- `takes.script_diff`（JSON 字段）：存完整 `script_diff_dict`，前端通过 GET /api/v1/takes 拿。无剧本时含 `{summary:null, line_matches:[], corrected_segments:[...]}`。
- `take_line_matches` 表（关系表）：存 line_no→line_id 映射。无剧本时 `line_matches=[]`，零写入，不影响关系表。

**orchestrator 零改动**：现有序列化逻辑 `[asdict(m) for m in l2_output.line_matches]` 对空列表返回 `[]`，无需改 orchestrator。

### 4.3 前端适配

前端 `ScriptDiffView` 现有逻辑：

- `diff` 为 null → 显示「L2 未完成 / 无剧本」
- `summary`、`corrected`、`matches` 都空 → 显示「无偏差」
- 有 `corrected_segments` → 显示原→改列表

无剧本且无纠错（`corrected_segments=[]`）时走「无偏差」分支——可接受，与「无剧本」语义有一定混淆。但 `diff` 不为 null（已有 L2 结果），所以不走「L2 未完成 / 无剧本」分支，显示「无偏差」是合理的兜底。

评估：无剧本有纠错时，显示纠错列表（正确）；无剧本无纠错时，显示「无偏差」（可接受，不引入假数据）。**不改前端**，让后端数据形态适配现成渲染。

---

## 5. 文件改动清单

| 文件 | 改动内容 | Owner |
|---|---|---|
| `backend/llm/tools/script.py` | 新增 `build_l2_no_script_tool()` | backend-agent |
| `backend/llm/config.py` | 新增 `"l2_take_no_script"` task_type 条目 | backend-agent |
| `backend/pipelines/l2_take.py` | `run_l2_take` task_type 分叉、`_build_user_message` 分叉、`_validate_data_dict` 放宽（缺失字段给默认）+ original==corrected 过滤 + detail 归一化 | backend-agent |
| `backend/asr/stream_driver.py` | `_emit` 加繁→简转换（幻觉过滤前）、新增 `_normalize_to_simplified` 函数 | backend-asr（本 spec 定设计，实现交 backend-asr 或 Lead 协调） |
| `pyproject.toml` | 加 `OpenCC==1.3.1` 依赖 | Lead / backend-agent |
| `uv.lock` | 随依赖更新自动生成 | 自动 |
| `backend/tests/test_l2_pipeline.py` | 新增无剧本分支单测（见第 6 节）| backend-agent |
| `backend/tests/test_stream_driver.py` | 新增繁→简集成单测（见第 6 节）| backend-asr |

**不改文件**：`orchestrator.py`、`routes/takes.py`、`ScriptDiffView.tsx`、`db/dal.py`、`l2_constants.py`。

---

## 6. 测试入口

### 6.1 纯函数单测（不需要真实模型）

**`backend/tests/test_l2_pipeline.py`**：

| 测试名 | 覆盖行为 |
|---|---|
| `test_no_script_uses_no_script_task_type` | `script_lines=[]` 时 `infer_tool` 收到 task_type=`l2_take_no_script` |
| `test_has_script_uses_l2_take_task_type` | `script_lines` 非空时 task_type=`l2_take` |
| `test_no_script_user_message_no_script_block` | 无剧本 user message 不含「剧本台词」节 |
| `test_no_script_user_message_no_diff_task` | 无剧本 user message 不含「①找出 insertion」 |
| `test_no_script_output_empty_line_matches` | `report_corrections_only` 输出解析后 `line_matches=[]`、`summary=None` |
| `test_filter_identical_corrected_segments` | `original==corrected` 的段被丢弃，不出现在 `L2Output.corrected_segments` |
| `test_detail_null_string_normalized` | `detail="null"` 归一化为 Python `None` |
| `test_detail_none_string_normalized` | `detail="none"` 归一化为 `None` |
| `test_detail_empty_string_normalized` | `detail=""` 归一化为 `None` |

**`backend/tests/test_l2_tool_schema.py`**（或在现有 tool schema 测试文件里扩展）：

| 测试名 | 覆盖行为 |
|---|---|
| `test_no_script_tool_has_only_corrected_segments` | `build_l2_no_script_tool` schema 不含 `script_diff_summary` / `line_matches` |
| `test_no_script_tool_required_fields` | required 只有 `["corrected_segments"]` |

**`backend/tests/test_stream_driver.py`**（backend-asr 负责，此处列出便于协调）：

| 测试名 | 覆盖行为 |
|---|---|
| `test_emit_normalizes_trad_to_simplified` | `_emit` 输出的 `AsrFinalPayload.text` 已是简体 |
| `test_normalize_idempotent_on_simplified` | 已是简体输入幂等 |
| `test_normalize_filters_trad_hallucination` | 繁体幻觉「謝謝」在转简后被 `_is_hallucination` 过滤，不推送 payload |

### 6.2 smoke 测试（需要真实模型，默认 skip）

`test_no_script_correction_smoke`：给定真实转录（含繁体、含错别字），无剧本路径跑真模型：

- 断言：`line_matches == []`、`script_diff_summary is None`
- 断言：`corrected_segments` 中无 `original == corrected` 项
- 断言：模型未编造 insertion / summary

标记 `@pytest.mark.smoke`，不进 CI 默认流程，需 `--smoke` flag 激活。

**明确说明**：无剧本分支的机制正确性（schema 分叉、prompt 分叉）由单测验证；模型是否真的不乱编 summary/insertion 只能由 smoke 验。单测绿不等于 fix 有效，必须跑 smoke 后才能确认行为修正。

---

## 7. 风险与边界

### 7.1 最大风险：模型合规性（头号风险）

**描述**：4B 模型在精简 tool `report_corrections_only` 下是否真的不再编 summary/insertion，取决于模型在 grammar 约束下遵从 schema 的能力。精简 tool 删掉了 `line_matches` 和 `script_diff_summary` 字段，grammar 层面约束模型只能输出 `corrected_segments`——这是真正能抑制假数据的机制，比单纯改 prompt 更硬。但 4B 规模下如果 grammar 引擎存在 bug 或对 tool_choice 处理有偏差，仍可能出现异常。

**缓解**：smoke 测试 gate；如果 smoke 仍出现假数据，需要进一步收紧 tool schema（去掉 corrected_segments 的 optional 语义描述）或调整 system prompt。

### 7.2 次大风险：opencc 跨 ownership + 历史数据混字

**描述**：繁→简转换改动 `stream_driver.py`（backend-asr 文件），属于跨 ownership 修改，需 Lead 协调。同时历史入库数据不回溯，造成 `transcript_segments` 表中新旧 take 字体不一致（部分繁体、部分简体）。

**缓解**：本 spec 明确 backend-asr 负责实现 3.2，backend-agent 只提设计不碰该文件。历史数据混字影响展示风格，不影响功能正确性；dev 数据库清空重建可消除混字（无需迁移脚本）。

### 7.3 无剧本判定条件

`script_lines == []` 即为无剧本。这是 orchestrator 传给 L2Input 的值：`dal.get_latest_script` 返回 None 时 `script_lines = []`（orchestrator line 371-374）。如果场景有剧本但剧本所有行都被截断到 0 条，也会走无剧本路径——这是极端 edge case，现有截断逻辑保证至少保留 1 行（`_truncate_script_lines` 的 1000 字符上限针对总字符不是行数），实际不会发生。

### 7.4 空 corrected 时前端显示

无剧本且 corrected_segments=[]（无纠错）：`script_diff_dict = {summary:null, line_matches:[], corrected_segments:[]}`，前端三个字段都空，ScriptDiffView 走「无偏差」分支，显示「无偏差」。与「无剧本」语义有轻微混淆，但不引入假数据，可接受。如需区分，前端可判断 `!diff.script_diff_summary && matches.length === 0` 且 diff 存在时显示「无剧本 / 无纠错」——但这属于前端改动，本 spec 不要求，可作为后续 UX 细化。

### 7.5 繁简对已存转录的影响

opencc 转换只对新转录生效（forward only），已入库的旧 `transcript_segments.text` 不变。L2 Pipeline 读旧 take 的转录做 `previous_notes`（只取 `script_diff_summary`），不读 segment text，不受影响。diarization 回填读 `segment.text` 仅用于整合 `structured_transcript` 展示，旧 take 的繁体残留不影响功能正确性。

---

## 8. 依赖与接口契约变更

### 8.1 新增 TASK_CONFIG key

`TASK_CONFIG["l2_take_no_script"]` 是新增条目，不改已有 `"l2_take"` 和其他 key，下游代码零改动。

### 8.2 `L2Output` 字段不变

`L2Output.line_matches` 类型仍是 `list[LineMatch]`，无剧本时值为 `[]`。orchestrator 序列化 `[asdict(m) for m in l2_output.line_matches]` 对空列表返回 `[]`，正确。无需改 `L2Output` dataclass。

### 8.3 `script_diff` JSON shape 不变

前端消费的 `script_diff` 仍是 `{script_diff_summary, line_matches, corrected_segments}` 三字段结构，无剧本时值为 `{null, [], [...或空]}`。无需改前端 TS 类型（`ScriptDiff` 已有 `script_diff_summary: string | null` 和 `line_matches: LineMatch[]`）。

### 8.4 opencc 依赖是新外部依赖

`pyproject.toml` 加 `OpenCC==1.3.1`，需要 `uv lock` 更新 lock 文件。两平台均有预编译 wheel，无编译需求。Lead 需评估是否在 Sprint 内引入此依赖。

---

## 9. 实施顺序建议

1. backend-agent：`script.py` 新增 `build_l2_no_script_tool` + 单测（红→绿）
2. backend-agent：`config.py` 新增 `l2_take_no_script` + 单测（红→绿）
3. backend-agent：`l2_take.py` 分叉逻辑 + 单测（红→绿）：task_type 选择、user message 分叉、_validate_data_dict 放宽、original==corrected 过滤、detail 归一化
4. Lead 协调 backend-asr：`stream_driver.py` 加繁→简转换 + 单测
5. Lead：`pyproject.toml` 加 opencc 依赖 + `uv lock`
6. 集成 smoke 测试：无剧本真模型验证

步骤 1-3 完全在 backend-agent ownership 内，可独立推进。步骤 4-5 有 ownership 和依赖协调，需 Lead 介入。
