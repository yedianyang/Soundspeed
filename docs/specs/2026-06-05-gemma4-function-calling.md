# Spec: Gemma 4 Function Calling 接入 LLM 层

版本：v0.1（草稿）
日期：2026-06-05
状态：草稿，待 Lead 评审

变更记录：
- v0.1（2026-06-05）：初稿，基于真模型 spike 实测（llama.cpp + Gemma 4 E4B Q4_K_M GGUF，详见正文）。

依赖 spec：
- llm-service-design v1.1（`docs/specs/2026-05-25-llm-service-design.md`）B4：messages 协议、多模态接口预留
- llm-backend-selection v0.4（`docs/specs/2026-05-27-llm-backend-selection.md`）§11.3：llama-cpp-python + Q4_K_M + n_ctx=8192
- realtime-diarization-voicenote-design（`docs/specs/2026-06-02-realtime-diarization-voicenote-design.md`）§5/§8：VoiceNote 路由、ch2 note 区

对接背景：Gemma4 Hackathon Track A Agent，function calling 是硬性评分项，deadline 2026-06-08。

---

## 1. 背景

llm-service-design v1.1 B4 决议把推理入口升到 `infer(messages: list[dict])`，并在开放问题 4 中明确留了「Gemma 4 function calling 格式待 1.F 实测」。本 spec 填这个坑，并把 L2 pipeline 改造成以工具调用方式产出结构化结果，替代现有的 Markdown fence + `json.loads` 解析路径。

同期背景：Gemma4 Hackathon Track A 要求 agent 流程中有真实 function calling，且需在截止日（2026-06-08）前可演示。L2 改 function calling 是最小可演示路径，兼顾产品需求与参赛评分。

---

## 2. 技术背景：官方格式与 llama.cpp 行为

### 2.1 官方模板

Google 官方文档（ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4）规定 Gemma 4 function calling 格式如下。

工具声明走 `tools=` 参数，标准 JSON Schema 结构（type/function/name/description/parameters）。模型生成工具调用时吐出原生 FunctionGemma 格式：

```
<|tool_call>call:NAME{arg:<|"|>value<|"|>}<tool_call|>
```

工具结果作为 assistant 消息的 `tool_responses` 字段传回：

```
<|tool_response>response:NAME{...}<tool_response|>
```

官方用 transformers `apply_chat_template` 渲染，`enable_thinking=True` 可提升 function calling 准确率。

### 2.2 GGUF 模板验证

经 dump 确认，unsloth Gemma 4 E4B GGUF 的 `tokenizer.chat_template`（16804 字符）就是完整 FunctionGemma 模板，含 `<|tool>`/`<|tool_call>`/`<|tool_response>` 全套宏。llama-cpp-python 的 `Jinja2ChatFormatter` 在 `create_chat_completion(tools=...)` 调用时会把 `tools` 透传进模板 render，渲染出的工具声明与官方格式一致。

### 2.3 spike 实测结论（Tier 1 选型依据）

以下三组探针均在 llama.cpp + 真 E4B Q4_K_M GGUF 上跑出，作为本 spec 的选型依据。

**探针 A：天气工具 + `tool_choice="auto"`**

返回原生 FunctionGemma 字符串：
```
<|tool_call>call:get_current_temperature{location:<|"|>Tokyo<|"|>}<tool_call|>
```
special token 不被 llama.cpp 吞，官方正则可解析扁平标量参数。

**探针 B：L2 工具 + `tool_choice` 强制指定函数**

llama-cpp-python 自动启用 JSON grammar 约束，返回 OpenAI 风格结构化响应：
- `finish_reason = tool_calls`
- `content = None`
- `tool_calls[0].function.arguments` 是合法 JSON 字符串
- 嵌套数组（line_matches、corrected_segments）全部正确
- E4B 把错别字「茶手→插手」自动纠正

**探针 C：L2 工具 + `tool_choice="auto"`**

content 吐原生 FunctionGemma 嵌套格式，官方正则解析嵌套数组在第一个逗号处截断，崩溃。FunctionGemma 嵌套参数解析是已知脆点。

