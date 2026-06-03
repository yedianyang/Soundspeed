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
- **体量约束**：整本剧本分场输出远超 `script_parse.max_tokens=2048`（`config.py:54`）。3.B 必须采用**分块或按场循环**解析，不能一把梭整本。3.A 不定 prompt，但钉死「解析必须分场循环、单次调用输出可控」这条约束。

输入文本常见全角冒号格式（`角色：台词`），但解析器吃 raw，**不假设固定分隔符**（拍照 OCR / 不同剧本排版不一定有冒号）。

---

## 5. 导入入库 + 按场替换语义（3.C 实现）

逐场处理解析器输出的 `ParsedScene[]`：

**1. 定 scene_id（按 §0 分叉 1 分流）：**
- 单场路径（拍照 / 粘贴到当前场）→ `dal.get_active_scene_id()`；无 active scene → 422（见 §7）。
- 多场 + `scene_code != null` → `get_or_create_scene(scene_code, int_ext=…, time_of_day=…, location=…)`。
- 多场 + `scene_code == null` → 生成新唯一 `scene_code`（**append-only**：推荐「导入批次标识 + 场内序号」，保证 `ux_scenes_scene_code` 不撞、体现每次导入新建语义；具体格式 3.C 定）→ `get_or_create_scene(新code, …)`。

**2. 按场替换（起新版本）：**
```
script_id = insert_script(scene_id, raw_text_of_scene)   # version=None → 自动 MAX+1
for i, line in enumerate(scene.lines, start=1):
    insert_script_line(script_id, i, line.character, line.text)
```
旧版本 script + script_lines **留库不删**（历史）。

**3. 不回算已有 take、不更新 heading**（§0 分叉 3）。

**4. 行清洗（3.C 落实）：** 解析器对脏数据宽容，可能产出 `text` 为空串的 `ParsedLine`（§4 容错 / `_parse_chunk_output` 的 `get("text","")`）。3.C 入库前 **filter 掉 `text.strip()` 为空的行**，不写空 `script_lines` 行（对齐 §4「丢脏数据」，与 `/debug/script` 的空行过滤一致，debug.py:134）。`character` 为空串的也归一为 `None`（舞台指示）。

**raw_text 入库口径：** 每场 `raw_text` = 该场 `lines` 重新拼接（全角冒号 `角色：台词`，舞台指示行直出 text），与 `/debug/script`（debug.py:139）一致，保证 `scripts.raw_text` 可读且格式统一。

---

## 6. 三种输入路由

| 输入 | 场数 | scene_code 来源 | replacement | 提取层 |
|------|------|----------------|-------------|--------|
| 拍照（3.G/3.H） | 当前场 | active scene | 成立 | vision/OCR（3.G 选型） |
| 粘贴 | 1..N | 单场=active；多场=场次号 or append | 单场/有号成立 | 直接是文本 |
| 上传整本（3.F） | 全量分场 | 同多场粘贴 | 有号成立 | txt/pdf/docx 提取 |

三条输入**提取层不同，汇入解析器同一入口**（`raw_text → ParsedScene[]`，§4）。拍照 = spike（3.G 定 vision vs OCR）尚未落地，3.H gate 在 3.G 结论，本 spec 不展开实现，只钉「拍照=当前场、不走分场」。

「粘贴单场 vs 多场」如何判定目标（导入到当前场，还是按解析结果多场建场）由**端点参数显式声明**（§7），不靠解析器猜，避免单场剧本被误判成「替换当前场」或反之。

---

## 7. API 端点形态（3.D 概要，3.D 定稿）

- **`POST /api/v1/scripts/import`**（粘贴主入口）：body 含 `raw_text` + 目标声明（如 `target: "current_scene" | "multi_scene"`）→ 解析器 → §5 入库。`target=current_scene` 走单场路径（active scene），`multi_scene` 走分场 + scene_code 分流。
- **`POST /api/v1/scripts/upload`**（3.F）：收文件 txt/pdf/docx → 提取纯文本 → 复用 import 同一流程。
- **`/debug/script`**：**保留**作结构化注入 debug 工具（dev-only，跳过解析器，直收结构化 lines），不被 import 端点取代。

**错误口径（3.D 定细节）：** 单场路径无 active scene → 422；解析器零场 / 全空 → 422。具体请求/响应 schema 与错误码由 3.D 定稿。

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
