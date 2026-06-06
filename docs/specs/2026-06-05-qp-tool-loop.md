# QP (Query Pipeline) Tool-Loop 设计

- 日期：2026-06-05
- 状态：设计稿（待评审）
- 关联：`docs/specs/2026-06-05-gemma4-function-calling.md`（FC Tier 1/Tier 2 选型）、`docs/specs/2026-05-26-system-architecture.md`（QP/`query.request`/`qp.answer`）、`docs/specs/2026-05-25-llm-service-design.md`（`query_session` P1）、`docs/specs/2026-05-27-sqlite-schema.md`
- 调研来源：本设计的关键事实由一轮只读核查（5 个 agent，针对工具注册、memo 派发、场次匹配、thinking/两步走可行性、只读 SQLite）实测得出，下文以 ✅实测 标注。

---

## 0. 背景与范围

QP（Query Pipeline）是四条 LLM 管线里的查询管线：用户用自然语言提问，QP 从数据库检索并返回信息（不给建议，只给事实）。例：「第一场拍了多少条」「第 72 场在哪拍的」「有几个角色」。

本设计把 QP 落成一个**真正的 tool-loop（agentic loop）**：Gemma 自己决定调哪个工具、看结果、必要时再查，最后合成自然语言回答。这对应 FC spec §4.2 标为「远景，本次不实现」的 Tier 2，本设计是它的首次落地。

### 0.1 一句话定位

QP 内核是仓库里**第一个 Tier 2 多工具 agentic 循环**，也是工具注册表 `executor` 槽位的**第一个真实消费者**（此前 L2/NP 的 Tier 1 都用 forced 单工具，executor 一直是 `None`）。

### 0.2 v1 范围（本设计交付）

- QP 内核：工具集 + 两步走循环 + 数据库只读查询路径 + 直连入口 `POST /query`。**全部不依赖 NP，现在就能在 main-based 分支建并独立 demo。**
- 输出：自然语言文本。

### 0.3 明确延后（非本设计交付，文中标注落点）

- 入口分类器（memo 框 note/query 分流）——依赖 4.x NP 栈，等 4.x 合并后接入。
- 语音查询——结构性原因（见 §3.2），v2。
- 结构化输出 `data/refs`——v2。
- 场次别名表（格式变体）——v9 迁移，延后。
- thinking 开关——可选准确率增强，内核跑通后再加（见 §8）。

---

## 1. 决策摘要

| ID | 决策 | 依据 |
|----|------|------|
| D-QP-01 | 入口 = 共用 memo 框，前置 **forced 二分类器**分 note/query（option 1）；循环写成通用 `run_tool_loop(messages, toolset)`，toolset 作参数，为日后 option 2 留口 | forced 单工具是 FC spec 验证最稳的 Tier 1 路径；不动已建好的 NP |
| D-QP-02 | 查询层 = **方案 C**：策展工具（hot path 可靠）+ `query_database` 万能笔（覆盖所有表） | 用户拍板「赌 C」，要「能碰任意表」的全能 agent 形态 |
| D-QP-03 | 循环 = **统一两步走**：auto 跳抠工具名（稳）→ forced 跳用 grammar 出干净 JSON 参数；**最多 5 跳** | ✅实测 auto/forced 两条路径都在；绕开 probe C 的嵌套数组截断 |
| D-QP-04 | 万能笔 = 每查询开**临时 `mode=ro` 连接** + `set_authorizer` 只放行 SELECT（挡 FTS 影子表）+ **300 行封顶 + 3s 超时**；SQL 错回喂循环自纠 | ✅实测 mode=ro 拦写但拦不住 ATTACH，须配 authorizer |
| D-QP-05 | 场次解析 = 场次目录注入模型 + `normalize_scene_code`（常见）；**找不到就说没有，禁止模糊替换**；格式变体（`Sc_72/Scene72/S72`）用 **alias table（v9，延后）**；数字不同 = 不同场，不匹配 | 用户更正：硬情况是同号不同前缀，非跨命名空间 |
| D-QP-06 | 输出 v1 = 自然语言文本；结构化 `data/refs` 延后 | deadline；先跑通 |
| D-QP-07 | thinking 延后，仅用于 auto 跳；需改 `client.py`（Jinja 子类，不走私有属性） | ✅实测 GGUF 模板支持，但 `create_chat_completion` 无 `**kwargs`；grammar 跳上 thinking 无效 |
| D-QP-08 | 范围次序：**内核现在在 main-based 分支建 + 直连 `POST /query` 独立 demo**；入口分类器等 4.x 合并接 `POST /notes`；语音查询 v2 | ✅实测 NP 栈仅在 4.x（领先 main 36 commit，未合并） |
| D-QP-09 | 工具注册**照搬** L2/NP 机制（`build_X_tool()` → registry → TASK_CONFIG）；config 按 main 现有 **eager** 写法加，QP 工具只 import 中性叶子模块避开循环；**合并 4.x 时重贴到其 lazy 结构**（见 memory `project_qp_tool_loop`）；QP 是 executor 槽首个真实消费者 | ✅实测 4.x 把 config 改成 lazy helper，两分支 config.py 风格不同 |
| D-QP-10 | 循环编排住在 **QP pipeline**（`backend/pipelines/qp_query.py`），反复调 `service.infer`(auto)/`infer_tool`(forced)；service 仅加一个**按调用覆盖 tool_choice/tools** 的小钩子 | service 现有队列/锁契约不动，循环逻辑内聚在 pipeline |
| D-QP-11 | **prompt 预算优先**：hero 问题靠策展工具（手写 SQL，几乎零 schema 上下文）；`query_database` 是真·escape hatch，只在长尾用、带有界上下文；few-shot 钉「查一次就回答」防 run-on（详 §5.5） | memory `feedback_prompt_attention_zero_sum`：4B prompt 越长越乱，方案 C 的长 prompt 与之顶上，须把重活挪到无需上下文的策展工具 |
| D-QP-12 | **所有 QP 读走只读连接**：策展工具与万能笔都不碰共享 `self._conn`；万能笔额外加 authorizer/行数封顶/超时 | ✅实测 executor 在 `to_thread` worker 跑，碰共享连接是跨线程并发隐患（核查原话） |

