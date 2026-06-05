# SP Pipeline 剧本导入设计 spec（分场 + 按场替换 + 与 2.x 对齐）

状态：草稿，待 Lead 评审。日期：2026-06-03。分支：feat/3.x-script-import。票号：3.A。

---

## 0. 已定决策（2026-06-03 拍板）

本节列三个设计分叉及拍板结果，blast radius 从大到小。正文按此实现，交 Lead 评审。

### 分叉 1（头号，blast radius 最大）—— 多场导入时 scene_code 从哪来

**问题：** 「按场替换」完全靠 `get_or_create_scene` 按 `scene_code` 去重（2.A §3）。`scene_code` 不稳 → 同一场重导时匹配不上既有行 → 建出重复场，replacement 直接失效。中文拍摄台本的 slugline 常是「内 咖啡馆 日」这种无规范场次号的写法，`scene_code` 没有现成稳定来源。

**判别事实（已核对，不是猜）：**
- dev 样例 `DEV_SCRIPT_SAMPLE`（`frontend/src/data/devFixtures.ts:16`）是纯对白格式（`罗湘：…` 全角冒号），**无 slugline、无场次号**。
- dev 种子（`backend/api/entrypoint.py:43`）的 `scene_code` 是人造顺序号 `"Scene_1"`，不是从剧本解析来的。
- 真实拍摄台本可能带 slugline（内/外 地点 日/夜），是否带显式场次号（如 `场 3`、`3A`、`Scene 12`）**不保证**。

**采纳（hybrid，按输入路径分流）：** 把 scene_code 不确定性圈进「无场次号的多场导入」这一个角落，其余路径行为确定。

| 路径 | scene_code 来源 | replacement |
|------|----------------|-------------|
| 单场（拍照 / 粘贴到当前场） | = 当前 active scene 的 code，**不推导** | 成立（同场起新版本） |
| 多场，解析器抽到显式场次号 | 规范化场次号 | 成立（get_or_create 命中既有场 → 新版本） |
| 多场，抽不到场次号 | 分配**新** scene_code，带合成命名空间前缀（如 `import:<批次>:<场序号>`），既保证 UNIQUE 不撞、也不会被未来真实场次号意外匹配 | **不成立，append-only**：每次导入新建场，重导不合并 |

无场次号场的 slugline 三要素（int_ext/time_of_day/location）照常随建场写入 heading，供人肉识别；只是不作为去重键。

**⚠ 充分条件 = scene_code 跨生产者一致（详见 §3.1）：** 「都调 `get_or_create_scene`」只是**必要**条件。它按字符串相等去重，所以只有当 2.x 手动建场（`POST /scenes` 自填 code）与 3.x 解析器抽出的 code **归一化成同一字符串**时，replacement 才真成立。今天 2.A 把 `scene_code` 当自由字符串、无 canonical 格式，故上表「多场 + 有场次号」一行的 replacement 在共享归一化约定落地前是 **best-effort**；**同源重导（同一来源再导一次）始终可靠**。

**已否决备选：**
- **(a) slugline 文本 hash 当 scene_code** —— 地点时间相同的两场必撞键，文案微调一个字即 miss，去重既会误合并又会漏匹配。直接否。
- **(b) 一律 append-only，不支持任何 replace** —— 丢掉「有场次号台本」按场替换的核心价值（3.x 验收要求「按场替换」）。
- **(c) 一律强制 replace（无号也启发式猜合并）** —— 误合并污染历史版本链，无号场无可靠合并依据。

**这是 3.A 的命门。** 3.B 解析器必须输出「抽到的场次号 or null」，3.C 入库按上表分流。

### 分叉 2 —— 建场入口走哪个 DAL 方法 + 与 2.A 的 sequencing

**问题：** 3.C ticket 验收原写「未命中→`create_scene`」。但 `create_scene`（`dal.py:181`）是纯 INSERT 无去重，与 2.x 的 `POST /scenes` 并发或同 `scene_code` 会撞 `ux_scenes_scene_code` 唯一索引（IntegrityError）。

**采纳：** 3.x 所有建场统一走 2.A §3 定义的**唯一入口** `get_or_create_scene`，**不用 `create_scene`**。3.A 不自定义第二套建场逻辑（与 2.A「唯一建场入口」契约一致）。

