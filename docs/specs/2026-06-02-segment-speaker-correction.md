# take detail 说话人归属纠正（segment speaker correction）

日期：2026-06-02
配套：`docs/specs/2026-05-29-1.J-1.L-frontend-integration.md`（前端接入主 spec）
设计 review：codex 第二意见已做（见文末 design log），关键意见已吸收。

范围：take detail（录后回看的 take 详情）里，把**单条** segment 的 speaker **改判**给正确的说话人，落库改 `transcript_segments.speaker`。后端加一个嵌套 PATCH 端点 + DAL 方法；前端在 `HistoryTakes` 的 take 详情里把 speaker 渲染成可点（复用并改造死代码组件 `SpeakerLabel`）。**L2 不重跑。**

非目标（scope guard）：

- 给匿名 id 贴人名/角色（另一个 ticket，在 settings page）。
- Live 录制中纠正（Live 的 segment 没有 `segment_id`，需先动 WS/ASR 契约）。
- 批量「合并整组」、「从此往后」粒度。
- L2 重跑、多端实时同步（无 WS correction 事件）。

## 背景

diarization 输出 `SPEAKER_00 / SPEAKER_02` 这种匿名 id，存在 `transcript_segments.speaker`，会分错：要么单句误判（某句归错人），要么把同一个人拆成两个 id。当前 take detail（`HistoryTakes.tsx` 的 `TakeDetail`，77-80 行附近）把 speaker 渲染成纯展示 `<span>`，点不了。`SpeakerLabel.tsx` 是一个**零引用**的可切换 DropdownMenu 死代码（写死 `SPEAKER_OPTIONS = SZA/YY/Unknown`、用 `SPEAKER_TEXT/SPEAKER_DOT` 配色）。本 ticket 把单条 segment 的归属纠正做成真功能。

## 数据现状（grounding）

- `transcript_segments`（schema.sql:137-149）：`segment_id PK, take_id FK(ON DELETE CASCADE), ch CHECK(1/2), speaker TEXT NULL, text NOT NULL, start_frame, end_frame, created_at`。**无 `updated_at`**；`speaker` 列无空串约束（空串拦截必须在 API/DAL 层）；`speaker NULL` = 未知。
- **ch2 恒无 speaker**：`orchestrator._on_asr_final` 注册 ch2 时 `force_speaker_none=True`（orchestrator.py:86/138），写库无视 payload 一律 `None`；L2 只读 `list_segments(take_id, ch=1)`（orchestrator.py:309）。
- DAL 范式：route 调 DAL helper，DAL 内部不被外部拼 SQL；写走 `_write_tx`。现成：`get_take`、`list_segments`、`insert_segment`。
- take detail 端点 `GET /api/v1/takes/{id}` 返回 `TakeDetailDTO {..., segments: SegmentOut[]}`，`SegmentOut` 含 `segment_id/ch/speaker/text/start_frame/end_frame`（takes.py）。
- 鉴权范式：`require_admin`（`Depends`），422 用法见 debug.py:59/133。
- 前端：`request()`（api.ts:16）非 2xx 抛 `ApiError`，JSON body，204 → undefined；命令式 POST 范式见 `startTake/endTake`。react-query：`useTake(id)` key `["take", id]`，全局 `staleTime` 30s（main.tsx）。前端**无测试框架**（package.json 只有 vite/eslint）。
- L2 prompt 用 speaker：`build_transcript_block`（l2_take.py:140）把 speaker 写进 prompt（`None` → "未知说话人"）。**纠正后不重跑 L2 → DB 的 speaker 与已生成的 `takes.script_diff` 会不一致**（已知取舍，见 §3.4 提示）。

## 锁定决策

| # | 决策 | 选择 |
|---|---|---|
| 1 | 语义 | 纠正归属（非贴名） |
| 2 | 落库 + L2 | 落库改 `transcript_segments.speaker`；**L2 不重跑** |
| 3 | 入口 | take detail 录后改（非 Live 录制中） |
| 4 | 粒度 | 单条 |
| 5 | 端点形态 | 嵌套 `PATCH /api/v1/takes/{take_id}/segments/{segment_id}` |
| 6 | 候选 | 当前 take 的 distinct speaker id（**含当前条自身**）+ 「未知（null）」 |
| 7 | 前端组件 | **复用改造** `SpeakerLabel`（救活死代码） |
| 8 | ch2 | **前后端都拦**（前端不渲染可点；后端 422 拒 ch2 改 speaker） |

## 2. 后端

### 2.1 DAL

新增 `get_segment(segment_id) -> TranscriptSegment | None`：按 `segment_id` 取单行（含 `take_id`、`ch`、`speaker`），不存在返回 None。用于 route 做归属/ch 校验。

新增 `update_segment_speaker(segment_id, speaker: str | None) -> int`：`UPDATE transcript_segments SET speaker = ? WHERE segment_id = ?`，返回受影响行数。走 `_write_tx`。**不写 `updated_at`（表无此列）**，不触碰 `takes.script_diff`。

