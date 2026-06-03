# 场次与 Take 管理操作层 API 设计

状态：草稿，待 Lead 评审。日期：2026-06-03。分支：feat/2.x-scene-org。票号：2.A。

---

## 0. 已定决策（2026-06-03 Lead 拍板）

本节列出三个设计分叉及其拍板结果，blast radius 从大到小排列。所有推荐方案均已采纳，正文按此实现。

### 分叉 1（头号，blast radius 最大）—— take.start 与 active scene 的关系

**问题：** 本 spec 把「当前场次」真相源定为 `scenes.is_active`。take.start 的请求体携带 `scene_id`，两者如何协调？

**采纳（b）：** take.start 保留 body 的 `scene_id`，但改为**校验 `scene_id == get_active_scene_id()`**，不一致返回 409。不再隐式激活。改动最小，前端请求体契约不变，消掉双写。

**已否决备选（a）：** take.start 直接读 DAL 的 active scene，body 的 `scene_id` 退化为建议或忽略。单一数据源，无需 must-match 校验。

**Bootstrap 行为（必须理解，否则容易误读成契约破坏）：** dev 种子（`backend/api/entrypoint.py:44`）在空库启动时调用 `create_scene("Scene_1") + set_active_scene`，所以 `take.start {scene_id:1}` 命中 active，不会触发 409，现有 dev 流程照常。新库或多场景下：场建好但未激活，take.start 会 409，直到调 `/activate`。这使**「先激活、再开拍」成为前端硬要求**（操作员选场→激活→roll），是预期 UX，不是副作用。2.x 接线票必须实现「激活」操作，否则前端在新建场首次开拍会静默失败。

### 分叉 2 —— 事后改 take_number / scene 的 UNIQUE 冲突算法

**问题：** `UNIQUE(scene_id, take_number)` 约束下，改 `scene_id`（移场）或改 `take_number` 都可能撞号，怎么处理？

**采纳：**
- 改 `scene_id`（移场）：默认追加为目标场的下一个 `take_number`（`COALESCE(MAX(take_number),0)+1`），避免撞号。若同时显式指定 `take_number` 且已占用，按下条处理。
- 同场内改 `take_number` 撞已占用号：**两条 take 交换编号**，在一个事务里完成。对应真实场景：「我把第 2、3 条标反了」。
- `shift` / 顺移后续编号：v1 不做，YAGNI。

**已否决备选：** 撞号直接 409，让前端手动解决；或 shift 顺移后续编号。

### 分叉 3 —— POST /scenes 建场是否自动激活

**采纳：不自动激活。** 建场和激活是两个动作，单一职责。激活走独立 `POST /scenes/{scene_id}/activate` 端点。

**已否决备选：** 建场即激活（少一次调用，但把两个语义糅合）。

---

## 1. 范围与非目标

**范围（本期 2.A）：**

场次组织层的数据模型已建好（scenes、takes、take_events、audit_log），前端 admin mock 把控件全做了但全是本地 state、没接后端。本期补：

- 操作层 API 端点（建场、激活场、事后编辑 take、删除 take）
- active scene 真相源 reconcile + take.start 校验语义
- 事后编辑冲突算法（移场/换号）
- 同场对比只读视图字段契约（2.E 实现前的字段定义）
- 新增 DAL 方法签名

**非目标（本期不做）：**

- 数据模型设计和 migration 改动（零改 migration）
- LLM 对比摘要（属 2.F）
- 前端实现（属 2.x 其他票）
- 软删除（v4，排在 diarization spec 的 v3 speakers 表之后）

---

## 2. 现状锚点

以下均为已核对事实，直接作为实现依据。

**schema（backend/db/schema.sql）：**

- `scenes.scene_code` 有唯一索引 `ux_scenes_scene_code`（schema.sql:23），是独立 `CREATE UNIQUE INDEX`，非内联约束。
- `takes` 有 `UNIQUE(scene_id, take_number)`（schema.sql:47）；`status` CHECK 限 `keeper/ng/hold/tbd`；`takes.scene_id REFERENCES scenes ON DELETE RESTRICT`（schema.sql:34）。
- 子表 `take_events`、`take_line_matches`、`transcript_segments` 对 `take_id` 全是 `ON DELETE CASCADE`（schema.sql:64/119/140）。
- `PRAGMA foreign_keys = ON` 在 `backend/db/lifecycle.py:18` 统一设置，CASCADE 和 RESTRICT 均生效，删除算法依赖这一点。
- `audit_log` 表已存在（schema.sql），字段：actor / action / payload（JSON）/ ts。