---

## 2. 架构：内核 + 入口 两层

QP 干净地拆成两层，依赖边界清晰：

```
┌─────────────────────────── 入口层（Entry，依赖 4.x，延后接入）────────────────────────┐
│  memo 框（文字/语音，与 NP 共用）                                                      │
│        │ 文字: POST /notes（4.x）        │ 语音: POST /notes/voice（4.x，见 §3.2）      │
│        ▼                                                                               │
│  ① 分类器（forced 单工具 note|query）   ← 仅文字有 pre-model 拦截点                    │
│   ┌────┴─────┐                                                                         │
│ note         query                                                                    │
│   │            │                                                                       │
│   ▼            ▼                                                                       │
│ 现有 NP    （进内核）                                                                  │
└────────────────┼──────────────────────────────────────────────────────────────────┘
                 │
┌────────────────┼─────────────────── 内核层（Core，本设计 v1，main 即可建）───────────┐
│  直连入口 POST /query ──────────────┤  ← v1 demo 走这条，不依赖 4.x                   │
│                                     ▼                                                  │
│  ② QP 工具循环 run_tool_loop（两步走，≤5 跳）                                          │
│        ├─ 工具集：count_takes / get_scene_info / list_characters /                     │
│        │          search_script_lines / query_database                                │
│        ├─ executor 调 DAL 只读方法 / DAL.query_readonly（万能笔安全墙）                │
│        └─ 出口：自然语言文本 → WS qp.answer.{conn_id}（或同步 HTTP）                   │
└───────────────────────────────────────────────────────────────────────────────────┘
```

要点：内核对 NP 零依赖，`POST /query` 让它在 main 上独立可跑可 demo。入口层是薄薄一层，等 4.x 把 NP 栈合进 main 再补，落点是 `POST /notes`（文字路径，见 §3）。

---

## 3. 入口层（延后，落点已定）

> 本节描述入口分类器的设计，**实现等 4.x 合并**。v1 内核用 `POST /query` 直连，不需要本节。

### 3.1 文字路径分类器（option 1）

memo 框文字经 `POST /notes`（4.x，`backend/api/routes/takes.py:325 create_note`）进来，在 `parse_note()` 之后、`orchestrator.run_np_async(...)` 之前插入分类器：

- 新 task_type `memo_route`：forced 单工具 `route_memo(kind)`，`kind` 是扁平枚举 `["note","query"]`。forced + grammar，零解析风险（Tier 1 最稳路径）。
- `note` → 原样进现有 NP（不动）；`query` → 进 QP 内核循环。