**sequencing（3.x↔2.x 唯一硬依赖）：** `get_or_create_scene` 是 2.A 新增方法，目前**只存在于 `feat/2.x-scene-org` worktree 未提交的 spec 草稿里，main 上还没有该 DAL 方法**。所以 **3.C 实现前，2.A/2.B 的 `get_or_create_scene` 必须先合进 main**；否则 3.C 只能临时绕 `create_scene`（正是本分叉要避免的）。3.B（纯解析器，不碰 DB）不受此约束，可先行。

### 分叉 3 —— 重导对已有 take 的影响（已知「非效果」，写明免被当 bug）

**问题：** 重导某场起新 script 版本后，该场已有 take 的 `take_line_matches` 怎么处理？scene 的 heading 是否更新？

**采纳：两条都「不动」。**
- **不回算已有 take 的 line_matches。** `get_latest_script` 只取 max 版本，仅影响**之后**的 L2 运行；已有 take 的 `take_line_matches` 指向旧版本的 `line_id`，保持历史不变，不串版本。
- **重导不更新已有 scene 的 heading。** `get_or_create_scene` 命中既有行时按 2.A §3 忽略 `int_ext/time_of_day/location` 等可选参数（不更新已有字段）。要改 heading 另走 `update_scene_heading`。

这两条是 re-import 的**预期非效果**，明示以免 review 时被当 bug。

---

## 1. 范围与非目标

**范围（本期 3.A，产出契约解锁 3.B–3.F）：**
- 解析器 input/output 契约（3.B 据此实现）
- 导入入库 + 按场替换语义（3.C 据此实现）
- 三种输入（拍照/粘贴/上传）路由与 scene_code 分流
- 与 2.x `get_or_create_scene` 对齐 + sequencing
- API 端点形态概要（3.D 定稿）

**非目标（本期不做）：**
- **LLM 分场 prompt 工程**（属 3.B；3.A 只定 I/O schema 与约束，不写 prompt）
- 拍照 vision/OCR 选型与实现（属 3.G spike / 3.H）
- 文件格式提取实现 txt/pdf/docx（属 3.F；3.A 只定它**汇入解析器同一入口**）
- 前端导入 UI（属 3.E）
- schema / migration 改动（**零改**，§2 证明现有表已够）

---

## 2. 现状锚点

以下均为已核对事实，直接作为实现依据。

**schema（`backend/db/schema.sql`，`user_version=2`，已够，零改）：**
- `scripts`：`script_id` / `scene_id`（FK RESTRICT）/ `raw_text` / `version`（DEFAULT 1）/ `UNIQUE(scene_id, version)`（`ux_scripts_scene_version`，schema.sql:92）。**版本机制天生支持「按场替换」。**
- `script_lines`：`line_id` / `script_id`（FK CASCADE）/ `line_no` / `character`（NULL=舞台指示行）/ `text` / `UNIQUE(script_id, line_no)`。FTS5 虚拟表 `script_lines_fts` + 三个同步触发器（insert/delete/update）自动维护检索索引。
- `scenes`：`scene_code` 有唯一索引 `ux_scenes_scene_code`（schema.sql:23）；`int_ext` / `time_of_day` / `location`（slugline 三要素列，v2 已加，schema.sql:18-20）。

**DAL（`backend/db/dal.py`，已有方法）：**
- `insert_script(scene_id, raw_text, version=None)`（:439）：`version=None` 时自动取该场 `MAX(version)+1`。**「按场替换=起新版本」零改 DAL。**
- `get_latest_script(scene_id)`（:463）：`ORDER BY version DESC LIMIT 1`。**L2 自动读最新版。**
- `insert_script_line(script_id, line_no, character, text)`（:474）：FTS5 触发器自动同步。
- `create_scene`（:181，纯 INSERT 无去重，**3.x 不用它**，见分叉 2）。
- **缺失：** `get_or_create_scene`（由 2.A 新增，见 §3）。

**L2 读取链路（`backend/core/orchestrator.py` `_run_l2_async`，:322-326）：**
`get_latest_script(scene_id)` → `list_script_lines(script_id)` → `_truncate_script_lines(…, max_chars=1000)`（:229）→ `L2Input.script_lines`。
即：**只要解析器把 `script_lines` 填上，L2 立刻能真比对**——这正是 3.x 解锁「L2 真比对」的机制，3.x 不改 L2、不改 orchestrator 读取侧。

**`/debug/script` 现状（`backend/api/routes/debug.py:111`）：**
收**已结构化**的 `lines`（`character`/`text`），`insert_script` + 循环 `insert_script_line`，**不解析、不分场**，写当前/指定 scene。3.x 落地后它退化为「结构化注入」的 dev-only debug 工具（跳过解析器），真导入走新端点（§7）。

