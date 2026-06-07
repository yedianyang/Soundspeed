# NP 重设计 —— 一步式结构化提取 + 确定性解析

状态:设计待评审（v2，spike 后重定架构）　|　日期:2026-06-07　|　分支:worktree-feat-np-qp-quality　|　范围:仅 NP(文字+语音) + 回灌契约 + 前端队列

## 1. 背景与目标

现状:NP(`run_np_note`/`run_np_voice`)= 一次 forced `structure_note`，定死单条 `{take_id, category, content}`，一次只能改一条、靠模型猜 take_id、类别靠硬编码中文关键词表、Mark 与文字 note 揉一起。

目标(轴=输出质量+任务泛化，不碰延迟/显存):① 多目标写(一句话改多条 take 的 Mark/落多条 note) ② 读 DB 状态消歧+自我纠错 ③ Mark 与文字 note 拆开。范围:只动 NP note 分支 + NP 回灌契约 + 前端队列。**不碰 QP、不碰 note/query 调度器。**

## 2. 关键决策依据:spike（2026-06-07，打真 E4B，均亲自复跑核对）

`experiments/2026-06-07-np-agent-spike/`：
- **两步 forced agent 循环:3/5**。失败=复合 mark+note 漏写、引用对不上硬标错(4B 多步规划弱)。
- **一步提取，可选+嵌套 schema:0/24**。不公平测试:4B 走最小合法 JSON，可选字段全跳。
- **一步提取，扁平+全 required+哨兵:21/24**。两个 CRUX(「第四进」→shot 4、复合 mark+note)全 3/3。

**结论:E4B 不擅多步规划，擅一次性扁平结构化提取。架构据此定为「一步式提取 + 确定性解析」。**

**Schema 法则(对 E4B 的硬约束):扁平 + 全字段 required + 哨兵值。可选字段必被跳、嵌套对象会退化。**

## 3. 架构

NP note 分支 = 模型一次提取意图 → 代码确定性解析 + 查存在 + 多目标 apply。文字/语音共用，语音先 ASR。

### 3.1 模型层:唯一一个 forced 工具 `extract_np`（扁平、全 required、哨兵）
- `scene_ordinal: int`（0=当前场）
- `shot_ordinal: int`（0=当前镜）
- `take_ordinals: int[]`（[]=不按编号）
- `deictic: enum[none, current, prev]`（这条=current；上一条/刚才那条=prev；用了编号=none）
- `mark: enum[pass, ng, keep, tbd, none]`（none=无打标意图）
- `note_text: str`（""=无备注）
- `note_category: enum[note, issue]`
输入:system(字段语义 + 「第N进=shot_ordinal」等映射 + 当前上下文:场/镜/活跃条/上一条) + raw_text。

### 3.2 代码层:确定性解析 → 查存在 → apply
1. **解析**:scene_ordinal/shot_ordinal/take_ordinals/deictic + 当前上下文 → 具体 take_id 列表(多目标)。0/[]→当前场/镜；deictic current→活跃 take、prev→最近完成 take；take_ordinals 非空→该场镜下这些 take_number。
2. **查存在**:解析不出/不唯一/目标不存在 → `clarify`(确定性，广播候选，不写)。根治「硬标错」。
3. **apply(多目标)**:`mark!=none` → `set_take_status`(每个 take_id)；`note_text!=""` → `insert_note`(每个 take_id)。两者都做。

### 3.3 文字 vs 语音
- 文字:调度器判 note → `extract_np`(raw_text) → 解析/apply。
- 语音:调度器判 note → 一次多模态 ASR → 转写当 raw_text → 同一步。
- 调度器(note/query)本轮不动。

注:`mark_takes`/`add_note`/`find_takes`/`get_take` 不再是模型可选 FC 工具，降为代码内部函数；模型只调 `extract_np` 一次。drop 掉 select_action 循环 + catalog，显著简化。

## 4. Mark / note 拆开
mark 与 note_text 分开，一句可同时出。例「这条不错，演员后半段的表演可以后期使用，保」→ `mark=keep` + `note_text="演员后半段的表演可以后期使用"`（spike Case 3 实证 3/3）。`note_category` 区分 note/issue。

## 5. 引用解析（代码确定性）
- ordinal(第N场/进/次) + deictic(这条/上一条):模型提取，代码解析。
- 显式 ASCII 前缀(parse_note "72 3"):可预填/交叉校验(折叠旧 D3「已解析字段被丢」缺口)。
- 查存在失败 → clarify。
- **已知 v1 缺口**:按内容引用(「收音不好那条」)需加搜索步，本版不做，留扩展位。

## 6. 写安全
能唯一解析 → 直接 apply + 回灌「改了哪几条」；解不出/歧义/不存在 → clarify(确定性，无状态，用户再说一句清楚的当新输入重跑)。

## 7. 回灌契约（WS）—— 破坏性变更，本轮随前端一起改
- `note.applied.{conn_id}`(替代 note.processed):`{client_id, changes:[{op:"mark"|"note", take_id, scene_code, shot, take_number, take_suffix, status?, content?, category?}], ts}`。一个 client_id 带一组变更(纯 mark 也走这里，否则前端 pending 挂死)。
- `note.clarify.{conn_id}`(新):`{client_id, message, candidates:[{take_id, scene_code, shot, take_number, status}], ts}`。
- `note.failed`:保留，仅真失败(parse_error/timeout/model_unavailable/asr_unclear)。

## 8. 前端队列适配（本轮范围）
- `types/api.ts`:新增 `NoteAppliedMsg`/`NoteClarifyMsg`；`FeedReceipt` 加 `changes[]`；新增 `ClarifyItem`。
- `store/session.ts`:`noteApplied(m)` → 按 client_id 移除 pending + 推一条带 changes 的 receipt；`noteClarify(m)` → 移除 pending + 推 clarify item。
- `InlineFeedbackQueue.tsx`:`ReceiptRow` 渲染 N 条变更(如「已记录:第2条→keep、第3条→keep」)；新增 `ClarifyRow`(显示 message + 候选，用户经输入框重发，新 client_id)。
- `hooks/useLiveConnection.ts`:接 `note.applied`/`note.clarify` topic。
- 不变:PendingNote(仍一条输入一条 pending)、QaItem(QP 答案)、note.failed 失败态/重试。

## 9. 不变的部分（本轮不碰）
QP 管线与 `query_session` 工具；note/query 调度器(`classify_memo`/`route_memo`/语音 hop A)；take/note 存储层(`set_take_status`/`insert_note`/`update_take_meta` 复用)；take 卡片状态渲染。比 agent 方案新增更少(无新模型可选工具)。

## 10. 测试 / 评测
- 单测:`extract_np` 调用契约、解析函数(ordinal/deictic→take_id)、查存在→clarify、多目标 apply、回灌 payload 形状。模型决策用 stub。
- **评测 harness(B 类门控，独立任务)**:NP 是 B 类(改模型行为)，按「B 类改动前先搭 harness」先行。标注 fixture 覆盖提取准确率:ordinal/deictic/mark/note/多目标/英文口语泛化。spike 的 8 用例×3 是种子，扩成正式 fixture + 固定解码，与单测分开门控。**实现在 harness 就位后才开始。**

## 11. 风险
- 提取仍可能错(21/24) → 查存在 clarify 兜；harness 量化盯回归。
- 哨兵语义(deictic=none vs current) → 应用层兜底当前条(spike Case 6 实证)。
- 契约破坏性变更 → 前端同轮改，一起出。
- 内容引用缺口 → v2。