**选型决策（D-FC-01）**：Tier 1（单一已知输出工具，如 L2）一律走 `tool_choice` 强制路径，拿结构化 JSON `tool_calls`，不写 FunctionGemma 解析器，最稳。FunctionGemma 正则解析只在 Tier 2 多工具 auto 路径才需要（见 §4.2）。

---

## 3. 架构：两层 + 工具注册表

### 3.1 Layer 1：模态前端

把不同模态字节转换成 Gemma 能读的文本，**function calling 不负责模态转换**。

| 输入模态 | 转换方式 | 优先级 |
|----------|----------|--------|
| 转录文本（ch1 ASR segments）| 直接组装文本，无需转换 | P1，本次实现 |
| 语音 note（ch2 voice note） | Gemma 4 原生音频输入（mtmd），或 whisper ASR 中转 | P3，见 §5 |
| 拍照剧本 | Gemma 4 视觉输入（mtmd mmproj），或 OCR 中转 | P3，见 §5 |
| 完整剧本文件 | 文本读取 + 分块（n_ctx=8192 上限） | P2，SP pipeline |

### 3.2 Layer 2：Gemma 工具路由

给定文本内容 + 按上下文裁过的工具集，Gemma 选工具调用。

4B 模型不适合大工具箱自由路由。入口（UI 动作/channel）已能裁小工具集，Layer 2 只看到当前场景适用的 1~3 个工具。L2 pipeline 场景只给 `report_script_analysis` 一个工具，用 `tool_choice` 强制调用，grammar 保证输出合规。

### 3.3 工具注册表

落在 `backend/llm/tools/`：

```
backend/llm/tools/
├── __init__.py
├── registry.py       # 名字 → (json_schema, python_executor)
├── script.py         # report_script_analysis（本次实现）
├── note.py           # note 相关工具（待 NP pipeline 接入）
└── transcript.py     # 转录查询类工具（待 QP pipeline 接入）
```

`registry.py` 暴露：

```python
def get_tool_schema(name: str) -> dict: ...
def get_executor(name: str) -> Callable: ...
def list_tools(domain: str | None = None) -> list[str]: ...
```

L2 转换产出的 `report_script_analysis` 是注册表第一个工具。

---

## 4. 工具定义

### 4.1 report_script_analysis 完整 JSON Schema

```python
REPORT_SCRIPT_ANALYSIS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "report_script_analysis",
        "description": (
            "报告本次 take 的转录文本与剧本台词对比结果，"
            "包含逐行匹配情况、替换/遗漏位置和纠错后文本。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script_diff_summary": {
                    "type": "string",
                    "description": "整体对比摘要，50 字以内"
                },
                "line_matches": {
                    "type": "array",
                    "description": "逐行匹配结果",
                    "items": {
                        "type": "object",
                        "properties": {
                            "line_no": {
                                "type": "integer",
                                "description": "剧本行号（1-indexed）"
                            },
                            "diff_type": {
                                "type": "string",
                                "enum": ["match", "missing", "substitution", "insertion"],
                                "description": "匹配类型，与代码 _VALID_DIFF_TYPES 同源"
                            },
                            "detail": {
                                "type": "string",
                                "description": "具体差异描述，substitution 时写出实际说的内容"
                            }
                        },
                        "required": ["line_no", "diff_type", "detail"]
                    }
                },
                "corrected_segments": {
                    "type": "array",
                    "description": "需要纠错的转录片段（仅限错别字/口误，不含剧本差异）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "idx": {
                                "type": "integer",
                                "description": "转录段索引（0-indexed）"
                            },
                            "original": {
                                "type": "string",
                                "description": "原始转录文本"
                            },
                            "corrected": {
                                "type": "string",
                                "description": "纠错后文本"
                            }
                        },
                        "required": ["idx", "original", "corrected"]
                    }
                }
            },
            "required": ["script_diff_summary", "line_matches", "corrected_segments"]
        }
    }
}
```

`diff_type` 的 enum 值（match/missing/substitution/insertion）必须与 `backend/pipelines/l2_take.py` 里的 `_VALID_DIFF_TYPES` 集合保持严格同源，不单独维护。