---

## 3. 跨线对接点（3.x ↔ 2.x）：get_or_create_scene

引用 2.A §3 定义的**唯一建场入口**，3.x 不复制第二套。

```python
def get_or_create_scene(
    scene_code: str,
    *,
    description: str | None = None,
    shoot_date: str | None = None,
    int_ext: str | None = None,
    time_of_day: str | None = None,
    location: str | None = None,
) -> tuple[int, bool]:  # (scene_id, created)
    ...
```

行为（2.A §3 权威）：命中 `scene_code` → `(existing_id, False)`，**忽略其余可选参数**（不更新既有行）；未命中 → INSERT → `(new_id, True)`；并发撞 `ux_scenes_scene_code` → 捕获 IntegrityError 后重新 SELECT 返回既有行。

3.x 的「scene 匹配/建场」**全部调此方法**，不绕 `create_scene`。sequencing 见 §0 分叉 2。

### 3.1 scene_code 归一化契约（跨生产者一致性）

`get_or_create_scene` 按 `scene_code` **字符串相等**去重。两个生产者写入 scene 的 code 必须归一化一致，否则各自建场：

- **2.x 手动建场**：`POST /scenes` 的 `scene_code` 目前是 client 自填自由字符串（dev 种子 `"Scene_1"`），2.A 无格式约束。
- **3.x 解析器建场**：从 slugline 抽场次号并「规范化」（§4）。

失败例：手动建了 `"Scene_3"`，解析器抽出规范化成 `"3"` → `"Scene_3" != "3"` → 建重复场，正是 2.A 要防的。

**采纳（交 Lead + 2.x 对齐）：** 定一条**共享 scene_code 归一化规则**，2.x 手动建场入口与 3.x 解析器**引用同一函数**（建议落 DAL/util 层，如 `normalize_scene_code(raw) -> str`：去前后空白、统一大小写、抽数字 + 字母后缀如 `3A`、剥离 `Scene/场/SC` 等前缀词），两条路径写库前都过它。**该规则落地前，跨「手动建场 ↔ 解析器建场」去重是 best-effort，同源重导可靠**——本限制明示，不留隐患。规则细节与归属（放 2.x 还是 3.x）开工前与 2.x 商定。

---

## 4. 解析器契约（3.B 实现，3.A 钉死 I/O）

**3.x 的脊梁是解析器**：raw text/图-文本 → 结构化 `script_lines`。解析器质量在 L2 关键路径上。

**输入：** `raw_text: str`（粘贴文本 / 文件提取文本 / 拍照 OCR 文本——三条输入提取层不同，**汇入此单一入口**）。

**输出：** `list[ParsedScene]`，每个：

```json
{
  "scene_code": "string | null",
  "slugline": {"int_ext": "string|null", "time_of_day": "string|null", "location": "string|null"},
  "lines": [{"character": "string|null", "text": "string"}]
}
```

**契约约束：**
- `scene_code`：解析器抽到的**显式场次号**（如 `场 3` / `3A` / `Scene 12`），归一化为字符串；抽不到 → `null`（入库侧按分叉 1 分流，解析器不负责造 code）。
- `slugline`：能从场头解析的内外景/时间/地点，缺省 `null`。仅供 heading 展示，**不作去重键**。
- `lines[].character = null` 表**舞台指示行**（对齐 schema `script_lines.character` 可空，及 L2 `_build_script_lines_block` 的「（舞台指示）」渲染，`l2_take.py:135`）。
- **`line_no` 不由解析器 emit**：由入库侧（3.C）按 `lines` 顺序从 1 分配，对齐 `script_lines.UNIQUE(script_id, line_no)`。解析器只保证 `lines` 有序。
- **分场**：按 slugline 切场边界；**单场 / 无明显 slugline → 归为一场**（`scene_code=null`、`slugline` 全 `null`）。
- **slugline 理解整体交 LLM 结构化**（用户定）：场次号抽取 + 内外景/时间/地点三要素**都由 `script_parse` LLM 在输出里给**，不另写规则/正则解析器。实测**约一半剧本有场次号、镜次号不一定有**，故 `scene_code=null` 与 slugline 部分字段缺失是**常态合法值**，不是错误——append-only 路径（§0 分叉 1）是常规分支，不是边缘兜底。
- **容错**：脏数据（空行、页眉页码、非对白噪声）丢弃或归舞台指示行，不抛异常。
- **体量约束**：整本剧本分场输出远超 `script_parse.max_tokens`（现 **8192**，`config.py`；实测 out≈1.29×raw，整本 ~2 万字≈4 万 tok=5×n_ctx）。3.B 必须**分块**解析，不能一把梭整本（实测 `chunk_size=1500` 字符≈窗口 40% 安全，可放宽 ~3000）。**prompt v1 见 §4.1（3.B 已实现并经用户确认）。**