> 归属（take_id 匹配）与 ch1 限制不放进 UPDATE 的 WHERE，而是由 route 先用 `get_segment` 校验后再调 update——这样 404（归属/不存在）与 422（ch2/空串）边界清晰，错误可定位（codex Q1）。

### 2.2 端点

`PATCH /api/v1/takes/{take_id}/segments/{segment_id}`，`async def`，`require_admin`，加在 `backend/api/routes/takes.py`。

请求体 `PatchSegmentBody { speaker: str | None }`——`speaker` **必填字段**（pydantic 无默认值），值可为 `null`。缺字段由 pydantic 自动 422；这样「未知」必须显式传 `{"speaker": null}`，不会与「漏传」混淆。

处理顺序（决定 404/422 边界）：

1. `body.speaker` 若是 `str` 且 `strip()` 为空 → **422**（"speaker must not be blank"）。`null` 合法；非空 `str` 合法（**不校验是否是已知 id**——纠正可改成任意值，候选由前端约束，后端不加 id 白名单以免耦合）。
2. `seg = dal.get_segment(segment_id)`：`None` → **404**；`seg.take_id != take_id` → **404**（嵌套资源「不存在于该 take」，不用 403）。
3. `seg.ch == 2` → **422**（"ch2 segment speaker is immutable"）。
4. `dal.update_segment_speaker(segment_id, body.speaker)`，返回更新后的 `SegmentOut`（200）。

**不发 WS、不动 `take.changed`、不重跑 L2、不改 `script_diff`**（保持 L2 为纠正前快照）。

### 2.3 L2 stale

后端不做任何 stale 标记（无 `updated_at`、响应无 diff 生成时间）。提示完全由前端本地态处理（§3.4）。

## 3. 前端

### 3.1 类型 / API（`types/api.ts`、`lib/api.ts`）

`TranscriptSegmentDTO` 已含 `segment_id/ch/speaker/text/frames`，无需改。

新增：

```ts
export async function correctSegmentSpeaker(
  takeId: number, segmentId: number, speaker: string | null,
): Promise<TranscriptSegmentDTO> {
  return request<TranscriptSegmentDTO>(
    `/api/v1/takes/${takeId}/segments/${segmentId}`,
    { method: "PATCH", body: JSON.stringify({ speaker }) },  // null 也显式带
  )
}
```

不新增 hook；调用方用 `useQueryClient().invalidateQueries({ queryKey: takeQueryKey(takeId) })` 触发 detail refetch（**无乐观更新**，以 refetch 为准，省 rollback）。

### 3.2 改造 `SpeakerLabel`（复用）

props 改为：

```ts
{ speaker: string | null,
  options: (string | null)[],          // 候选，含 null=未知
  onChange: (speaker: string | null) => void,
  disabled?: boolean }                  // ch2 → true，渲染为不可点 muted
```

- 不再依赖 `SPEAKER_OPTIONS`；候选由 props 传入。
- 「未知」用 `null` 表示；菜单项 `null` 显示「未知」；触发器文本 `{speaker ?? "未知"}：`。
- 配色统一走哈希（与 Live 一致）：text 用 `speakerColor(speaker)`（`null` → muted）；dot 新增 `speakerDot(speaker)`——与 `speakerColor` **共用一个 `hash→index` helper**（避免漂移），返回 `SPEAKER_PALETTE` 对应的 `bg-*` class，`null` → `bg-muted-foreground`。在 `constants.ts` 落这两个函数。
- 当前选中项高亮 `cn(opt === speaker && "bg-accent")`，沿用原范式（与 `StatusBadge` 一致）。
- `disabled`：不挂 DropdownMenu，渲染为 muted 纯文本。

`SPEAKER_OPTIONS / SPEAKER_TEXT / SPEAKER_DOT` 改造后变孤儿，但**保留**（settings page 贴名 ticket 可能用），本 ticket 不删。

### 3.3 接入 `TakeDetail`（`HistoryTakes.tsx`）

segment 渲染处（71-80 行）：speaker 从纯 `<span>` 换成 `<SpeakerLabel>`。

- `candidates = useMemo`：`data.segments` 里 distinct 的非 null speaker（**含当前条自身值**，否则唯一 speaker 时当前值会从菜单消失——codex 必改）+ `null`（未知）。
- `onChange(newSpeaker)`：`correctSegmentSpeaker(takeId, seg.segment_id, newSpeaker)` → 成功 invalidate `takeQueryKey(takeId)` + 标记本 take 已纠正（§3.4）；失败（`ApiError`）显示错误提示，不改本地 UI。
- ch2：`disabled`（ch2 的 speaker 恒为 null、本就不显示，gate 仍写明不挂可点 label）。

### 3.4 L2 stale 提示

纠正标记放**父组件 `HistoryTakes` 的本地 state**（如 `correctedTakes: Set<number>`，与现成的 `statusOverrides` / `expanded` 同范式）。**不能放 `TakeDetail`**——它只在展开时挂载，折叠即 unmount，标记会丢，导致提示在「折叠→再展开同一 take」后消失，与「本会话持续」矛盾（codex 工作树 review P2）。`TakeDetail` 改收 `corrected: boolean` prop + `onCorrected(takeId)` 回调；成功 `onChange` 时调 `onCorrected` 置位父组件 state。本 take 被标记时，在 L2 区（`ScriptDiffView` 上方）显示小字「说话人已纠正，剧本分析未更新」。仅本地态，刷新即消（提示性质，可接受）。

