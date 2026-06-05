# Spec: Note 输入设计

版本：v0.1
日期：2026-06-12
状态：草稿，待 Lead 评审
owner：境熙

依赖 spec（按权威级别排序）：
1. sqlite-schema v0.3.3（`docs/specs/2026-05-27-sqlite-schema.md`） — `takes.notes` 字段、`take_events` 表结构与 event_type
2. system-architecture v0.1（`docs/specs/2026-05-26-system-architecture.md`） — pipeline 结构（`np_note.py` 占位）、API 层布局
3. realtime-diarization-voicenote-design（`docs/specs/2026-06-02-realtime-diarization-voicenote-design.md`） — ch2 语音备注边界
4. llm-service-design v1.0（`docs/specs/2026-05-25-llm-service-design.md`） — `note_struct` task type 占位

覆盖范围：打字 note 的输入格式、解析规则、数据落点（`take_events` + `takes.notes`）、API 契约（`POST /notes`）、前端 memo 框行为草案、与 ch2 语音备注 / NP / L2 的边界。不涉及 ch2 语音备注的内部实现（gate 在 4.E）。

---

## 1. 背景与目标

### 1.1 现状缺口

录音师在录制过程中需要快速记录文字备注（如「飞机声」「灯光师进场」「这条过了」）。当前系统存在三个缺口：

- `takes.notes`（`TEXT`）字段已存在但无独立写入入口：`end_take()` 是唯一写 notes 的 DAL 方法（`backend/db/dal.py:273-285`），且 notes 只在 take 结束时整体提交，不支持录制中逐条追加。
- 没有 note 相关的 API 端点：架构 spec 提到 `GET /notes` 和 `POST /notes` 但未实现（`system-architecture v0.1 §10`）。
- 前端没有 note 输入框：`takes.notes` 字段在前端 store 中为默认 `null`，admin UI 不渲染（`frontend/src/store/session.ts:147`，`frontend/src/routes/admin/` 下 grep notes 0 hits）。

### 1.2 目标

- 定义打字 note 的文本输入格式与解析规则（rule-based parser，无 LLM 依赖）
- 定义 note 数据落点：以 `take_events` 为主存储（event_type=`manual.note`），以 `takes.notes` 为聚合冗余
- 定义 API 端点 `POST /api/v1/notes` + `GET /api/v1/takes/{take_id}/notes`
- 给出前端 memo 框行为草案（4.D 实现时细化）
- 明确与 ch2 语音备注（4.E/4.F）、NP Pipeline、L2 Pipeline 的边界

### 1.3 不覆盖

- ch2 语音备注的内部采集、LLM 归置、存储形态（gate 在 4.E 决策，4.F 实现）
- note 的编辑 / 删除（MVP 不做）
- LLM 驱动的 note 语义结构化（`note_struct` task type 的 system prompt 细化，后续 P3 ticket）

---

## 2. Note 输入格式

### 2.1 语法

```
[<scene_code> <take_number>] [@<category>] <content>
```

三段均可选，空白 trim。

### 2.2 定位前缀（可选）

格式：`<scene_code> <take_number>`，两个 token 以空格分隔。

- `scene_code`：匹配 `scenes.scene_code` 的值，大小写敏感（与 scenes 表一致）。
- `take_number`：正整数，该场次内的 take 编号。

省略时绑定到**当前活跃 take**（`takes.status IN ('tbd', 'tbd')` 且 `end_ts IS NULL`）。如果当前没有活跃 take 且未指定定位前缀 → 409。

### 2.3 类别标记（可选）

以 `@` 开头、后接小写字母关键词：

| 标记 | 含义 | 是否联动 take.status |
|---|---|---|
| `@keeper` | 标记为保留条 | 可选（见 §9.4） |
| `@ng` | 标记为 NG | 可选（见 §9.4） |
| `@hold` | 标记为待定 | 可选（见 §9.4） |
| `@issue` | 问题记录 | 否 |
| `@note` | 一般备注（默认，可省略） | 否 |

不在上述列表的 `@xxx` → 返回 400，报错信息列出合法值。

### 2.4 内容

除去前缀和类别后的全部文本，trim 后存储。允许空内容（`@keeper` 无正文）。最大长度 2000 字符（约 500 个中文字），超出返回 400。

### 2.5 示例

| 输入 | scene_code | take_number | category | content |
|---|---|---|---|---|
| `飞机声` | null | null | note | 飞机声 |
| `3A 2 飞机声` | 3A | 2 | note | 飞机声 |
| `@issue 灯光问题` | null | null | issue | 灯光问题 |
| `3A 2 @keeper` | 3A | 2 | keeper | （空） |
| `3A 2 @issue 开头有飞机声` | 3A | 2 | issue | 开头有飞机声 |
| `@keeper` | null | null | keeper | （空） |