输入文本常见全角冒号格式（`角色：台词`），但解析器吃 raw，**不假设固定分隔符**（拍照 OCR / 不同剧本排版不一定有冒号）。

### 4.1 prompt v1（3.B 已实现，2026-06-04 用户确认为当前任务主 prompt）

> Source of truth 在代码：system = `backend/llm/config.py` 的 `TASK_CONFIG["script_parse"]["system"]`，user = `backend/pipelines/sp_script.py` 的 `_build_user_message`。此处为快照——改 prompt 改代码、回填此节。gen 参数 `temperature=0.1`、`max_tokens=8192`。

**System prompt：**

```
你是剧本解析器。把输入的剧本片段解析为 JSON，直接输出合法 JSON，不要 markdown 代码块。

输出格式：
{"scenes": [{"scene_code": "string或null", "slugline": {"int_ext": "string或null", "time_of_day": "string或null", "location": "string或null"}, "lines": [{"character": "string或null", "text": "string"}]}]}

规则：scene_code 是剧本中明确写出的场次号（如「场3」「3A」），没有就填 null。character 是说话角色名，舞台指示行填 null。

示例输入：
内 咖啡馆 日
罗湘：我们先聊聊。
（罗湘坐下）

示例输出：
{"scenes": [{"scene_code": null, "slugline": {"int_ext": "内", "time_of_day": "日", "location": "咖啡馆"}, "lines": [{"character": "罗湘", "text": "我们先聊聊。"}, {"character": null, "text": "罗湘坐下"}]}]}
```

**User prompt**（`{chunk_text}` 为分块后单块剧本原文，≤1500 字符）：

```
请解析以下剧本片段，输出 JSON。直接输出 JSON，不要 markdown 代码块。

{chunk_text}
```

**设计理由：** 极简版（schema + 1 个 few-shot，不堆细则）——4B Gemma 实测 prompt 越长越乱，堆规则反退步。slugline 理解整体交 LLM（不写正则，见 §4）。

**实测确认（2026-06-04，《双日寒铳》docx/pdf，探针 `scripts/sp_material_probe.py`）：** 性能 OK，用户确认为主 prompt。场次（`scene_code`）/ slugline 三要素 / 对白（`character`+`text`）/ 舞台指示（`character=null`）均正确结构化；slugline **顺序反转**（few-shot 是「内 咖啡馆 日」正序，真实台本「大漠·沙梁 日 外」int_ext 在末尾）仍泛化抽对；括号表演提示（`沙里红（压着嗓子）：`）自动剥离为 `character=沙里红`。

**已知瑕疵（待权衡，不反射改）：** 角色名开头、无冒号的舞台指示（`铁面屠笑了。…`）被误判成对白、挂到该角色名下。修需在 system 加规则，但权衡 4B 极简原则（加细则可能 regress 更多），暂记录不动。

---

## 5. 导入入库 + 按场替换语义（3.C 实现）

逐场处理解析器输出的 `ParsedScene[]`：

**1. 定 scene_id（按 §0 分叉 1 分流）：**
- 单场路径（拍照 / 粘贴到当前场）→ `dal.get_active_scene_id()`；无 active scene → 422（见 §7）。
- 多场 + `scene_code != null` → `get_or_create_scene(scene_code, int_ext=…, time_of_day=…, location=…)`。
- 多场 + `scene_code == null` → 生成新唯一 `scene_code`（**append-only**：推荐「导入批次标识 + 场内序号」，保证 `ux_scenes_scene_code` 不撞、体现每次导入新建语义；具体格式 3.C 定）→ `get_or_create_scene(新code, …)`。

**2. 入库（无重复直接写，有重复走确认）：** 命中已有脚本的场**不静默替换**，走 §5.1 的 preview/confirm。底层写入动作仍是 `insert_script(scene_id, raw_text_of_scene)`（version=None 自增）+ 循环 `insert_script_line(script_id, i, character, text)`，旧版本 script + script_lines **留库不删**（历史）。

**3. 不回算已有 take、不更新 heading**（§0 分叉 3）。