**DAL（backend/db/dal.py）已有方法：**

- `create_scene`（:181，纯 INSERT，无去重）
- `set_active_scene`（:195）
- `get_active_scene_id`（:203）
- `list_scenes`（:210）
- `update_scene_heading`（:219）
- `start_take`（:251）
- `end_take`（:267）
- `get_take`（:312）
- `list_takes`（:319）
- `insert_take_event`（:334）

**DAL 缺失（本期新增，见 §10）：** `get_or_create_scene`、`update_take_meta`、`delete_take`。切场复用已有 `set_active_scene`。

**路由（backend/api/routes/takes.py）已有端点：**

- `POST /take/start {scene_id, shot}`
- `POST /take/end`
- `GET /takes`
- `GET /takes/{id}`
- `PATCH /takes/{id}/segments/{segment_id}`（说话人纠正，已存在，新端点命名不得与此撞）
- `GET /scenes`
- `GET /scenes/{id}/script`

所有端点 `async def` + `Depends(require_admin)`。

**orchestrator（backend/core/orchestrator.py）：**

- `_on_take_start`（:147）调 `session.activate_scene(scene_id)`，只更新内存 `session.scene_id`，**不写 `scenes.is_active`**。
- take_number 自动取 `len(list_takes(scene))+1`（:156）。

**当前问题：** 生产运行时没有任何路径切换 `scenes.is_active`，只有 dev 种子在空库启动时执行一次。`session.scene_id`（来自 take.start payload）和 `scenes.is_active` 当前可以发散。本 spec 的 §4 负责 reconcile。

---

## 3. get-or-create 契约（跨线 2.x↔3.x seam）

### 背景

`POST /scenes` 和 3.x 剧本导入自动建场都需要「按 scene_code 查找或创建」语义。两条路径不得各写各的 INSERT，统一走一个 DAL 方法。

### DAL 方法签名

```python
def get_or_create_scene(
    self,
    scene_code: str,
    *,
    description: str | None = None,
    shoot_date: str | None = None,
    int_ext: str | None = None,
    time_of_day: str | None = None,
    location: str | None = None,
) -> tuple[int, bool]:
    """返回 (scene_id, created)。created=True 表示本次新建，False 表示复用已有行。"""
```

### 行为规范

1. `SELECT scene_id FROM scenes WHERE scene_code = ?`，命中→返回 `(existing_id, False)`，忽略其余可选参数（不更新已有行的字段）。命中时不更新 description / slugline 等字段；要改 heading 走已有 `update_scene_heading`。
2. 未命中→执行 INSERT，返回 `(new_id, True)`。
3. 并发场景下 INSERT 撞唯一索引 `ux_scenes_scene_code`（IntegrityError）→捕获后重新 SELECT 返回既有行。单用户低风险，防御性兜底。

这是**唯一**建场入口。`POST /scenes` 和剧本导入自动建场必须调此方法，不得绕行直接调 `create_scene`（纯 INSERT，无去重）。

---

## 4. active scene 真相源 + 切场端点

### 真相源

`scenes.is_active` 为权威真相源。`session.scene_id` 仅为内存缓存，切场时必须同步刷新，不得仅更新其中一侧。

### 新增端点 POST /scenes/{scene_id}/activate

```
POST /scenes/{scene_id}/activate
权限：require_admin
必须 async def（同 takes.py 单连接线程安全约束）
```

执行步骤（全同步，非事务隔离，写 audit 最后）：

1. 检查是否有 take 正在录制：查 `session.take_active`（内存活体标志）。为 True 则返回 409（录制中禁止切场）。此处用 `session.take_active` 而非查 `end_ts IS NULL`——activate 判的是「当前是否有 take 在录」，是全局状态，不是针对某一行。
2. scene 不存在→404。
3. 调 `dal.set_active_scene(scene_id)`，写 `scenes.is_active`。
4. 刷新 `session.scene_id = scene_id`（内存缓存）。
5. 写 `audit_log`：action=`scene.activate`，payload 含 scene_id、actor。

成功返回 `{"scene_id": ..., "scene_code": ...}`。