---

## 3. 解析器

### 3.1 位置

`backend/pipelines/note_parse.py` — 纯函数模块，无 LLM 依赖、不 import DAL。只做文本解析 + 校验，返回 `NoteStruct`，不写库。

### 3.2 NoteStruct 数据类

```python
@dataclass
class NoteStruct:
    raw_text: str           # 原始输入文本
    scene_code: str | None  # 解析出的场次编号
    take_number: int | None # 解析出的 take 编号
    category: str           # 解析出的类别，默认 "note"
    content: str            # 解析出的正文，可为空串
    ts: float               # 输入时间戳（调用方传入）
```

### 3.3 解析逻辑

```
parse_note(raw_text: str, ts: float) -> NoteStruct:

1. 正则提取 scene_code + take_number：
   模式: ^([A-Za-z0-9_-]+) (\d+)\b
   匹配成功 → scene_code, take_number 填充；剩余文本 = raw_text 去掉该前缀
   不匹配 → scene_code=None, take_number=None

2. 正则提取 category：
   在剩余文本开头匹配 ^@([a-z]+)\b
   匹配成功 → category = 匹配值，剩余文本去掉该前缀
   不匹配 → category = "note"

3. content = 剩余文本.strip()

4. 校验：
   - category 必须在 {"keeper", "ng", "hold", "issue", "note"}
   - content 长度 <= 2000
   - 校验失败 → raise NoteParseError
```

### 3.4 NoteParseError

```python
class NoteParseError(Exception):
    """Note 解析错误，携带用户可视化的错误消息。"""
```

---

## 4. 数据落点

### 4.1 take_events（主存储，每 note 一行）

| 列 | 值 |
|---|---|
| `event_type` | `"manual.note"` |
| `ts` | note 提交时间（请求中的 ts 或服务端时间） |
| `payload` | `{"category": "issue", "content": "开头有飞机声", "raw_text": "3A 2 @issue 开头有飞机声"}` |

`event_type` 选 `manual.note` 而非 `system.note`，理由是：`system.note` 是为系统自动生成的事件预留（sqlite-schema v0.3.3 §2.3 注释「系统自动记录」），手动 note 用 `manual.note` 区分来源。

### 4.2 takes.notes（聚合冗余，每 take 一条 TEXT）

每次 note 追加后，DAL 原子更新 `takes.notes` 为「该 take 所有 note 的拼接文本」。格式：

```
[2026-06-12T14:30:01+08:00] @issue 开头有飞机声
[2026-06-12T14:31:05+08:00] @note 灯光调整
```

每行一条 note，前缀 ISO 8601 时间戳（带时区） + 类别 + 正文。行首无 `-` 或编号，纯文本。`takes.notes` 保持为人类可读的汇总文本字段，语义与 schema 注释「Ch2 原文拼接 + 候补 note」一致（sqlite-schema v0.3.3 §2.2）。

### 4.3 不建立独立的 notes 表

当前不建 `notes` 表。理由：

- take 级别的 note 量级小（每 take 几条），不需要独立表的查询优化。
- `take_events` 已提供按 take_id + event_type 的索引，日常查询足够。
- 如需按 take 列出所有 note，`list_take_events(take_id, event_type="manual.note")` 即可。
- 后续若 note 量级变化或需独立检索（全文搜索、跨 take 聚合），再建 `notes` 表 + migration（P3 ticket）。本 spec 对加表持开放态度，见 §9.2。

### 4.4 DAL 新增方法

在 `backend/db/dal.py` 新增两个方法：

```python
def insert_note(
    self,
    take_id: int,
    category: str,
    content: str,
    raw_text: str,
    ts: float,
) -> int:
    """
    写入一条 note 事件（take_events），并原子更新 takes.notes 聚合。
    返回 event_id。
    """
    ...

def list_notes(
    self,
    take_id: int,
    category: str | None = None,
) -> list[TakeEvent]:
    """
    按 take_id 列出 note 事件（event_type='manual.note'）。
    可选按 category 过滤，按 ts 升序返回。
    """
    ...
```

`insert_note` 内部原子性：同一事务中 INSERT `take_events` + 重建 `takes.notes` 聚合文本（从 `take_events` 中 SELECT 该 take 所有 `manual.note` 行、排序拼接后 UPDATE `takes.notes`）。不依赖调用方保证一致性。

---

## 5. API 契约

### 5.1 POST /api/v1/notes

Request body：