**4. 行清洗（3.C 落实）：** 解析器对脏数据宽容，可能产出 `text` 为空串的 `ParsedLine`（§4 容错 / `_parse_chunk_output` 的 `get("text","")`）。3.C 入库前 **filter 掉 `text.strip()` 为空的行**，不写空 `script_lines` 行（对齐 §4「丢脏数据」，与 `/debug/script` 的空行过滤一致，debug.py:134）。`character` 为空串的也归一为 `None`（舞台指示）。

**5. 跳过清洗后全空的场（防版本链覆盖）：** 某场清洗后无任何有效行 → **整场跳过**：不 `insert_script`、多场路径也**不 `get_or_create_scene`**（不建空场）。理由：空 re-import 若写一个空版本，`get_latest_script` 会返回该空版本 → L2 丢掉该场 diff 基准。故**清洗判空必须在定 scene_id 之前**。副作用：纯 slugline 无对白的场也被跳过、不建场（MVP 可接受）。全批跳光 → 返回空 → 端点 422。

**raw_text 入库口径：** 每场 `raw_text` = 该场**清洗后** `lines` 重新拼接（全角冒号 `角色：text`，舞台指示行直出 text），与 `/debug/script`（debug.py:139）一致，保证 `scripts.raw_text` 可读且格式统一。

---

## 5.1 重复场确认替换流程（preview / confirm，3.C/3.D/3.E）

> **决策（2026-06-03 用户拍板）：** 重复场替换走「确认 + diff 对比」，不静默覆盖；无重复直接入库。blast radius：3.C 拆 preview/confirm 两阶段、3.D 两阶段端点、3.E 左右对比弹窗。这是对原 §5「直接插新版本」的 reframe。

「按场替换」**不静默发生**：命中已有脚本的场要让场记看 diff 确认是否替换，避免误导入覆盖好脚本的版本链（与步骤 5 跳过空场同一价值——不让可疑导入污染版本链）。

**定义：**
- **重复场** = 该场 `get_or_create_scene` 命中已有 `scene_code`（`created=False`）**且** `get_latest_script(scene_id)` 非空（已有脚本版本）。
- **无重复场** = 新建场（`created=True`）或命中但该场尚无脚本（首次导入）。

**阶段 1 — preview（解析 + 只读分类，解析只发生这一次，零写）：**
1. 解析 `raw_text → ParsedScene[]`（§4），行清洗 + 跳过全空场（步骤 4/5）。
2. **只读分类（关键：preview 绝不写库、绝不建场）**：用 `list_scenes()` 建 `{scene_code: scene_id}` 映射 + `get_latest_script(scene_id)` 判有无脚本，逐场判定：
   - 单场（current_scene）→ scene_id=`get_active_scene_id()`；该场有脚本 → 重复，否则无重复。
   - 多场有号 → scene_code 在映射 → 命中场，有脚本则**重复**、无脚本则无重复（首次导入）；不在映射 → **新场**（无重复）。
   - 多场无号 → 合成 code（每次唯一）→ 必不在映射 → 新场（无重复，append-only）。
   ⚠ **`get_or_create_scene` 不在 preview 调**——它有 INSERT 副作用，会给每个新场建孤儿空场、破坏「零写」承诺。preview 只用上述只读方法（全在 main），**零 2.x 依赖，可今天用真 DAL 集成测**。
3. 分支：
   - **整批无重复** → 直接全部入库（走阶段 2 的写逻辑、无 decisions），返回 `{status:"imported", scenes:[…]}`。一步完成、无弹窗。
   - **存在重复场** → **不写任何东西**，返回 `{status:"needs_confirmation", plan:<结构化导入计划>, conflicts:[{scene_id, scene_code, original:{raw_text,lines}, incoming:{raw_text,lines}}]}`。

**阶段 2 — confirm（写阶段，唯一调 `get_or_create_scene` 的地方）：**
- 输入：`plan`（阶段 1 解析结果）+ `decisions`（每个重复场 `replace | skip`）。
- 写：对每个要写的场调 `get_or_create_scene`（真建场/复用，**2.x 方法，整个 3.C 只在这里依赖 2.x**）→ `insert_script` 新版本 → `insert_script_line`。无重复场全写；重复场按 `decisions`（`replace` 写、`skip` 不写）。「整批无重复」分支也复用本写逻辑（视作全 write）。
- 注：preview 的 scene_id 是只读 snapshot；confirm 真建场以 `get_or_create_scene` 为准（snapshot 与写之间若有并发建场，靠 `get_or_create_scene` 的 IntegrityError 兜底，单用户低风险）。

