# NP/QP 共享输入 × 就地反馈呈现（reconcile 版）

日期：2026-06-06
状态：实现完成，待设备手测 + 与 voice-qp 合并排序
分支：`feat/np-qp-input-feedback`（源 PR #47 `worktree-frontend-design` 的呈现层，重接到主线 #44 调度）

## 背景

PR #47 在 main `7be5303` 上做了「共享输入 × 就地反馈」整条前端线，但其后主线 #44（`0b9b5d3`
块③ memo 文本调度器）已经把 NP/QP 自动派发 + `qp.answer` WS 传输实现并合进 main。两条线在
`MemoInput.tsx` / `session.ts` / `types/api.ts` / `lib/api.ts` 语义撞车（#47 用 dev 期 `?` 前缀显式
路由 + 同步 `postQuery`，#44 用后端自动判 kind + WS 气泡回灌）。

用户定向：QP 答案呈现采用 #47 的「就地队列 + LLM 档案」，弃 #44 的 12s 气泡；由 Lead 把 #47 的
呈现层重接到 #44 已落地的调度 + WS 传输上。

## 重接后的统一模型（三条路径）

| 路径 | 触发 | 走哪 | 答案回灌 |
|---|---|---|---|
| 自动派发 | 正常打字发 memo | `POST /notes` + `CONN_ID`（后端块③判 kind） | kind=query → 撤乐观 note + 转就地 `qaItem(processing)`，答案经 `qp.answer.{CONN_ID}` 按 client_id `resolveQa` |
| 强制查询 | 误判兜底「↩ 其实是提问」 | `POST /api/v1/query` 同步（`postQuery`） | 同步返回直接 `resolveQa` |
| 强制备注 | 误判兜底「✎ 记为备注」 | `POST /notes` 不带 conn_id（后端跳过分类恒 note） | `note.processed` WS → 就地回执 |

弃用：#47 的 `?`/`？` 前缀脚手架、#44 的 `qpAnswer` 12s 气泡（state/action/UI 一并删）。两套传输
（/notes 自动 + /api/v1/query 同步）保留但角色不同（自动派发 vs 显式覆盖），无死代码。

## 契约改动：`qp.answer` payload 带 `client_id`

队列模型要把答案落到对应那条 `qaItem`，而 main 的 `qp.answer` 只带 `connection_id + answer_text`，
无法对应。故扩展：

- `QpAnswerPayload`（`backend/core/events.py`）加 `client_id: str | None = None`。
- `run_qp_and_broadcast` / `schedule_qp_broadcast`（`backend/api/routes/query.py`）加可选 `client_id` 入 payload。
- `POST /notes` 文本 query 分支（`takes.py`）传 `body.client_id`。
- 前端 `QpAnswerMsg` 加 `client_id?: string`；`useLiveConnection` 的 `qp.answer` 处理器 → `resolveQa(client_id, answer_text)`。

**与 voice-qp 共享同一字段契约**：`worktree-feat+voice-qp` 的 `012a80c` 已为语音 query 给 `qp.answer`
加了同名 `client_id` 字段（语音 202 时未知 kind，靠 WS 带回精确撤 pending）。本线给文本 query 路径
也透传 client_id。字段定义相同（`client_id: str | None = None` / `client_id?: string`），故契约一致；
但两线注释口径不同，合并不是「零冲突」：

- `events.py` / `types/api.ts`：仅 client_id 字段附近的注释有琐碎冲突，取任一即可。
- `useLiveConnection` 的 `qp.answer` 处理器是**真分叉**：本线 `resolveQa(client_id, answer_text)`（队列）
  vs voice-qp `setQpAnswer + removePending`（气泡）。后合者按「队列取代气泡」收敛（保留 resolveQa）。
- `query.py` 的 `client_id` 透传是本线独有（voice-qp 走自己注入式 `_schedule_qp_broadcast`，未动 query.py），无冲突。

## 验证

- `tsc -b` 干净、`vite build` 通过、`eslint` 零新增错（仅余声纹既有 `EnrollRecorderDialog.tsx:41`，与 main 一致）。
- 后端 TDD 红→绿；全量 `pytest` **912 passed / 12 skipped**。
- Playwright 结构冒烟（隔离 dev server，无后端）：/admin 干净渲染、无 JS/渲染错；业务三 tab 无「LLM 反馈」、
  底栏「LLM 历史」入口在、MemoInput 新 placeholder、设置页文件名格式段实时预览生效（`Scene 1·Shot 1·Take 1 → Scene1 · Shot1 · Take1`）、
  「LLM 历史」点开档案浮层渲染空态「还没有问答或 L2 推送」。
  注：仅验证「渲染 + 不崩」；数据驱动的队列/答案/未读流转需 LLM 后端，归设备手测（下节）。

## 待办：设备级行为手测（真机/浏览器，需 LLM 后端）

1. 打字普通备注 → 见就地「处理中」→ `note.processed` 落定转「已记录 <类别> <内容>」回执，3s 自走。
2. 打字一句问句（如「第三场 NG 几条」）→ 后端判 query → 就地「正在查询…」→ `qp.answer` 到达后该行转答案；
   多条并发时各自落到对应行（client_id 对应正确）。
3. 误判互转：回执「↩ 其实是提问」起一条查询；答案行「✎ 记为备注」归一条备注回执。
4. 「LLM 历史」入口：L2 推送 / QP 新答案亮未读点；点开档案浮层见 QP 问答 + L2 时间线，点开即清未读。
5. 文件名格式：切预设/前缀/补零/分隔符，Live 分隔条 + titlebar 实时随。
6. 横竖屏（iPad 横竖 + 手机竖屏）各过一遍，业务三 tab/底栏控制视觉与 main 一致。

> 注：本线呈现层组件（`InlineFeedbackQueue` / `LLMArchiveSheet` / `AdminHome` / `BottomControlBar`）与 PR #47 逐字一致，
> 仅胶水层（MemoInput/session/useLiveConnection/feed-actions/api/types）重接。档案浮层开合等纯呈现交互沿用 #47。