可迁移性（D-QP-01）：循环写成 `run_tool_loop(messages, toolset)`，toolset 作参数。日后切 option 2（把 `record_note` 也变成循环里的工具）只需「砍分类器 + 把 record_note 加进 toolset + 入口指向统一循环」。前提是 NP 的 note 结构化逻辑保持可复用——✅实测 `run_np_note`/`run_np_voice`/`_validate_data_dict`（`np_note.py`）本身是纯函数可复用，但落库副作用（`insert_note`+`set_take_status`+WS publish）缠在 `Orchestrator._finalize_np` 里，切 option 2 时需把它抽成独立可调用——这是迁移的主要成本，记录在案。

### 3.2 语音路径：v1 不做（结构性原因）

✅实测：文字在进模型前有 `body.text` 可分类（便宜的 pre-model 拦截点）；**语音没有**——`POST /notes/voice` 走 Gemma 原生音频，进模型前没有文本，`_run_np_voice_async` 用 `raw_text=''` 占位、由模型边听边判。所以 forced 二分类器对语音**结构上做不了**。

结论：v1 只做文字查询。语音查询要么进 v2 的 option 2 形态（模型选工具即分类），要么等语音先转文本——均延后。这不影响 demo：文字分类器已经交付「一个聪明框，自己分辨记录还是提问」的 agentic 叙事。

### 3.2.1 QP-voice 设计方向（v2，2026-06-06 评审定向）

**目标形态**：语音 memo → **一个多模态 auto-tool 模型调用**完成 ASR + 分类 + 路由 + 回答，全在 Gemma 4 原生音频里（不用 whisper、少一跳）。这正是上面「option 2（模型选工具即分类）」的落地路径，且**顺手解了 §3.2 的分类器结构难题**——语音进模型前没文本无法预分类，但模型边听边选工具时，**它选了 QP 工具 = 判定为查询、走 note 结构化 = 判定为记录**，分类内生于 auto 路由。

**可行性（两半边已独立证实，剩一个不确定性）**：
- 音频 → FunctionGemma tool call：4.x NP-voice 已做（audio + **forced** structure_note，真模型 smoke 验过）。模型「听音频吐 tool call」可行。
- auto 多工具路由：QP-text 已做（Task 11，模型看到 5 工具自选）。
- **唯一待验**：4B 在「音频 + 看到 N 个工具声明」时 **auto 选工具**的可靠性（auto 比 forced 难，模型要真看懂工具去选，非 grammar 焊死）。需真语音 spike。

**实现要点（option a，比当前多模态 text-only swap 深一层）**：让 `MultimodalGemma4Handler` 的 prompt **既保留音频嵌入（mtmd/load_image）、又注入 FunctionGemma 工具声明**（`<|tool>declaration:...<tool|>`）。当前 Task 11 的修复只把 **text+tools** 切到原生 FunctionGemma formatter，**audio+tools 仍走多模态 handler、不渲染工具**——故 audio+auto 现在走不通（forced 靠 grammar 兜不需渲染，auto 需要）。QP-voice 要落地必须做这层 handler 改造，详见 client.py `create_chat_completion` 的前瞻注释 + memory `project_qp_tool_loop`。

**spike 验法（时盒，本期不做）**：真语音 WAV（「第一场拍了多少条」）+ 给多模态 handler 注入工具声明 → 看模型是否 auto 调对工具。验通了 QP-voice + 分类器一并设计。

---

## 4. 内核：工具集（方案 C）

5 个工具，卡在 FC spec「4B 路由 1~3 个最优、别超太多」的上限附近。**所有参数必须是扁平标量**（string/int/bool），不许嵌套数组/对象——✅实测 auto 跳的 FunctionGemma 字符串解析对扁平标量稳（probe A），对嵌套数组截断崩溃（probe C）。

| 工具 | 参数（全扁平） | executor 做什么 |
|------|----------------|-----------------|
| `count_takes` | `scene_ref: str`, `status?: str` | DAL 计数，软删 `deleted_at IS NULL` 过滤写死在 executor |
| `get_scene_info` | `scene_ref: str` | 返回 location / int_ext / time_of_day / shoot_date / 角色数 |
| `list_characters` | `scene_ref: str` | `script_lines.character` 去重清单 |
| `search_script_lines` | `query: str`, `scene_ref?: str` | 走 `script_lines_fts` FTS5 MATCH（BM25 排序） |
| `query_database` | `sql: str` | 万能笔，只读 SQL，兜一切长尾（安全墙见 §6） |

策展工具的 SQL 由我们手写在 executor 里、必对、安全；万能笔是模型现写、需安全墙。常见问题走策展工具（可靠），冷门问题才动万能笔。

### 4.1 注册机制（D-QP-09，照搬 L2/NP）