**解析只发生一次（硬约束）：** LLM 解析不确定，preview 与 confirm **不能各解析一遍**（结果会漂移、对比失真）。preview 的 `ParsedScene[]` 必须带到 confirm。MVP：结构化 `plan` 随 preview 返回、confirm 回传（无状态，剧本结构化数据不大）；备选：后端 `plan_token` 暂存（免回传，需临时存储 + TTL）。3.D 定。

**对比数据粒度：** 后端给 `original` 与 `incoming` 两边的 `raw_text` + 结构化 `lines`（character/text 列表）；逐行 diff 渲染由前端（3.E）做。

**current_scene 多场（统一 §0 分叉 1 单场路径的边缘）：** `target=current_scene` 时把解析出的多场**合并成一个新版本**（所有场的 lines 顺序拼成一个 script）对当前 active scene。当前场已有脚本 → 走重复确认（original=当前场最新版 vs incoming=合并内容）；无脚本 → 直接写。不丢场、不产生 N 个版本、替换前必确认。

**⚠ dup 检测是 best-effort（基于 `scene_code` 身份匹配）**，两类「人眼觉得是重复」的场**不会触发确认弹窗**——记此以校准信任：
- **误归一化（§3.1）**：手动建场 `"Scene_3"` vs 解析器抽 `"3"`，字符串不等 → 分类「新场无重复」→ 直接写、无弹窗、建出平行场。`normalize_scene_code`（2.x/3.x 共享）落地前，这个洞恰在「人眼会认为是重复」的 re-import 上。
- **无号场 append-only**：合成 `import:{batch_id}:{n}` 每次唯一 → 永不命中 → 重导同一无号多场剧本总是 append、不提示。

即心智模型「重导就弹窗」实际是「`scene_code` 命中才弹窗」。

---

## 6. 三种输入路由

| 输入 | 场数 | scene_code 来源 | replacement | 提取层 |
|------|------|----------------|-------------|--------|
| 拍照（3.G/3.H） | 当前场 | active scene | 成立 | vision/OCR（3.G 选型） |
| 粘贴 | 1..N | 单场=active；多场=场次号 or append | 单场/有号成立 | 直接是文本 |
| 上传整本（3.F） | 全量分场 | 同多场粘贴 | 有号成立 | txt/pdf/docx 提取 |

三条输入**提取层不同，汇入解析器同一入口**（`raw_text → ParsedScene[]`，§4）。拍照 = spike（3.G 定 vision vs OCR）尚未落地，3.H gate 在 3.G 结论，本 spec 不展开实现，只钉「拍照=当前场、不走分场」。

「粘贴单场 vs 多场」如何判定目标（导入到当前场，还是按解析结果多场建场）由**端点参数显式声明**（§7），不靠解析器猜。`target=current_scene` 即使解析出多场也**合并成当前场的一个新版本**（§5.1），不丢场也不误建多场；替换当前场已有脚本时走确认。

---

## 7. API 端点形态（3.D 概要，3.D 定稿）

两阶段 import（preview / confirm，见 §5.1）：
- **`POST /api/v1/scripts/import`**（阶段 1 preview，粘贴主入口）：body `{raw_text, target: "current_scene" | "multi_scene"}` → 解析 + 分类。**无重复** → 直接入库返回 `{status:"imported", scenes:[…]}`；**有重复** → 不写，返回 `{status:"needs_confirmation", plan, conflicts:[{scene_id, scene_code, original, incoming}]}`。
- **`POST /api/v1/scripts/import/confirm`**（阶段 2，仅 needs_confirmation 后）：body `{plan, decisions:[{scene_id, action:"replace"|"skip"}]}` → 按决策写（§5.1 阶段 2）。
- **`POST /api/v1/scripts/upload`**（3.F）：收文件 txt/pdf/docx → 提取纯文本 → 复用 import 同一两阶段流程。
- **`/debug/script`**：**保留**作结构化注入 debug 工具（dev-only，跳过解析器，直收结构化 lines），不被 import 端点取代。

**错误口径（3.D 定细节）：** 单场路径无 active scene → 422；解析器零场 / 全空（含全部场清洗后空）→ 422。具体请求/响应 schema 与错误码由 3.D 定稿。

---

## 8. 约束（记死，不是分叉）

