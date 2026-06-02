# 剧本面板接真后端 + 场次 slugline 结构化（1.J–1.L 后续）

日期：2026-06-01
配套：`docs/specs/2026-05-29-1.J-1.L-frontend-integration.md`（前端接入主 spec）
范围：后端加场次 slugline 结构化列（schema 迁移）+ 读剧本端点 + 注入端点带 heading + DEV 播种默认；前端 `ScriptPanel` 从 mock 切到真库 + dev 面板加 heading 输入。属 1.J–1.L 收尾（主 spec §3.6 当初把剧本显示列为「保持本地 mock，无端点」，本 ticket 补上）。

## 背景

`frontend/src/routes/admin/components/ScriptPanel.tsx` 现在渲染写死的 `MOCK_SCENES`，完全不连后端。dev 面板「注入剧本」（`POST /debug/script`）把剧本写进 `scripts` / `script_lines` 表，但只供 L2 跑 diff，不流到面板。结果：注入剧本后面板看不到注入内容（显示 mock，台词碰巧撞了让人误以为接通）。

另外，面板头部的「室外 · 日 · 街道」是正经剧本 slugline 的结构化内容（内外景 · 时间 · 地点），但 `scenes` 表没有这些字段。本 ticket 把它们作为结构化列加上，并让注入剧本时一起填。

## 数据现状（grounding）

- `scenes`：`scene_id, scene_code(unique), description(nullable), shoot_date(nullable), is_active, created_at`。**缺 slugline 字段（内外景/时间/地点）。**
- `scripts`：`script_id, scene_id, raw_text, version, created_at`，`UNIQUE(scene_id, version)`。`get_latest_script(scene_id)` 取最新版本。
- `script_lines`：`line_id, script_id, line_no, character(nullable), text, created_at`。
- 迁移机制：`backend/db/migrations/runner.py` 用 `PRAGMA user_version` 跟踪版本，`MIGRATION_FILES: dict[int,str]` 注册 `版本→.sql`，`apply_migrations` 幂等执行；每个 `.sql` 末尾自己 `PRAGMA user_version = N`。当前只有 `1: v1_init.sql`。`backend/db/schema.sql` 是 canonical 全量参考（与迁移并存，需同步更新避免漂移）。
- DAL 现成：`list_scenes()`、`get_latest_script`、`list_script_lines`、`get_active_scene_id`、`create_scene`、`set_active_scene`。DEV 播种在 `entrypoint.py`：`create_scene("Scene_1")` + `set_active_scene`（仅 DB 空时，幂等）。

## 2. 后端

### 2.1 迁移 v2：scenes 加 slugline 列

新增 `backend/db/migrations/v2_scene_heading.sql`：

```sql
ALTER TABLE scenes ADD COLUMN int_ext     TEXT;  -- 内外景：室内 / 室外
ALTER TABLE scenes ADD COLUMN time_of_day TEXT;  -- 时间：日 / 夜 / 晨 …
ALTER TABLE scenes ADD COLUMN location    TEXT;  -- 场景地点：街道 / 咖啡馆 …
PRAGMA user_version = 2;
```

- `runner.py` 的 `MIGRATION_FILES` 注册 `2: "v2_scene_heading.sql"`。
- `schema.sql` 的 `scenes` 建表同步加这三列（canonical 参考，保持一致）。
- 三列均 nullable TEXT，无 CHECK（值开放，前端自由文本；不枚举约束，正经剧本 slugline 写法多样）。
- 现有 `soundspeed_dev.db`（user_version=1）下次起后端自动升到 v2，旧行三列为 NULL。**注意**：DEV 播种受「DB 空」门控，现有 DB 已有 Scene_1 → 不会补默认 heading；要看默认值要么 dev 面板注入一次（带 heading），要么删 `soundspeed_dev.db` 重新播种。

### 2.2 DAL

- `list_scenes()` 的 SELECT 加 `int_ext, time_of_day, location`（前端头部读它）。
- 新增 `update_scene_heading(scene_id, *, int_ext=None, time_of_day=None, location=None)`：**部分更新**，只写非 None 字段（COALESCE 或动态 SET），避免缺省值清掉已有 heading。走 `_write_tx`。

### 2.3 读剧本端点

`GET /api/v1/scenes/{scene_id}/script`（`async def`，`require_admin`，加在 `backend/api/routes/takes.py` 现有 `/scenes` 旁）。

- 取该场最新 script（`get_latest_script`）+ 行（`list_script_lines`），行按 `line_no` 升序。
- 响应：`{ "script": { "script_id": int, "version": int, "lines": [{"line_no": int, "character": str|null, "text": str}] } }`。
- 该场无剧本 → `{ "script": null }`（200）。scene 不存在同样 `{ "script": null }`（不单独查存在性）。
- DTO：`ScriptLineOut(line_no, character, text)`、`ScriptOut(script_id, version, lines)`。