✅实测 canonical 注册三步，QP 逐字照抄：

1. `backend/llm/tools/transcript.py`：每个工具一个 `build_X_tool()`（或一个 builder 返回 list），返回 OpenAI 风格 tool dict，`name == schema["function"]["name"]`。任何枚举/常量从中性 leaf 模块 **lazy import**，避开 `config → tools → pipeline → config` 循环。
2. `backend/llm/tools/registry.py` `_bootstrap()`：注册每个工具，**传真实 `executor=<callable>`**（QP 是 `executor!=None` 与 `get_executor()` 的首个真实使用者，executor 契约见 §5.3）。
3. `backend/llm/config.py`：按 main 现有 **eager** 写法给 `query_session` 挂 `tools=[...]` + `tool_choice="auto"`（不是 forced）；QP 工具只 import 中性叶子模块避开 `config→tools→pipeline→config` 循环。所有生成参数不进 `_META_KEYS`，service 自动透传。**合并 4.x 时须把此条重贴到 4.x 的 lazy `_build_X_task_config()` 结构上**（已记入 memory `project_qp_tool_loop`）。

QP 工具挂在 `query_session`（P1，✅实测它现在有 system prompt 但无 tools 字段——本设计补上 tools + `tool_choice="auto"`），不是 `agent_init`（那是另一个预留的 5 轮 Agent 占位）。

---

## 5. 内核：两步走循环（D-QP-03，核心）

### 5.1 机制

每一跳统一两步，**彻底不在脆弱的 auto 字符串里解析参数**：

```
run_tool_loop(messages, toolset, max_hops=5):
  for hop in 1..max_hops:
    # step A — auto 跳：service.infer(task='query_session', tool_choice='auto'[, thinking])
    #          返回 FunctionGemma content 字符串
    text = infer(...)
    name = scrape_tool_name(text)        # 正则 <|tool_call>call:(\w+)  —— 名字在第一个 { 之前，铁稳
    if name is None:                     # 没有 tool_call、吐的是自然语言 → 最终答案
        return text
    # step B — forced 跳：service.infer_tool(tool_choice=forced(name))
    #          grammar 约束 JSON，参数被正确转义（probe B 路径）
    args = infer_tool(..., tool_choice={"type":"function","function":{"name":name}})
    # step C — 执行 + 回喂
    result = registry.get_executor(name)(args)     # 见 §5.3
    messages += [assistant_tool_call(name,args), tool_response(name,result)]
  return fallback("查询轮数超限")        # 兜底，正常用不到
```

✅实测可行性：auto 路径（`infer` 返回 content）与 forced 路径（`infer_tool` 返回 `tool_calls[0]`）都在；forced 时 llama.cpp 自动建 JSON grammar，参数干净。这正是 FC spec §4.2 候选 2「两步走：auto 选名、forced 取参」，本设计**统一用到每一跳**，连策展工具的字符串参数（搜索词带 `}`、中文逗号）也不会破解析。

✅**真模型 probe 实测回填**（2026-06-06，gemma-4-E4B Q4_K_M，实现计划 Task 7.5，commit `84428fa`）：
- **auto 跳确返 content 串**：`finish_reason=stop` + content=`<|tool_call>call:NAME{...}`（`tool_calls=None`），`service.infer` 正常返回、不撞「content=None+finish_reason=tool_calls」护栏，正则抠名成功。两步走 step A 成立。
- **多跳回喂格式（关键修正）**：OpenAI 风格 `assistant{content:None,tool_calls}` + `role=tool` 回喂会撞这个 GGUF 的 Jinja `UndefinedError: 'raise_exception' is undefined`（状态相关、不稳）。**改用纯文本回喂**：`assistant`=auto 步原始 content（模型自吐 `<|tool_call>`）+ `user`=`f"工具 {name} 返回：{json}"`，实测 3 次确定性稳定渲染 + 自然语言收尾。`<|tool_response>` 标记格式亦可但不如纯文本稳。
- 副发现：llama.cpp 0.3.25 Metal 退出期 `GGML_ASSERT` teardown 崩溃（已知上游 bug，结果在崩溃前产出，不影响断言）。