- **解析质量在 L2 关键路径上**：行拆错 → diff 错 → `take_line_matches` 错。3.B fixture 必须覆盖**单场 / 多场 / 中文全角冒号 / 脏数据**，且 GT 标注走**独立 fresh-context**（对齐评测 judge 独立性纪律：生成与标注两次独立调用，不让同一上下文自评）。
- **单场过长截断**：L2 读 `script_lines` 截 1000 字（`orchestrator._truncate_script_lines`，:229）。分场后 L2 只读本场行，天然有界，缓解截断；但单场仍可能超 1000 字，后续按需调上限或分块（不在 3.x 范围内强解，标注即可）。

---

## 9. 验收映射（对回 3.A 验收标准）

| 3.A 验收标准 | 本 spec 对应章节 |
|--------------|-----------------|
| 解析器契约 raw text/图-文本 → `[{scene_code/slugline, lines}]`，1..N 场 | §4 |
| 按场 upsert + 版本替换语义写清 | §5 + §2（DAL/schema 现状） |
| 重复场确认替换 + diff 对比（2026-06-03 reframe，超出原 3.A 验收）| §5.1 |
| scene 匹配/建场与 2.x 对齐方案 | §3 + §0 分叉 2 |
| 拍照路径指向 3.G spike | §6 |
| 拍照=当前场、粘贴/上传=多场 的路由写清 | §6 |
| Lead 评审通过 | §0 待评审 |

---

## 10. 对接点 / 下游解锁

- **3.B**（解析器）：据 §4 契约实现，依赖 1.F（LLMService）；纯解析不碰 DB，**不受 2.A sequencing 约束**，可先行。
- **3.C**（入库/按场替换）：据 §5 实现。**只有多场分支硬依赖 2.A `get_or_create_scene` 先合 main**（§0 分叉 2）；**单场路径**（§5 步骤 1，拍照/粘贴到当前场）只用 `get_active_scene_id` + `insert_script` + `insert_script_line`，全在 main，**可与 2.A 并行**。开工前知会 2.x（与 `POST /scenes` 建场路径、§3.1 归一化对齐）。
- **3.D**（API 端点）：据 §7 定稿请求/响应 schema 与错误码。
- **3.E**（前端导入 UI）：消费 §7 端点。
- **3.F**（文件上传）：据 §6 汇入解析器同一入口。
- **3.G/3.H**（拍照）：据 §6 路由，spike 先行定选型。

---

## 11. 与 4.x 多模态 LLM 入口架构对齐（2026-06-05 补）

> **来源：** 4.x `docs/specs/2026-06-05-voice-note-and-np-refinement.md` §5.1 / §5.2（v0.4 架构定调 + v0.5 方案 A）。该 spec 的多模态架构（单实例三入口）是 3.x 与 4.x 共用的地基；本节把它**吸收进 3.x spec**，让 feat/3.x 推给开发者后可照此独立实现、不必另翻 4.x spec。按 **2026-06-05 的 4.x 设计当前形态**对齐（4.x 仍在打磨），定稿后回校一次。
> **为什么进 3.x spec：** 3.x 有两条 LLM 通路——**3.B 文本解析**、**3.G/3.H 拍照 vision**——它们不是孤立的，而是 4.x 定的「单实例多模态 handler」三入口里的两个。3.B/3.G/3.H 实现必须遵循此架构，**不许各自建模型实例**。

### 11.1 架构（4.x 定调，硬约束）

全后端**只有一份** `Llama`，**不开第二个**。这份实例挂**一个多模态 handler**（`chat_handler=<多模态 handler>` + `mmproj-F16.gguf`，含 gemma4v 视觉 + gemma4a 音频两个投影器），按 content 类型分**三个入口**：

| 入口 | 3.x 这边谁用 | 其他用户 | 投影器 |
|------|-------------|---------|--------|
| 文本 | **SP 剧本解析（3.B）** | L2 / 文本 NP | 无 |
| 图像 | **拍照 OCR（3.G/3.H）** | —— | gemma4v |
| 音频 | —— | 语音 NP（4.x） | gemma4a |

**SP（剧本导入）是唯一横跨两个入口的业务线**：粘贴 / 上传的剧本文本走**文本入口**（3.B 解析），拍照剧本走**图像入口**（3.G/3.H OCR）——同一条业务、两条 LLM 通路，都挂这一份共享实例。这是 3.x 内部的入口分配，**记在本 spec 即可、不进 4.x**（4.x 只定义架构，不管各业务怎么用入口）。

三入口**共用同一 `CHAT_FORMAT`**（gemma 模板）。L2 / 文本 NP / SP 共用一份模型已是现状（都调 `LLMService.infer`，`_lock` + priority 串行调度）；多模态化后仅多 mmproj 增量，**显存不翻倍**。管线仍平行（各 async 编排互不等待），进模型经 LLMService 序列调度串行喂同一实例。