### take.start reconcile（分叉 1 采纳方案 b 的主线实现）

修改 `POST /take/start` 行为：

- 保留请求体的 `scene_id` 字段（前端契约不变）。
- **校验必须在路由函数里、`orchestrator.publish(TAKE_START)` 之前同步执行**：`scene_id == dal.get_active_scene_id()`，不一致直接 `raise HTTPException(409, {"error": "scene_not_active", "active_scene_id": ...})`。不能把校验放进 orchestrator 事件 handler（handler 在 fire-and-forget 路径里，无法返回 HTTP 状态码）。
- 不再隐式调用 `set_active_scene`，不再隐式激活。

orchestrator `_on_take_start`（:147）的 `session.activate_scene(scene_id)` 可以保留作内存缓存刷新，但不再作为「激活」的唯一路径——这条路径不写 DB，允许保留，但含义退化为「确认内存与 DB 一致」。

**Bootstrap 行为：** dev 种子已激活 Scene_1，`take.start {scene_id:1}` 命中 active，不触发 409，dev 流程照常。新库下须先调 `/activate`。

---

## 5. 建场端点 POST /scenes

```
POST /scenes
权限：require_admin
必须 async def（同 takes.py 单连接线程安全约束）
```

请求体：

```json
{
  "scene_code": "string（必填）",
  "description": "string（选填）",
  "shoot_date": "string（选填，格式 YYYY-MM-DD）",
  "int_ext": "string（选填）",
  "time_of_day": "string（选填）",
  "location": "string（选填）"
}
```

执行：调 `dal.get_or_create_scene(scene_code, **kwargs)`。

成功返回（HTTP 200，无论新建还是复用）：

```json
{
  "scene_id": 1,
  "scene_code": "Scene_1",
  "created": true,
  "is_active": false
}
```

统一返回 200（而非 201）是有意为之：get-or-create 是幂等语义，用 `created` 布尔字段区分「本次新建」与「复用既有」，调用方无需区分 200/201 的 HTTP 语义。

按分叉 3 采纳决策：**不自动激活**。`is_active` 反映 DB 当前状态（通常 false，除非该 scene 恰好已是 active）。激活走 `POST /scenes/{scene_id}/activate`。

---

## 6. 事后编辑 PATCH /takes/{take_id}

```
PATCH /takes/{take_id}
权限：require_admin
必须 async def（同 takes.py 单连接线程安全约束）
```

### 可改字段

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 枚举校验：keeper / ng / hold / tbd；应用层校验（pydantic Literal 或显式检查）返干净 422，不依赖 DB CHECK（失败是 IntegrityError→500） |
| shot | string | 镜次标注 |
| notes | string | 备注，自由文本 |
| scene_id | int | 移场（目标场必须存在，不存在→404，前置校验在 DAL 内，不靠 FK 抛 IntegrityError→500） |
| take_number | int | 改号（同场内，可与 scene_id 同时传） |

部分更新：只改请求体中出现的字段，未传字段保持原值。

### 录制中限制

针对具体某条 take 是否在录，判定方式：查该 take 行的 `end_ts IS NULL`（行级判断，与 activate 用 `session.take_active` 的全局判断不同）。

`end_ts IS NULL` 的 take（正在录制）：

- 不允许改 `scene_id`、`take_number`→409（`{"error": "take_in_progress"}`）。
- 允许改 `notes`（notes 是场记的实时标注，不影响录制逻辑）。
- `status`、`shot` 录制中是否允许改：本 spec 不限制，实现时按需加守卫。

### DAL 方法 update_take_meta

```python
def update_take_meta(
    self,
    take_id: int,
    *,
    status: str | None = None,
    shot: str | None = None,
    scene_id: int | None = None,
    take_number: int | None = None,
    notes: str | None = None,
) -> None:
    """部分更新 take 元数据，处理 UNIQUE 冲突。在一个事务内完成。"""
```

### 冲突处理算法（按分叉 2 推荐，在同一事务内）

**情形 A：仅改 scene_id（移场），不指定 take_number。**

前置：校验 `new_scene_id` 存在于 `scenes` 表，不存在→404（不靠 FK IntegrityError）。

```
target_next = COALESCE(MAX(take_number), 0) + 1 FROM takes WHERE scene_id = new_scene_id
UPDATE takes SET scene_id = new_scene_id, take_number = target_next WHERE take_id = ?
```