### 4.2 Tier 2 工具路由（远景，本次不实现）

多工具 auto 路由需要健壮的 FunctionGemma 嵌套解析器，候选方案：

1. 手写健壮嵌套解析器（正则 + 状态机）
2. 两步走：auto 路径先选工具名，再 forced 路径取参数
3. 改走 Ollama 原生 tool_calls（Ollama 已返回 OpenAI 格式，省去解析）

三种方案均留作 Tier 2，不在本次实现范围内。

---

## 5. 多模态（Layer 1 mtmd）现状

llm-backend-selection v0.4 §11.1 确认：llama-cpp-python 0.3.25 prebuilt wheel 自带 libmtmd，Gemma 4 多模态需额外 mmproj-F16.gguf（unsloth repo 已有），已标 P3 延后。

llm-service-design v1.1 B4 / 开放问题 3：`infer` 的 messages content 块预留 image/audio 接口，拍照剧本第二阶段才需要，MVP 未启用。

realtime-diarization-voicenote-design §5 / §8：ch2 语音 note 目前走 whisper ASR 转文本后交 LLM 归置，原生音频输入（Gemma 4 audio 通路）属于 Gemma audio spike（参见 memory `reference_gemma_audio_mtmd.md`）的后续工作。

**结论**：拍照剧本（image content）与语音 note 直接入模型（audio content）这两条 Layer 1 路径都需要 mtmd + mmproj，目前均未接线，属 Tier 2/远景，P3。本次 spec 覆盖范围仅为文本输入路径（Layer 1 直通）。

---

## 6. Tier 1 落地改动点

本次只改 L2 pipeline，入口（L2 组装 Gemma 请求）和出口（L2 返回 L2Output）之间的上下游（diarization 触发、takes 写库、take.changed 广播）无感知。

### 6.1 backend/llm/config.py

`TASK_CONFIG["l2_take"]` 新增两个字段：

```python
"l2_take": {
    "max_tokens": 512,
    "temperature": 0.2,
    "priority": 2,
    "system": "...",
    # 新增：
    "tools": [REPORT_SCRIPT_ANALYSIS_SCHEMA],
    "tool_choice": {"type": "function", "function": {"name": "report_script_analysis"}},
},
```

### 6.2 backend/llm/service.py

当前 `infer` 末尾硬取：

```python
return result["choices"][0]["message"]["content"]
```

强制调用路径下 `content = None`，这行会返回 `None` 而非抛 `LookupError`（后续 pipeline 静默失败）。需要让 `infer` 能识别 `finish_reason == "tool_calls"` 路径并返回 `message` 整体，或增加一个变体入口。

**方案（D-FC-02）**：`infer` 返回类型泛化为 `str | dict`：当 `finish_reason == "tool_calls"` 时返回 `message` 字典（含 `tool_calls`），否则返回 `content` 字符串，保持现有调用方兼容。各 pipeline 按自身预期类型断言，收到意外类型时 raise。

此改动影响 `service.py` 返回值契约，**需同步更新 llm-service-design spec（v1.2）**，由 Lead 评审后操作。

### 6.3 backend/pipelines/l2_take.py

解析逻辑从现有路径改为：

```python
# 原路径（删除）
raw = _strip_markdown_fence(content)
data = json.loads(raw)

# 新路径
message = infer_result  # dict，含 tool_calls
args_json = message["tool_calls"][0]["function"]["arguments"]
data = json.loads(args_json)
```

之后复用现有字段校验：`_VALID_DIFF_TYPES` 枚举检查、`line_no`/`idx` 类型校验，最终组装 `L2Output`。

`L2Input`/`L2Output` 数据契约不变，不触碰上下游。

---

## 7. 测试金字塔

以下各层单独可测，自下而上：

**Layer 0：渲染验证**
不加载权重，仅验证 Jinja2 chat 模板渲染结果。传入 `tools=[REPORT_SCRIPT_ANALYSIS_SCHEMA]` 后，渲染出的字符串包含 `<|tool>declaration:report_script_analysis`。纯 Python，毫秒级。