注：slugline（头部）走 `GET /scenes` 列表（含三列），不放此端点——头部是 scene 元数据，台词是 script 内容，分开。

### 2.4 /debug/script 带 heading

`POST /api/v1/debug/script` 的 body 加可选 `int_ext`、`time_of_day`、`location`（`str | None = None`）。注入剧本行后，若三者有任一非空，对解析出的 `scene_id` 调 `update_scene_heading`。其余逻辑不变。

### 2.5 DEV 播种默认 heading

`entrypoint.py` 播种：`create_scene("Scene_1")` + `set_active_scene` 之后，`update_scene_heading(seed_id, int_ext="室外", time_of_day="日", location="街道")`。仅 DB 空时执行（幂等，与现有播种同门控）。

## 3. 前端

### 3.1 类型 / API

- `types/api.ts`：`SceneDTO` 加 `int_ext: string|null`、`time_of_day: string|null`、`location: string|null`。新增 `ScriptLineDTO { line_no, character: string|null, text }`、`ScriptDTO { script_id, version, lines: ScriptLineDTO[] }`。
- `lib/api.ts`：加 `getSceneScript(sceneId)` → `GET /scenes/{sceneId}/script`（返回 `ScriptDTO | null`，读 `resp.script`）+ `useSceneScript(sceneId)`（key `["scene-script", sceneId]`，`enabled: sceneId != null`）。`injectDebugScript` 增加可选 heading 参数透传到 body。

### 3.2 ScriptPanel 接真库

- 场次列表用 `useScenes()`（端点已存在，现含三列）。当前场 = `is_active===1`（复用 `pickActiveScene`）。
- 左右导航 / 跳转在真实场次列表上走（现只 1 场，导航基本 no-op，多场自动可用）。
- viewScene 的台词用 `useSceneScript(viewScene.scene_id)`。
- 头部渲染：`SCENE {scene_code}` + slugline `{int_ext} · {time_of_day} · {location}`（缺的省略，全缺则不显 slugline 行）+ `description`（有才显）。
- 行映射：`character == null` → 动作描述（无说话人）；有值 → 台词（`character：text`）。复用现 `renderLines` 两种样式。
- 加载 / 空 / 错误态：加载「加载中…」；无剧本「该场暂无剧本」；失败显错误文案。

### 3.3 dev 面板 heading 输入

`SettingsDialog.tsx` 开发者 tab 的剧本注入块，加三个小输入：内外景 / 时间 / 地点，预填 `室外` / `日` / `街道`。注入时随 `injectDebugScript` 一起发。

## 决策点（已定）

1. **slugline 作为结构化列加上**（用户定）：`scenes` 加 `int_ext / time_of_day / location` 三列，注入时填，面板头部显。不做枚举约束（自由文本）。
2. **上传 / 拍照 OCR 按钮保持本地 mock**：后端无 OCR / 剧本导入端点（主 spec §3.6 导入导出列为不接）。按钮留着仍走 MOCK_OCR 本地预览，不接后端。
3. 读剧本端点放 `takes.py`（与 `/scenes` 同模块）。

## 不变量

- 不改 take / transcript / L2 相关代码；不改 `/debug/script` 注入剧本行的核心逻辑（只加 heading 透传）。
- 不改 `ScriptPanel` 视觉骨架（工具栏、导航、卡片样式），只换数据源 + 决策点调整。
- 迁移幂等、向后兼容（旧行三列 NULL，前端按缺省省略）。
- 无 emoji。

## 测试 / 验收

后端（TDD，`.venv/bin/python`，Python 3.12，**禁用 anaconda**）：
- 迁移：临时 DB 应用迁移后 `user_version==2` 且 `scenes` 有三列；v1→v2 升级幂等（重复 apply 不报错）。
- DAL：`update_scene_heading` 部分更新（只写非 None）；`list_scenes` 返回三列。
- 端点：`/debug/script` 带 heading → scene 三列被更新；`GET /scenes/{id}/script` 有剧本返回 lines（按 line_no 升序、character null 与非 null 都正确序列化），无剧本返回 `{script: null}`。
- 全部走红→绿（先写测试看失败，再实现）。

前端（[手动测试]）：
- `pnpm -C frontend build` 通过。
- dev 面板填 heading（室外/日/街道）+ 剧本 → 注入 → 剧本面板头部显「SCENE Scene_1 · 室外 · 日 · 街道」，台词显注入内容（罗湘 / 访谈者…），不再是 mock。
- 无剧本的场显「该场暂无剧本」；导航 / 跳转不报错。

## 工作流（SOP）

Lead 不碰源码，委派：后端（迁移 + DAL + 端点 + 播种）给 backend-agent 走 TDD（红→绿 + 契约/迁移测试）；前端给 general-purpose（[手动测试]）。**后端先行**（前端依赖端点 + SceneDTO 新字段）。各自 diff 过 codex review → 合并。