用 `COALESCE(MAX(...),0)+1` 处理目标场尚无 take 时 `MAX` 返回 NULL 的情形（NULL+1=NULL 会撞 NOT NULL 约束）。追加为目标场下一号，不撞。

**情形 B：仅改 take_number（同场换号），目标号已被占用。**

```
occupied_take_id = SELECT take_id FROM takes
    WHERE scene_id = current_scene_id AND take_number = new_take_number

-- 交换：借用临时占位号 -1 避免中间态撞唯一键
UPDATE takes SET take_number = -1 WHERE take_id = occupied_take_id
UPDATE takes SET take_number = new_take_number WHERE take_id = take_id
UPDATE takes SET take_number = old_take_number WHERE take_id = occupied_take_id
```

SQLite 的 UNIQUE 索引**逐语句即时检查**，故借 `-1` 临时占位号让三步 UPDATE 中间态不撞唯一键。`take_number` 无正数 CHECK，`-1` 仅事务内瞬时存在，事务提交后不可见。

**情形 C：同时改 scene_id 和 take_number。**

前置：同情形 A，校验 `new_scene_id` 存在，不存在→404。目标 `(new_scene_id, new_take_number)` 若未被占用→直接 UPDATE。若已被占用→本 spec v1 返回 409，不做交换（跨场交换语义复杂，YAGNI）。

**情形 D：仅改 take_number，目标号未被占用。** 直接 UPDATE，无冲突。

`shift`（顺移后续编号）v1 不做，YAGNI。

### audit_log

每次成功编辑写一条：

```json
{
  "action": "take.edit",
  "payload": {
    "take_id": 1,
    "changed_fields": ["status", "scene_id"],
    "conflict_resolution": "swap | append | none"
  }
}
```

---

## 7. 删除 DELETE /takes/{take_id}

```
DELETE /takes/{take_id}
权限：require_admin
必须 async def（同 takes.py 单连接线程安全约束）
```

### 行为

硬删。子表（`take_events`、`take_line_matches`、`transcript_segments`）靠 `ON DELETE CASCADE` 自动清，依赖 `PRAGMA foreign_keys = ON`（`backend/db/lifecycle.py:18` 已确认）。

录制中判定：查该 take 行的 `end_ts IS NULL`（行级判断，针对具体某条 take，与 activate 用 `session.take_active` 的全局判断不同）。录制中→409（`{"error": "take_in_progress"}`）。

二次确认由前端负责，后端不做额外交互。

成功返回 HTTP 204 No Content。

### DAL 方法 delete_take

```python
def delete_take(self, take_id: int) -> None:
    """在一个事务里：写 audit_log → DELETE FROM takes（子表 CASCADE 自动清）。"""
```

执行顺序（在同一事务内）：

1. `SELECT * FROM takes WHERE take_id = ?` 取快照（用于 audit payload）。
2. 写 `audit_log`：action=`take.delete`，payload 含被删 take 快照（scene_id、take_number、status、shot、notes、start_ts、end_ts）。
3. `DELETE FROM takes WHERE take_id = ?`（子表 CASCADE 跟删）。

### 与现有 take 编号逻辑的冲突（必须同期改造）

现 orchestrator `_on_take_start`（orchestrator.py:156）用 `take_number = len(list_takes(scene)) + 1` 计算下一个编号。硬删中间 take 后这套计数逻辑会撞唯一键：以 1/2/3 删掉 2 为例，剩余 take 数为 2，下次 `take.start` 算出 `2+1=3`，与已有的 take 3 撞 `UNIQUE(scene_id, take_number)` → INSERT 抛 IntegrityError。

**本线必须把编号逻辑从「计数」改成「单调最大值」：**

```python
take_number = COALESCE(MAX(take_number), 0) + 1  # 永不复用已删号
```

这条 orchestrator 改动属于 2.x 删除票的范围，**删除端点与编号逻辑改造必须一起落**，不能分批：先上删除端点、未改编号，操作员删完中间 take 后下次开拍会挂（IntegrityError）。

---

## 8. 同场对比只读视图字段契约

本节由 2.A 定字段，2.E 实现端点。字段均为纯事实，无推荐性摘要。

### 建议端点形态