**Layer 1：工具 schema 构造器**
纯函数测试。断言 `REPORT_SCRIPT_ANALYSIS_SCHEMA` 结构合规（type/function/name/description/parameters 各字段存在），断言 `diff_type` enum 值集合等于 `_VALID_DIFF_TYPES`（同源校验）。无 IO，毫秒级。

**Layer 2：tools / tool_choice 透传**
用 `_CapturingClient`（mock）替换底层 llama-cpp-python，断言 `create_chat_completion` 收到的 kwargs 包含 `tools` 和 `tool_choice`，且值与 `TASK_CONFIG["l2_take"]` 一致。

**Layer 3：tool_calls → L2Output 映射**
用 `StubClient` 返回固定的结构化 `tool_calls` response（模仿探针 B 结构），断言解析后的 `L2Output` 字段正确，复用现有 `_VALID_DIFF_TYPES` / `line_no` / `idx` 类型校验逻辑。Tier 1 路径不需要 FunctionGemma 解析器，这层也不测 FunctionGemma。

**Layer 4：pipeline 集成**
镜像现有 `run_l2_take` 测试结构，把 client 换成 StubClient，走完整 `l2_take.py` 执行路径，断言 `L2Output` 合规。

**Layer 5：真模型 smoke**
标 `@pytest.mark.smoke`，读环境变量 `GEMMA_MODEL_PATH`，未设置则 `pytest.skip`。跑完整 L2 pipeline，断言输出的 `L2Output` 结构属性（有 `line_matches` 字段且是 list），不断言内容。默认 CI 不运行，本地手跑验收。

**约定**：Layer 0~4 一律 StubClient/mock，不加载模型。真模型调用只在 Layer 5 smoke 里出现。

---

## 8. 风险表

| 风险 | 等级 | 影响 | 对策 |
|------|------|------|------|
| E4B 多工具自由路由准确率不足 | 中 | Tier 2 agent 流程不稳 | 入口裁小工具集；Tier 1 用强制路径绕过 |
| `infer` 返回类型泛化的回归面 | 高 | SP/NP/QP pipeline 静默失败 | 各 pipeline 加断言；Layer 2 测试覆盖 `tool_calls` 与 `content` 两条分支 |
| Tier 2 FunctionGemma 嵌套解析 | 中 | 嵌套数组截断（已实测） | Tier 2 另起 ticket；候选三方案见 §4.2 |
| mtmd 未接线（图像/音频 Layer 1） | 低（P3） | 拍照剧本/语音 note 无法直接入模型 | 现有 whisper ASR + 文本中转兜底；mmproj 加载路径已文档化（backend-selection §9.2） |
| 3 天 deadline（截止 2026-06-08）| 高 | Hackathon 评分 | Tier 1 + Layer 0~3 测试为最小可演示范围，优先完成 |

---

## 9. 验收标准

- [ ] `REPORT_SCRIPT_ANALYSIS_SCHEMA` 的 `diff_type` enum 与 `_VALID_DIFF_TYPES` 同源校验通过
- [ ] Layer 0 渲染测试：传 `tools=` 后模板输出含工具声明 token
- [ ] Layer 2 透传测试：`_CapturingClient` 断言 `tool_choice` 强制字段到达底层
- [ ] Layer 3 映射测试：StubClient 返回结构化 `tool_calls`，`L2Output` 构造正确
- [ ] Layer 4 pipeline 集成：现有 `l2_take` 测试在 StubClient 下通过
- [ ] Layer 5 smoke：本地有真模型时 `pytest -m smoke` 通过，断言 `L2Output.line_matches` 是 list
- [ ] `service.py` 改动不破坏 SP/NP/QP 现有 pipeline 测试（`content` 分支回归）
- [ ] 上下游（diarization 触发、takes 写库、take.changed 广播）无改动，集成测试不回归

---

## 修订记录

v0.1（2026-06-05）：初稿，基于真模型 spike 实测（llama.cpp + Gemma 4 E4B Q4_K_M GGUF，探针 A/B/C）。工具注册表结构、Tier 1 改动点、测试金字塔约定由本 spec 首次确立。