🔴**根因级共享层 bug（Task 11 e2e 挖出，commit `44c5da2`）**：QP auto 经 service 不调工具。根因——4.x 多模态单实例（方案 A）给 Llama 装 `MultimodalGemma4Handler`（继承 llama_cpp `Gemma4ChatHandler`），其 `CHAT_FORMAT` **替换了 GGUF 内嵌 FunctionGemma Jinja 模板、不渲染工具声明** → 带 tools 的文本请求模型看不到工具。L2/NP 用 forced tool_choice 靠 grammar 兜没暴露，QP **auto** 暴露。**任何 post-4.x 经 service 走 FunctionGemma auto 工具调用都会撞**。修复：`GemmaClient` 构造时从 GGUF `tokenizer.chat_template` 另建原生 FunctionGemma `Jinja2ChatFormatter`（bos/eos 必须 `detokenize(special=True)`），text+tools 请求临时换上（`_lock` 串行无竞态），音频/图像仍走多模态 handler。✅ QP e2e + L2/NP forced FC smoke 全绿零回归。

### 5.2 终止与跳数

- 终止：某跳模型不再吐 tool_call、直接给自然语言 → 返回该文本。
- 上限：**5 跳**（D-QP-03，用户定）。超限走兜底文案。正常问题 1~2 跳。
- **防 run-on**：走到 5 跳兜底「查询轮数超限」是糟糕的 demo 结局。靠 §5.5 的 few-shot 钉死「查一次（至多两次）就回答，别反复查」，把循环正常收在 1~2 跳，兜底只作保险。
- 代价：每跳两次模型调用。一条问题约 3~5 次调用，本地 E4B 秒级，可接受。

### 5.3 executor 契约（QP 首次定义）

✅实测 registry 的 `executor!=None` 路径在生产从未跑过。QP 首个消费者，需定契约：

- 签名：`executor(args: dict) -> dict`（已 JSON 解析的参数进，结构化结果出）。
- 同步函数；在 service 的 `asyncio.to_thread` worker 线程上下文附近执行——所以**所有** QP 的 DB 访问（策展工具 + 万能笔）必须线程安全：一律走只读连接，**不碰共享 `self._conn`**（D-QP-12，见 §6）。
- 错误不抛穿循环：executor 内部把 DB 错误/越权/超时**包成友好错误串**塞进结果，让模型下一跳自纠。

### 5.4 service 的最小改动

循环编排住在 `backend/pipelines/qp_query.py`（D-QP-10），不给 service 加 round-trip。唯一需要的 service 改动：`infer`/`infer_tool` 支持**按调用覆盖 `tool_choice`/`tools`**（forced 跳要动态强制某个工具名）。当前 `tool_choice` 来自静态 TASK_CONFIG；加一个可选覆盖参数即可，与现有 `gen_kwargs` 透传一致，改动小且不破坏现有调用方。

### 5.5 上下文注入与 prompt 预算（D-QP-11）

这是方案 C 成败的最大变量：QP 的 system prompt 里塞什么，直接决定 `query_database` 能不能写对 SQL。但它和 memory `feedback_prompt_attention_zero_sum` 顶上了——4B prompt 越长越乱（u5 极简反超堆细则版），而方案 C 想要的「schema DDL + 场次目录 + 角色目录 + few-shot SQL + 只给事实不给建议 + 找不到说没有」恰好是会拖垮 4B 路由与 SQL 准确率的长 prompt。这个张力在用户拍 C 时还没摆上台面，必须在 spec 里钉死。

化解（强化方案 C，不重开）：

- **重活挪给策展工具**：demo 的 hero 问题（计数 / 场景信息 / 角色清单 / 台词搜索）全走策展工具。它们 SQL 我们手写、executor 自己解析 `scene_ref`，**几乎不需要 schema 上下文**——模型只要选对工具、填对扁平参数。这同时降了解析风险和 prompt 预算。
- **`query_database` 是真·escape hatch**：只在策展工具覆盖不了的长尾才用。它要的上下文（精简 schema + 目录）只在「模型已决定动万能笔」时才有意义——分层处理：auto 跳的 system prompt 保持极简（够选工具即可），万能笔的 schema 卡片放进它的工具 description / 一次性 few-shot，不长期占 auto 跳预算。
- **prompt 最小集合（待 spike 验）**：(1) 角色定位 + 「只给事实、不给建议」+「找不到就说没有、禁止编造或替换」三条硬规则；(2) 工具清单（schema 自带）；(3) 场次目录（编号 + slugline + 顺序号，注入 user/context 而非 system，按当前项目场次数动态拼，几十场可控）；(4) few-shot：每个策展工具 1 例 + `query_database` 1~2 例，**示范「查一次就回答」**以防 §5.2 的 run-on。
- **预算纪律**：先按极简跑（对齐 u5 反超的教训），某类问题实测不稳才往里加上下文，每加必量化收益，别一上来堆全套细则。