```
GET /scenes/{scene_id}/compare
权限：require_admin
必须 async def（同 takes.py 单连接线程安全约束）
可选参数：?shot=<shot_value>（按 shot 过滤）
```

返回该场所有 take 的事实投影，按 `shot` 值分组。

### 字段契约

每条 take 投影包含：

| 字段 | 来源 | 说明 |
|------|------|------|
| take_id | takes.take_id | |
| take_number | takes.take_number | |
| shot | takes.shot | 用于分组/过滤 |
| status | takes.status | keeper / ng / hold / tbd |
| end_ts | takes.end_ts | NULL 表示录制中 |
| script_diff_exists | takes.script_diff IS NOT NULL | bool，差异数据是否存在 |
| script_deviation_count | COUNT(*) FROM take_line_matches WHERE take_id=? AND diff_type IN ('missing','substitution','insertion') | 剧本偏差数 = missing+substitution+insertion 的行数，不是 ASR 错别字修正数 |
| typo_fix_count | JSON_ARRAY_LENGTH(takes.script_diff, '$.corrected_segments') | ASR 错别字修正数（`l2_take.py:96` 的 corrected_segments，只对真正有修改的 segment 输出）；与 script_deviation_count 是两个不同指标，不得混用 |
| line_matches_hit | COUNT(*) FROM take_line_matches WHERE take_id=? AND diff_type='match' | 命中台词数（diff_type='match' 行） |
| line_matches_total | COUNT(*) FROM take_line_matches WHERE take_id=? | take_line_matches 中该 take 的 diff 行数；insertion 且 line_no=-1 的行（caller 写入时已跳过）不计入，与剧本总台词数不是同一概念 |
| notes_exists | takes.notes IS NOT NULL AND takes.notes != '' | bool，notes 是否有内容 |

### 明确排除

take 级「本场建议 keeper」等推荐性摘要属 2.F（LLM 对比摘要），不在 2.E / 本视图。2.E 只呈现事实字段，不做推断。

---

## 9. WS 通知契约

系统是多观察者架构（`active_observers` + `/view`），写操作完成后需要发 WS 事件，否则连着的 admin/view 看到旧数据。

| 操作 | 事件名 | 最低 payload |
|------|--------|-------------|
| PATCH /takes/{take_id}（改 status 等） | `take.changed`（复用现有） | {take_id, scene_id, 变更后字段} |
| DELETE /takes/{take_id} | `take.deleted`（新增） | {take_id, scene_id} |
| POST /scenes/{scene_id}/activate | `scene.changed`（新增） | {scene_id, scene_code, is_active: true} |

`take.deleted` 和 `scene.changed` 是新增事件，事件名与 payload 结构由本节定义，**实际 publish 接线延到 2.x 接线票落地**。实现时在 orchestrator/路由里对应位置加 `ws.broadcast(event_name, payload)` 调用即可，接口与现有 `take.changed` 一致。

TODO（2.x 接线票）：确认 `take.deleted` 和 `scene.changed` 的前端 handler，确保 UI 能正确移除已删 take 并刷新当前场次显示。

---

## 10. 新增 DAL 方法签名汇总

本期新增三个 DAL 方法，签名见对应各节（§3 / §6 / §7），此处集中重列：

```python
# §3：建场唯一入口
def get_or_create_scene(
    self,
    scene_code: str,
    *,
    description: str | None = None,
    shoot_date: str | None = None,
    int_ext: str | None = None,
    time_of_day: str | None = None,
    location: str | None = None,
) -> tuple[int, bool]: ...

# §6：事后编辑，部分更新 + 冲突处理
def update_take_meta(
    self,
    take_id: int,
    *,
    status: str | None = None,
    shot: str | None = None,
    scene_id: int | None = None,
    take_number: int | None = None,
    notes: str | None = None,
) -> None: ...

# §7：硬删 + audit
def delete_take(self, take_id: int) -> None: ...
```

---

## 11. API 契约汇总表