```json
{
    "text": "3A 2 @issue 开头有飞机声",
    "ts": 1718123456.789
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `text` | string | 是 | 原始输入文本，最大 2000 字符 |
| `ts` | number | 否 | 输入时间戳（Unix 秒，含小数）。不传则用服务端当前时间 |

不提供 `take_id` 参数 —— take 定位信息从 `text` 中解析。若未指定定位前缀，由 handler 从 SessionState 取当前活跃 take。

Response 201：

```json
{
    "event_id": 42,
    "take_id": 15,
    "scene_code": "3A",
    "take_number": 2,
    "category": "issue",
    "content": "开头有飞机声",
    "raw_text": "3A 2 @issue 开头有飞机声",
    "ts": 1718123456.789
}
```

错误响应：

- **400** `category_unknown`：@ 后的类别不在合法列表中
- **400** `content_too_long`：content 超过 2000 字符
- **400** `parse_error`：文本格式无法解析（如 take_number 不是数字）
- **404** `scene_not_found`：scene_code 不存在
- **404** `take_not_found`：scene_code + take_number 组合不存在
- **409** `no_active_take`：未指定定位前缀且当前无活跃 take
- **401** / **403**：沿用现有 admin 认证（Bearer token）

### 5.2 GET /api/v1/takes/{take_id}/notes

Response 200：

```json
{
    "take_id": 15,
    "notes_aggregated": "[2026-06-12T14:30:01+08:00] @issue 开头有飞机声\n[2026-06-12T14:31:05+08:00] @note 灯光调整",
    "events": [
        {
            "event_id": 42,
            "category": "issue",
            "content": "开头有飞机声",
            "raw_text": "3A 2 @issue 开头有飞机声",
            "ts": 1718123456.789
        },
        {
            "event_id": 43,
            "category": "note",
            "content": "灯光调整",
            "raw_text": "灯光调整",
            "ts": 1718123465.123
        }
    ]
}
```

- `notes_aggregated`：`takes.notes` 字段当前值，人类可读汇总
- `events`：按 ts 升序排列的 note 事件列表

### 5.3 路由注册

在 `backend/api/routes/takes.py` 中新增两个端点，复用 `takes_router`（prefix `/api/v1`）。认证沿用现有 admin check（`auth.require_admin`）。

---

## 6. 前端 memo 框（行为草案）

以下为 4.D 的输入，4.D 实现时可调整细节。

### 6.1 位置与形态

- AdminHome 页面底部区域，`BottomControlBar` 上方
- 一个 textarea，placeholder `"输入备注..."`
- 提交按钮（或回车提交）

### 6.2 交互

- 回车提交 → POST /api/v1/notes → 成功后清空 textarea、在 note 历史区即时追加新 note
- Shift+Enter 换行
- 提交中按钮 disabled + loading 态
- 错误 toast 提示（如「未找到 3A-2」）

### 6.3 note 历史区

- 当前 take 的 note 列表，按时间倒序
- 每条显示：时间（短格式，如 `14:30:01`）、类别标签、正文
- 类别标签用颜色区分：@keeper 绿 / @ng 红 / @issue 黄 / @note 灰
- take 切换时自动切换到新 take 的 note 列表

### 6.4 类别快捷按钮

（建议，非强制）textbox 旁放一排类别按钮（Keeper / NG / Issue / Note），点击后自动在输入框前插入 `@keeper ` 或对应前缀。

---

## 7. 边界

### 7.1 ch2 语音备注（4.E / 4.F，本期不做）

- ch2 语音备注通过 `system.voicenote` event_type 写入 `take_events`，位于 `voice_note` 路由（diarization-voicenote-design §8）。
- 与手动 note 在 `take_events` 中共存，event_type 不同（`manual.note` vs `system.voicenote`），不冲突。
- `takes.notes` 聚合文本在 4.E 决策后扩展格式以容纳语音备注行（如加 `[ch2]` 前缀区分来源）。当前仅手动 note 写入该聚合。

### 7.2 NP Pipeline（不冲突）

NP Pipeline 写入 `takes.performer_issues` / `audio_quality` + `take_events`（event_type=`np.write`）。Note 解析器不碰这些字段，也不消费 NP 输出。

### 7.3 L2 Pipeline（不消费 note）

L2 Pipeline 的 `L2Input.previous_notes` 来自 `orchestrator._assemble_previous_notes()`，读取历史 take 的 `script_diff_summary`（`backend/core/orchestrator.py:245-272`），不是 `takes.notes` 字段。Note 不参与 L2 判断逻辑。若未来需要 L2 消费 note 内容（如「ch1 错词原因 = 环境噪音，来源 @issue note」），单独升级 L2Input 并在对应 spec 中明示。

### 7.4 take.end 行为

- `end_take()` 不再负责写入 `notes`（现有代码 `dal.py:273-285` 中 `notes` 参数保留为可选兼容旧调用，但新 note 仅通过 `insert_note` 写入）。
- 向后兼容：`end_take(notes="...")` 依然可用，但推荐改用 `insert_note` + `end_take(notes=None)`。

---

## 8. 测试入口

### 8.1 Note 解析器单元测试

`backend/tests/test_note_parse.py`

- `test_parse_note_current_take` — 无定位前缀，返回 scene_code=None, take_number=None
- `test_parse_note_with_scene_take` — 带 scene_code + take_number
- `test_parse_note_with_category_issue` — @issue 类别
- `test_parse_note_with_category_keeper` — @keeper 类别
- `test_parse_note_full_format` — 定位 + 类别 + 内容
- `test_parse_note_content_only` — 只有内容，无前缀无类别
- `test_parse_note_category_only` — 只有 @keeper，无内容
- `test_parse_note_category_defaults_to_note` — 无 @ 标记，category 默认为 note
- `test_parse_note_unknown_category_raises` — 未知 @ 类别抛 NoteParseError
- `test_parse_note_content_too_long_raises` — 超长内容抛 NoteParseError
- `test_parse_note_trailing_spaces_trimmed` — 前后空白被 trim
- `test_parse_note_scene_code_with_special_chars` — scene_code 含 `-` / `_` 正常解析

### 8.2 DAL 测试

`backend/tests/test_dal_notes.py`

- `test_insert_note_creates_event` — insert_note 写入 take_events，event_type='manual.note'
- `test_insert_note_updates_takes_notes` — insert_note 更新 takes.notes 聚合
- `test_insert_note_append_aggregation` — 多条 note 后 takes.notes 正确追加
- `test_list_notes_returns_all` — list_notes 返回所有 note
- `test_list_notes_filter_by_category` — category 过滤正确

### 8.3 API 测试

`backend/tests/test_api_notes.py`

- `test_post_note_success_current_take` — 正常创建（当前活跃 take）
- `test_post_note_success_explicit_take` — 通过 scene_code + take_number 指定 take
- `test_post_note_returns_event_id` — 201 响应含 event_id
- `test_post_note_no_active_take_409` — 无活跃 take 且未指定定位前缀
- `test_post_note_scene_not_found_404` — scene_code 不存在
- `test_post_note_take_not_found_404` — scene_code 存在但 take_number 不存在
- `test_post_note_unknown_category_400` — 未知 @ 类别
- `test_post_note_content_too_long_400` — 超长内容
- `test_post_note_unauthorized_401` — 未认证
- `test_get_notes_returns_aggregated_and_events` — GET 返回 notes_aggregated + events
- `test_get_notes_empty_take` — 无 note 的 take 返回空 events + null notes_aggregated

---

## 9. 开放问题

### 9.1 Note 是否允许在 take 结束后追加？

**建议允许。** take.end 后仍可通过显式指定 scene_code + take_number 追加 note（历史追溯场景）。当前活跃 take 仅用于「未指定定位前缀」时的默认绑定，take.end 后该默认绑定自然失效（没有活跃 take，触发 409），需要显式指定目标 take。

### 9.2 是否需要独立的 notes 表？

**本期不建。** 理由见 §4.3。后续若出现以下信号则建表 + migration：
- take 平均 note 数 > 20 条
- 需要跨 take 全文检索 note
- 前端需要按时间线展示所有 note（而非按 take 分组）

### 9.3 是否需要 note 编辑 / 删除？

**MVP 不做。** 理由：note 本质是现场时序记录，编辑破坏审计完整性。后续若有需求可加 event 级软删除标记（`take_events.payload` 加 `{"deleted": true}`）而不物理删除。

### 9.4 @keeper / @ng / @hold 是否联动 take.status？

**本 spec 不做自动联动。** 理由：
- take.status 变更需要明确意图（是否覆盖已有判断），自动联动可能产生意外副作用。
- 建议在 4.D 前端 memo 框中加「同步更新 take 状态」check box，由用户显式选择。

若 Lead 决定需要自动联动，`insert_note` 中加一行 `UPDATE takes SET status=<status> WHERE take_id=?`，实现成本低、不影响契约。

### 9.5 category 是否允许扩展？

**允许但不开放自定义。** 当前预定义 5 个类别，新增需改代码（parser 校验列表 + 前端标签颜色），但无需 migration。后续若需要用户自定义类别，另开 ticket 加配置表或 env 配置。

### 9.6 多行 note 支持？

当前 `content` 字段存完整文本（含换行），Shift+Enter 换行。解析器不截断换行。`takes.notes` 聚合中每行 note 以 `[timestamp]` 开头，与 note 正文中的换行可区分。