待 spike 的判别问题：让 `query_database` 可用的**最小**上下文是多少、是否还落在 4B 不退化的预算内？若 minimal 上下文都喂不出对的 SQL，就把该类问题**收编成新的策展工具**，而不是继续堆 prompt。

---

## 6. 万能笔安全墙（D-QP-04）

`query_database` 执行模型现写 SQL。✅实测方案（sqlite 3.45 实证）：

新增 `DAL.query_readonly(sql, params=(), *, max_rows=300, timeout_s=3.0)`，**每查询开临时只读连接**，不在共享连接上 toggle（共享连接跨线程、异常会泄漏只读态）：

1. **只读连接**：`sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)`，`row_factory=Row`，只设 `busy_timeout`。用完 `finally` close（照 `migrations/runner.py` / `lifecycle.py` 的临时连接模式）。✅实测 `mode=ro` 拦死 CREATE/INSERT/DELETE/DROP。
2. **authorizer 只放行 SELECT 系 action + scoped PRAGMA**：✅实测 `mode=ro` **拦不住 ATTACH**（query_only 也不行）。必须 `conn.set_authorizer()`，只允许 `SQLITE_SELECT/READ/FUNCTION/RECURSIVE`，其余 `SQLITE_DENY`——挡掉 ATTACH/写/临时表，仍放行 SELECT 和 WITH/CTE。这是真正的安全边界。另：**FUNCTION 独立堵 `load_extension`**（纵深防御，防连接配置变动打开 RCE）；**PRAGMA scoped 只放行 `data_version`**（MATCH 内部需要），其余 PRAGMA（table_info/writable_schema…）DENY。
   - ✅**实现期实证修正（Task 3，commit `7801a1e`，SQLite 3.53.1）**：原计划「authorizer 按表名挡 FTS 影子表、放行 `script_lines_fts` 让 MATCH 工作」**不成立**——FTS5 MATCH **内部**读 `_config`/`_idx` 影子表（`SQLITE_READ`）+ 触发 `PRAGMA data_version`，与「直接读影子表」无法按 action/参数区分，按表名挡会挡死 MATCH。**定案：放行所有表 READ（含影子表）**，靠 action 级（只 SELECT 系）+ mode=ro + 单句 + 封顶 + 超时守边界。影子表是 `script_lines` 派生的索引 blob、漏不出额外信息，挡它原只是「防模型查到 garbage」的可用性护栏，安全无价值且 env-fragile，去除。
3. **单句守卫**：用 `conn.execute()`（单游标）。✅实测多句会 raise `Warning: You can only execute one statement at a time`，免费。绝不用 `executescript`。
4. **行数封顶**：不改写 SQL。`cur.fetchmany(max_rows+1)`，`len>max_rows` 即截断，回 `rows[:max_rows]` + `truncated` 标志塞进结果让模型知道被截。
5. **计算超时**：`conn.set_progress_handler(cb, 1000)`，`cb` 捕获 `deadline=monotonic()+timeout_s` 超时返回非 0 → ✅实测 abort 为 `OperationalError: interrupted`。`finally` 清除。与 `busy_timeout`（锁等待）互补。

**适用范围（D-QP-12）**：上面 2 / 4 / 5（authorizer / 行数封顶 / 超时）是万能笔专属——策展工具 SQL 我们写死、参数化、安全，不需要。但**第 1 条「只读连接」对所有 QP 读都适用**：策展工具（count_takes 等）的 DAL 读方法也必须开临时 `mode=ro` 连接，**不碰共享 `self._conn`**。✅实测 executor 在 `to_thread` worker 跑，复用共享连接是跨线程并发隐患。即新增的全部 QP 读方法（count_takes / get_scene_info / list_characters / search_script_lines / query_readonly）统一走只读连接，可每查询临时开、或每个 loop 复用一条。executor 经 `request.app.state.orchestrator.dal` 拿到 DAL。

默认值 `max_rows=300` / `timeout_s=3.0`（评审已定）。

---

## 7. 场次引用解析（D-QP-05）

✅实测现状：`scenes.scene_code` 是单一自由文本列（唯一索引），唯一查询是精确等值（`get_or_create_scene`）。**没有**顺序号、没有别名表、**没有「标准化场景号格式」这个设置**（`app_settings` 里只有 `audio_input_device`）；用户提到的 `normalize_scene_code` 是 3.x spec 里写了但**未实现**的提案。

设计：