| Method | Path | Body | 成功响应 | 错误码 |
|--------|------|------|----------|--------|
| POST | /scenes | {scene_code, description?, shoot_date?, int_ext?, time_of_day?, location?} | 200 {scene_id, scene_code, created, is_active}（幂等，用 created 区分新建/复用） | 422 校验失败 |
| POST | /scenes/{scene_id}/activate | — | 200 {scene_id, scene_code} | 404 不存在，409 录制中 |
| POST | /take/start（受影响） | {scene_id, shot} | 200（原有） | 409 scene_id 与 active 不匹配，其余原有 |
| PATCH | /takes/{take_id} | {status?, shot?, notes?, scene_id?, take_number?} | 200 更新后 take | 404 不存在/目标 scene 不存在，409 录制中/跨场撞号，422 枚举不合法 |
| DELETE | /takes/{take_id} | — | 204 No Content | 404 不存在，409 录制中 |
| GET | /scenes/{scene_id}/compare | — | 200 [{take 投影}] | 404 不存在 |

---

## 12. migration 说明

**本期零改 migration。** schema 已具备所需结构（scenes / takes / take_events / audit_log），全部依赖现有表，无新列、无新表。

软删除（未来如有需要）是 v4，排在 diarization spec（`docs/specs/2026-06-02-realtime-diarization-voicenote-design.md`）的 v3 speakers 表之后。本期一律硬删，不加 `deleted_at` 或软删列。

---

## 13. 协调点

**与 diarization spec（⑤）：** diarization spec 要把 take.end 即时触发 L2 改成回填后 gate（`docs/specs/2026-06-02-realtime-diarization-voicenote-design.md` §4）。本 spec 改的是 take.start 的 scene_id 校验逻辑，影响 orchestrator `_on_take_start`（:147），与 `_on_take_end`（:183）是不同 handler。两边都改 orchestrator，落地时注意顺序：建议先落 diarization spec 的 take.end 改造，再落本 spec 的 take.start 校验，减少中间状态下的逻辑混乱。

**take.start 隐式激活 reconcile（⑥）：** 由本 spec §4 处理。`session.activate_scene` 内存更新保留，语义从「激活」退化为「缓存确认」，不再是激活的权威路径。

---

## 14. 验收映射（对回 2.A 验收标准）

| 验收标准 | 本 spec 对应章节 |
|----------|-----------------|
| 管理 API 契约定稿 | §5 / §6 / §7 / §11 |
| DAL 签名 + 冲突算法 | §6（UNIQUE 冲突算法）/ §10 |
| 同场对比字段契约 | §8 |
| 不动 migration + 软删 v4 排序说明 | §12 |
| 分叉已定 → §0 | §0 |

---

## 15. 复审决策（2026-06-03 手测后，Lead 拍板）

本节是手动测试后的覆盖性 addendum。正文 §0–§14 不改，以本节为准。覆盖点共三处：反转 1 覆盖 §7 / §11，反转 2 覆盖 §0 分叉 2 / §6 情形 B 和 C，反转 3 覆盖 §11。另有三条消歧义 pin。

### 反转 1 —— 删除改为软删（覆盖 §7、§11）

原 §7「硬删 + CASCADE + audit_log」作废，改为软删。

**新增列（见反转 3 的 v3 migration）：** `takes.deleted_at REAL`（nullable，SQLite timestamp，NULL 表示未删除）。

**`delete_take` 改写：**

```python
def delete_take(self, take_id: int) -> None:
    """软删：UPDATE takes SET deleted_at = <unix_ts>，audit_log 仍写。子表不 CASCADE。"""
```

执行顺序（同一事务内）：

1. 取快照 `SELECT * FROM takes WHERE take_id = ?`（用于 audit payload）。
2. 写 `audit_log`：action=`take.delete`，payload 含 take 快照。
3. `UPDATE takes SET deleted_at = unixepoch('now','subsec') WHERE take_id = ?`。

子表（`take_events`、`take_line_matches`、`transcript_segments`）数据**保留**，不触发 CASCADE，供撤销恢复用。

**新增 `restore_take`：**

```python
def restore_take(self, take_id: int) -> None:
    """撤销软删：UPDATE takes SET deleted_at = NULL，audit_log 写 take.restore。"""
```

前端撤销栈建议深度 5–10 条（具体由前端定），撤销按钮触发此端点。

**所有 take 查询默认排除软删行。** 以下 DAL 方法一律加 `WHERE deleted_at IS NULL`（或在 JOIN 条件里加）：

- `list_takes`（:319）
- `get_take`（:312）
- `start_take` 内的编号计算（见「next_take_number pin」）
- `update_take_meta` 的目标 take 校验
- 同场对比视图查询（§8 的 `GET /scenes/{scene_id}/compare`）
- 情形 A 移场的 `COALESCE(MAX(take_number),0)+1` 子查询