### 11.2 3.B = 文本入口（对 §4.1「prompt v1 钉死」的限定）

3.B 现状已走统一文本入口（`LLMService.infer`，§4 / §10）。收敛到多模态 handler 后，文本格式化从 **GGUF 默认 chat_template 换成 handler 的 `CHAT_FORMAT`**——典型差异：gemma 无 system role，system 折进 user turn 的方式两套不一致（handler 拼成**两个连续 user turn**，默认模板**合并成一个**），**prompt 字符串会变**。

**限定（重要）：** §4.1 说 v1「性能 OK / 用户确认为主 prompt」，那是在**默认 chat_template 下**实测的。切到统一 `CHAT_FORMAT` 后分场 / 行结构解析可能漂。**SP 须对标 4.x 给 L2 定的 parity 口径**（4.x spec §10-2 / §11）：收敛后跑一遍 `sp_smoke`、确认分场 + 行结构仍合法，漂了重调 prompt / 重盖 smoke 基准；**不追求与旧路径逐字一致**，仅当解析质量真退化才算不过。**此 SP 文本 parity 是 3.x 自己的收敛验收项**（§11.5），留在本 spec、**不进 4.x**——4.x 只定义架构（三入口），SP 用哪个入口、各自验收归 3.x。

### 11.3 3.G/3.H = 图像入口（方案 A：4.J 建实例，3.H 只接调用）

4.x v0.5 **方案 A**：4.J 直接按最终标准化形态建那份 **vision-ready 共享单实例**，构造参数已为 OCR 就位：

```
Llama(model_path=GGUF, chat_handler=<多模态 handler>,
      n_ctx=8192, n_batch=2048, n_ubatch=2048, n_gpu_layers=-1, seed=42)
handler.clip_model_path = <mmproj-F16>
_init_mtmd_context: image_min_tokens = image_max_tokens = 1120
```

`n_batch=n_ubatch=2048` + `image_max_tokens=1120` 是 **gemma4 vision OCR 的硬要求**（3.x `scripts/sp_vision_probe.py` 实测：gemma4 vision non-causal，1120 image token 要落单 ubatch；中文小字读清靠 1120 档）。4.J **现在就位**这些参数，目的就是让 3.x vision 合并时**只接调用、不动实例构造、零冲突**。

**3.H 落地口径：** 不自建模型实例，把拍照 vision 调用接进 4.J 建好的多模态实例（共用 `CHAT_FORMAT`、单 mtmd ctx 同挂 vision + audio）。`sp_vision_probe.py` 已实测打通 vision 通路（OCR 转录可用、cast 注入定位专名错根因），是 3.H 的参考原型，**本期仍 spike、不产品化**。

### 11.4 版本 + 依赖统一

- **rebase 红利**：3.x rebase 到 main（`d2cf92f`，含 #16 跨平台依赖迁移）后，`llama-cpp-python` 已是 **`==0.3.25`**，与 4.x 同版本。4.x spec §5.1 写的「3.x 0.3.23 手搓 vs 4.x 0.3.25 内置」版本差**已消失**——两边都用 0.3.25 **内置 `Gemma4ChatHandler`**。
- `sp_vision_probe.py:49` 现仍 `class Gemma4ChatHandler(Llava15ChatHandler)`（手搓继承 + override vicuna→gemma 模板）是 0.3.23 遗留，收敛时**弃手搓、改继承内置**。
- mmproj 路径解析 4.x 的 `LLMService` 已落 `resolve_mmproj_path`（env `GEMMA_MMPROJ_PATH` > HF cache `unsloth/gemma-4-E4B-it-GGUF` 的 `mmproj-F16.gguf` > 下载），3.x vision 复用、不另写。

### 11.5 3.x 这边的对齐待办

- **3.B**：收敛后补 SP 文本 parity 重验（对标 L2 口径，§11.2）。3.x 自管验收，**不回传 4.x**。
- **3.H**：vision 接进 4.J 共享实例（§11.3），不自建实例；`sp_vision_probe.py` 的 0.3.23 注释更新为 0.3.25 现状。
- **跨线 sequencing**：4.J 建实例（owner 4.x / 经纬）→ 3.H 接调用（owner 3.x），4.J 先落、3.H 后接，开工前与 4.x 对齐（类比 §0 分叉 2 的 2.A→3.C sequencing）。