1. **场次目录注入**：每次进 QP，把现有场次清单（`scene_id` + `scene_code` + slugline + 按创建序的位置号）塞进给模型的提示。模型据此把口语引用对到真实编号，不靠 LIKE 瞎猜；也解决万能笔写 SQL 时「场次那栏填啥」。
2. **常见情况（口述≈库里编号）**：**本设计内顺带落地** `normalize_scene_code(raw)->str`（trim/case-fold/剥 `Scene`/`场`/`Sc`/`S` 前缀、保留数字+后缀如 `3A`），写、查两侧都过一遍再精确匹配。顺手修掉现存 `Scene_3`≠`3` 去重 bug。归属（2.x/3.x）待 Lead 定。
3. **找不到 = 老实说没有**（情况 2）：硬规则，禁止模糊替换顶一个错的场。
4. **格式变体**（`Sc_72/Scene72/S72`，同号不同前缀，情况 3）：normalize 能覆盖大部分前缀变体；超出 normalize 的显式别名用 **alias table（v9 迁移，延后）**。
5. **数字不同 = 不同场**：不尝试跨号匹配（用户明确：不同号本就不同步）。

---

## 8. thinking 开关（D-QP-07，延后）

✅实测：

- 这个 GGUF（`models/gemma-4-E4B-it-Q4_K_M.gguf`）的内嵌模板**真支持** `enable_thinking`（grep 出 2 处 Jinja 条件 + thinking 宏 + `<channel|>`）。
- 但 `create_chat_completion` **无 `**kwargs`**，传 `enable_thinking` 会 TypeError。可行路径：装一个 `Jinja2ChatFormatter` 子类（render 时注入 `enable_thinking=True`），经 `Llama(chat_handler=...)` 安装。**不要**走私有 `_chat_handlers`（跨版本会碎）。这要改 `client.py`。
- thinking **只对 auto 跳有用**：forced 跳 JSON grammar 把每个 token 卡死，thinking 无效甚至打架。
- 因此：内核先不带 thinking 跑通（靠场次目录 + few-shot + 出错回喂兜准确率）；thinking 作为**可选准确率增强**后加，且只挂 auto 跳。

---

## 9. 输出契约（D-QP-06）

- v1：QP 返回一段自然语言文本。回包走 **WS `qp.answer.{conn_id}`**（评审已定，对齐架构文档、复用现有广播 seam）。
- **conn_id 管线**：`POST /query` 请求体须带发起方的 `conn_id`，QP 完成后据此把答案推回那条 WS 连接（`qp.answer.{conn_id}`）。计划阶段别漏这条管线（同步返回能绕开它，但既选 WS 就得接上）。
- v2：结构化 `{answer_text, data, refs}`，前端做视觉反馈（数字卡/表格/可点击 take/scene 跳转）。

---

## 10. 文件落点

| 文件 | 动作 | 说明 |
|------|------|------|
| `backend/llm/tools/transcript.py` | 新建 | 5 个 `build_X_tool()` schema（FC spec §3.3 预留的 QP 工具家） |
| `backend/llm/tools/registry.py` | 改 | `_bootstrap()` 注册 QP 工具，传真实 executor |
| `backend/llm/config.py` | 改 | 按 main 现有 eager 写法给 `query_session` 挂 tools + `tool_choice="auto"`（合并 4.x 时重贴到 lazy 结构，见 memory `project_qp_tool_loop`）；新增 `memo_route`（入口层，延后） |
| `backend/llm/service.py` | 小改 | `infer`/`infer_tool` 加按调用覆盖 `tool_choice`/`tools` 的可选参数 |
| `backend/llm/client.py` | 小改（延后） | thinking 的 Jinja 子类（§8，可选项） |
| `backend/pipelines/qp_query.py` | 新建 | `run_tool_loop` + QP 编排 + 场次目录拼装 |
| `backend/db/dal.py` | 改 | 新增只读方法：`count_takes`（软删过滤）、`get_scene_info`、`list_characters`、`search_script_lines`、`query_readonly`；存 `self._db_path`；`normalize_scene_code` |
| `backend/api/routes/`（query 路由） | 新建 | `POST /query`（请求体带 `conn_id`）直连入口（v1 demo）；答案推 WS `qp.answer.{conn_id}` |
| `backend/api/routes/takes.py` | 改（延后） | 文字 memo 分类器插在 `create_note` 内（入口层，等 4.x） |

---

## 11. 阶段划分