### 3.5 不碰

`LiveTranscript`、WS / ASR 链路、settings page 贴名配置。

## 4. 测试

### 后端（pytest，`test_dal.py` / `test_api.py`）

- DAL：`update_segment_speaker` 成功改 / 改成 `null` / `get_segment` 命中与未命中。
- 端点：合法 string → 200 + DB 改；`null` → 200 置未知；空白串 → 422；segment 不存在 → 404；segment 属别的 take → 404；take 不存在 → 404；ch2 segment → 422；无 token → 鉴权失败；**成功后 `takes.script_diff` 不变**（再 GET take detail 比对）。

### 前端

前端**无测试框架**。本 ticket 不引入 vitest（独立 hygiene ticket）。前端验证走**手动验收**，在 runbook（`docs/2026-05-29-1.J-1.L-browser-runbook.md`）补一节：

- take detail 展开 → 点 ch1 segment 的 speaker → 出「切换说话人」菜单，候选 = 本 take 出现过的 speaker（含当前值）+ 未知。
- 选另一个 speaker → 刷新后仍是新值（落库）；选「未知」→ speaker 变未知（PATCH body `null`，非字符串）。
- 纠正后 L2 区出现「说话人已纠正，剧本分析未更新」。
- ch2 segment 的 speaker 不可点。
- 任意 `SPEAKER_XX` 颜色能渲染（哈希分色）。

## 5. Design log（决策与 review 留痕）

- 4 个核心决策（语义/落库·L2/入口/粒度）+ 端点形态、候选含未知、组件路线、ch2 gate，均由用户逐项拍板（见 §「锁定决策」）。
- codex 第二意见关键吸收点：404 边界（wrong-take 用 404 非 403）；`null` 编码必须显式传 PATCH body；候选不排除当前条自身；ch2 前后端都拦；PATCH 后 refetch（不乐观更新）；L2 stale 用前端本地态提示；后端/前端测试清单。
- 与 codex 分歧并由用户裁定：组件路线——codex 倾向新建 `SegmentSpeakerMenu`（改动面窄），用户选**复用改造 `SpeakerLabel`**（救活死代码）。代价是要同时改 onChange 契约 / 配色 / 候选来源，§3.2 已写明改造点。
- codex 工作树 review（第二轮）P2：stale 标记原写在 `TakeDetail`，会被折叠 unmount 丢失；已上提到父组件 `HistoryTakes`（§3.4）。
- 实时更新 bug（手动验收发现）：纠正后 take detail 不实时刷新、要整页刷新。排查（probe + playwright）证明数据/重渲染均正常，根因是 Radix `asChild` trigger 复用同一 DOM 节点、重渲染却不重绘文本。修法：`<SpeakerLabel key={String(seg.speaker)} …>`，speaker 变即强制重挂载。纯前端，无 spec 决策变更。

---

## 6. 补充：take detail 备注区（ch2 语音 + 预留 typing memo）（2026-06-02 追加）

需求来源：用户手动验收第 8 项。ch2 = 物理第二音频通道（环境音 / 旁白类，`speaker` 恒空，靠 `debug/asr` 注入，当前 dev 无数据）。用户指出：手动键入的 **typing memo** 与 ch2 语音备注**同语义层级**（都是这条 take 的「备注」），故备注区做成通用结构、预留 typing memo，本次只显示 ch2、不实现 typing memo。

### 6.1 设计（纯前端，不碰后端）

- `TakeDetail` 把 `data.segments` 按 `ch` 分两组：ch1（主对话）、ch2（备注）。
- **ch1**：现状不变——转录区，speaker 可点纠正（§3.3 + key 修法）。
- **ch2**：若该 take 有 ch2 segment，在 `ScriptDiffView`（L2 区）**下方**渲染「备注」块：
  - 一条细分隔 + 小标题 **「备注」**。
  - 逐条列 ch2 的 `text`，muted 小字；ch2 `speaker` 恒空 → 不显示说话人标签、不可点（符合 §「锁定决策」ch2 不可改）。
- **通用命名 / 结构**：这块语义是「备注 / memo」，不写死 ch2（如组件 `MemoSection`、变量 `memoItems`）。typing memo 将来接入同一块、同层级，不重构。
- **typing memo**：本次**只预留命名/结构，不实现**输入 UI、不动后端（YAGNI）。
- **空状态**：既无 ch2、也无 memo → 整块不渲染（现有所有 take 都不显示，零影响）。

### 6.2 验收

无自动化（前端无测试框架）。手动：`POST /api/v1/debug/asr` 注一条 ch2（runbook ch2「boom 杂音」样例）→ 展开该 take → L2 区下方出现「备注」块、含该条文本；其它无 ch2 的 take 不显示该块。runbook §7 补一条。