实现时在 DAL 层统一加过滤，不在路由层各自处理。

### 反转 2 —— 冲突处理改为追加「+」后缀（覆盖 §0 分叉 2、§6 情形 B / C）

原 §0 分叉 2「同场撞号交换编号」和原 §6「情形 B 交换、情形 C 跨场撞号 409」全部作废，改为追加「+」后缀。

**新增列（见反转 3 的 v3 migration）：** `takes.take_suffix TEXT NOT NULL DEFAULT ''`。

**唯一约束改为三元（覆盖原 §0 分叉 2 所述二元约束）：** `UNIQUE(scene_id, take_number, take_suffix)`。

**冲突解决算法（新版，取代原 §6 情形 B / C）：**

`update_take_meta` 在改 `take_number` 或 `scene_id` 时，若目标 `(scene_id, take_number, '')` 已被占用，则给被移动/改号的那条顺位追加一个 `+`：`'' → '+' → '++'`，循环直到 `(scene_id, take_number, take_suffix)` 不冲突。

```
new_suffix = ''
WHILE EXISTS (
    SELECT 1 FROM takes
    WHERE scene_id = target_scene_id
      AND take_number = target_take_number
      AND take_suffix = new_suffix
      AND take_id != current_take_id
      AND deleted_at IS NULL
):
    new_suffix = new_suffix + '+'

UPDATE takes SET scene_id = target_scene_id,
                 take_number = target_take_number,
                 take_suffix = new_suffix
WHERE take_id = current_take_id
```

显示约定：`take_suffix = ''` 显示为 `Take 3`；`take_suffix = '+'` 显示为 `Take 3+`；`take_suffix = '++'` 显示为 `Take 3++`。显示逻辑在前端处理，后端只存 suffix 值。

`TakeNumberConflictError`：不再用于同/跨场冲突（已改为追加后缀）。可废弃；若保留，仅作理论兜底（循环 suffix 累积到不合理长度时的安全阀，实际上不会触发）。

原 §6 情形 A（移场追加 `COALESCE(MAX,0)+1`）不变，仍为「不指定 take_number 时追加到目标场最大号+1」。情形 B（同场换号）和情形 C（同时改 scene_id + take_number）的冲突处理统一按本节算法执行。

### 反转 3 —— migration 改为本期开 v3（覆盖 §11）

原 §11「本期零改 migration、软删 v4 排在 diarization v3 之后」作废。

**本期开 v3 migration。** runner 现已到 v2（`v2_scene_heading.sql`），下一个空号是 v3，不能留空号。

**⚠ 跨线协调（高优先级）：** diarization spec（`docs/specs/2026-06-02-realtime-diarization-voicenote-design.md`）§9 声明 speakers 表用 v3（`v3_speakers.sql`）。本 spec 抢占了 v3，diarization 必须顺延 v4（`v4_speakers.sql`），并在 runner.py 的 `MIGRATION_FILES` 里注册 `4: "v4_speakers.sql"`。**diarization spec 需同步修改** §9 / §6 中的版本号声明，属跨线协调项，由 Lead 通知经纬。

**v3 migration 内容（`v3_scene_take_soft_delete.sql`）：**

① 加 `takes.deleted_at REAL`（NULL = 未删除）。
② 加 `takes.take_suffix TEXT NOT NULL DEFAULT ''`。
③ 把原 `UNIQUE(scene_id, take_number)`（内联约束，schema.sql:47）改为 `UNIQUE(scene_id, take_number, take_suffix)`。

**⚠ 最易出错处——SQLite 无法 ALTER 内联 UNIQUE，必须整表重建：**

SQLite 不支持 `ALTER TABLE ... DROP CONSTRAINT`，内联约束只能靠重建。标准 12 步如下（在 v3 migration SQL 文件里顺序执行）：