- **v1（本设计，main 即建）**：transcript.py 工具 + registry executor + config + qp_query.py 循环 + DAL 只读方法 + query_readonly 安全墙 + `POST /query` 直连入口。独立可 demo。
- **v1.5（4.x 合并后）**：文字 memo 分类器接 `POST /notes`，QP 进共用框。
- **v2**：语音查询、结构化输出、场次 alias table（v9）、thinking 开关。

---

## 12. 测试策略（测试金字塔）

- **L0 工具 schema 单测**：`build_X_tool()` 结构正确、参数全扁平、`name==function.name`。
- **L1 executor / DAL 只读单测**：count_takes 软删过滤、get_scene_info、list_characters 去重、search FTS、`query_readonly` 的安全墙（用 StubDAL 或临时 DB）。
- **安全墙专测**：✅实测覆盖项——mode=ro 拦写、authorizer 拦 ATTACH/PRAGMA、多句 raise、fetchmany 截断、progress_handler 超时。每条断言。
- **L2 循环测**：用 StubClient 喂固定 FunctionGemma 字符串（auto 跳）+ 固定 tool_calls（forced 跳），断言：抠名正确、forced 取参正确、≤5 跳终止、出错回喂、终止条件。
- **L3 真模型 spike（写实现前先跑）**：(A) 仅 render 无权重，断言 `enable_thinking` True/False 渲染 prompt 有差异；(B) 带权重，forced 跳断言 `finish_reason=tool_calls` 且 `json.loads(arguments)` 成、auto 跳断言抠名在嵌套参数下仍对；(C) auto 跳 thinking 对 E4B 选名准确率的实测增益。模型本地已有，可直接跑。

---

## 13. 风险

| 风险 | 级别 | 影响 | 缓解 |
|------|------|------|------|
| 4B auto 在 5 工具上路由不准 | 中 | 选错工具/答非所问 | 工具集卡 ≤5、场次目录注入、(可选)thinking、few-shot |
| 4B 写错 SQL（万能笔） | 中 | 查询失败 | 策展工具兜常见问题、schema 卡片 + few-shot、出错回喂自纠、安全墙不让错查询造成破坏 |
| 每跳两次调用的延迟 | 低 | 用户等待 | 正常 1~2 跳；P1 优先级；秒级可接受 |
| service 覆盖 tool_choice 的改动回归 | 低 | L2/NP forced 路径 | 覆盖参数可选、默认不变；加回归测试 |
| 场次解析对不上 | 中 | 答错场 | normalize + 目录注入 + 找不到说没有（禁替换）；变体走 v9 alias |
| executor 槽首次启用无先例 | 低 | 契约不清 | §5.3 明确签名/同步/错误包裹 |

---

## 14. 开放问题（已评审拍板）

1. ✅ 万能笔默认 `max_rows=300` / `timeout_s=3.0`。
2. ✅ v1 回包走 WS `qp.answer.{conn_id}`。
3. ✅ `normalize_scene_code` 本设计内顺带落地（同时修去重 bug）；归属 2.x/3.x 待 Lead 定。
4. ✅ 万能笔放行基表 + FTS 虚拟表 `script_lines_fts`（可 MATCH）。~~挡掉 FTS 影子表~~——**实现期修正（Task 3，SQLite 3.53.1）**：MATCH 内部读影子表与直接读无法按表名区分，挡影子表会挡死 MATCH；改为放行所有表 READ（含影子表，漏不出额外信息），靠 action 级 authorizer + scoped `PRAGMA data_version` + `load_extension` 独立 DENY 守边界（见 §6.2）。
5. ✅ config.py 照 main 现有 eager 写法加；合并 4.x 时把 QP 配置重贴到 4.x 的 lazy 结构（已记入 memory `project_qp_tool_loop`，防合并时遗忘）。

---

## 15. 变更记录

- v0.1（2026-06-05）：初稿。基于一轮只读核查（工具注册/memo 派发/场次匹配/thinking/只读 SQLite 实测）。确立内核+入口两层拆分、方案 C + 统一两步走循环、只读安全墙、场次解析（normalize + 目录注入 + alias 延后）、thinking 延后、v1 走 `POST /query` 独立 demo。
- v0.2（2026-06-05）：评审回填。落 5 个开放问题决策（300 行 / 3s、WS、normalize 顺带落地、FTS 影子表收口、config.py eager + 合并重贴入 memory）。advisor review 补两节：§5.5 上下文注入与 prompt 预算（D-QP-11，化解方案 C 与 4B prompt 预算的冲突）、D-QP-12 所有 QP 读走只读连接（堵策展工具的跨线程隐患）；补 conn_id 管线（§9/§10）与 run-on 防护（§5.2）。