```sql
PRAGMA foreign_keys = OFF;

CREATE TABLE takes_new (
    take_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id         INTEGER NOT NULL
        REFERENCES scenes (scene_id) ON DELETE RESTRICT,
    take_number      INTEGER NOT NULL,
    take_suffix      TEXT    NOT NULL DEFAULT '',          -- 新增（反转 2）
    shot             TEXT,
    start_ts         REAL    NOT NULL,
    end_ts           REAL,
    status           TEXT    NOT NULL DEFAULT 'tbd'
        CHECK (status IN ('keeper','ng','hold','tbd')),
    performer_issues TEXT,
    audio_quality    TEXT,
    script_diff      TEXT,
    notes            TEXT,
    deleted_at       REAL,                                 -- 新增（反转 1）
    created_at       REAL    NOT NULL DEFAULT (CAST(strftime('%s','now') AS REAL)),
    updated_at       REAL    NOT NULL DEFAULT (CAST(strftime('%s','now') AS REAL)),
    UNIQUE (scene_id, take_number, take_suffix)
);

-- 拷数据：take_suffix 默认 ''、deleted_at 默认 NULL，其余逐列照搬
INSERT INTO takes_new (
    take_id, scene_id, take_number, take_suffix, shot, start_ts, end_ts,
    status, performer_issues, audio_quality, script_diff, notes,
    deleted_at, created_at, updated_at
)
SELECT
    take_id, scene_id, take_number, '', shot, start_ts, end_ts,
    status, performer_issues, audio_quality, script_diff, notes,
    NULL, created_at, updated_at
FROM takes;

DROP TABLE takes;
ALTER TABLE takes_new RENAME TO takes;

-- 子表 take_events / take_line_matches / transcript_segments 的 ON DELETE CASCADE
-- 声明在它们自己的 CREATE TABLE 里，重建 takes 不动它们；但要 foreign_key_check 验证关系完好。

PRAGMA foreign_key_check;
PRAGMA foreign_keys = ON;
```

重建期间关闭 `PRAGMA foreign_keys` 是 SQLite 整表重建的标准做法，完成后必须重开并做 `foreign_key_check`。子表（`take_events`、`take_line_matches`、`transcript_segments`）的 `ON DELETE CASCADE` 声明在它们自己的 `CREATE TABLE` 里，重建 `takes` 不影响子表声明，但数据关系需要 `foreign_key_check` 验证。

实现 v3 时若 `schema.sql`（全新库初始化路径）也定义 takes，需同步把 `take_suffix`、`deleted_at` 两列和三元 `UNIQUE (scene_id, take_number, take_suffix)` 加进 `schema.sql`，保证全新库与迁移库的 takes 结构一致。

### Pin：next_take_number 排除软删行

`orchestrator.py:156` 的编号逻辑（以及情形 A 移场的 `COALESCE(MAX,0)+1` 子查询）统一写法：

```sql
SELECT COALESCE(MAX(take_number), 0) + 1
FROM takes
WHERE scene_id = ? AND deleted_at IS NULL
```

语义：操作员误触建了 Take 4 再软删，该号释放，下次录制复用 4（对应「误触等于没发生」）。软删行不占号，新建 take 可以复用已软删的号。

### Pin：REC / 建 take 解耦（本期做浅层）

已确认实时 ASR 尚未接入生产（segment 仅靠 `/debug/asr` 注入），「录音中」今天是 UX + 注入门概念。

本期浅层解耦方案：`session` 拆成两个独立标志：

- `take_active`：有当前 take 块（建 take 后为 True，take 结束/软删后为 False）。
- `recording`：录音开关（REC on → True，REC off → False，独立于 take 块是否存在）。

操作语义：
- **Next Take**：建 take 块（`take_active = True`），不进录音态（`recording` 不变）。
- **REC on**：独立开关，`recording = True`，往当前 take 块里写 segment。
- **REC off**：`recording = False`，结束当前 take 的录音。

深层改写（take.end 回填后 gate L2 + 接真实 ASR）归 diarization 线，不在 2.x 本期范围。2.x 本期只做浅层解耦（两个标志），不改 orchestrator 的 take.end 触发链。

### Pin：影响面说明

本 addendum 三条反转使 2.B（DAL 实现）、2.C（API 实现）的既有实现与测试需要**返工**（不是新增）：

- 硬删 → 软删：`delete_take` 实现、所有 `list_takes`/`get_take` 的查询过滤、子表 CASCADE 行为。
- 交换/409 → 后缀：`update_take_meta` 冲突处理逻辑，相关单元测试需重写（测交换的用例全部改测后缀）。
- migration：v3 SQL 文件和 runner 注册。

后续票按本 addendum 写测试，不要按原 §6 / §7 写。
