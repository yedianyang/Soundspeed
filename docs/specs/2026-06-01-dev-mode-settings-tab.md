# dev-mode 设置弹窗分 Tab（1.J–1.L 后续 UX）

日期：2026-06-01
配套：`docs/specs/2026-05-29-1.J-1.L-frontend-integration.md`（前端接入主 spec）
范围：前端单文件 `frontend/src/components/admin/SettingsDialog.tsx` 结构重排。纯 UI，无逻辑 / 接口 / store / 类型改动。

## 背景

设置弹窗现为单列长滚动，把生产配置（音频输入、演员 / 说话人绑定、界面语言）和连接 + 调试（服务器连接、剧本注入、一键跑完整 take）混在一起。后者在日常使用时是噪音。分到独立 tab，让正常配置与开发 / 测试互不干扰。

## 决策

弹窗内用 Radix Tabs（`components/ui/tabs.tsx` 已存在，`AdminHome` 已在用，无新依赖）。两个 tab：

- **常规**：音频输入设备、演员 / 说话人双栏绑定、界面语言。
- **开发者**：服务器连接（API 地址 + connection pill + Admin Token 输入 / 保存）、开发 / 测试（剧本注入 textarea + 按钮、一键跑完整 take textarea + 按钮）。

## DEV 门控

- **开发者 tab 本身始终渲染**（含服务器连接）：token 在生产环境也要填，不能藏进 DEV-only。
- **开发 / 测试两块（剧本注入 + 一键跑完整 take）仍 `import.meta.env.DEV` 门控**：prod 构建 tree-shake 掉。prod 下开发者 tab 只剩服务器连接。

## 不变量（重排必须保持，逐条回归）

- 所有现有 state / handler 原样保留：token 保存（`localStorage` + `setToken` + invalidate `scenes`/`takes`）、`handleInjectScript`、`handleRunFullTake`（`startRecordingLocal` → `startTake` → 逐段 `injectDebugAsr` → `endTake` → `stopRecordingLocal`）、演员 / 说话人增删绑定合并。
- 不改任何 API 调用、store、类型、`devFixtures`、parser。
- 默认选中 tab = 常规。
- 弹窗滚动行为保留（`max-h-[70vh] overflow-y-auto`）；每个 tab 内容各自可滚。
- 无 emoji。

## 验收（[手动测试]）

- `pnpm -C frontend build`（`tsc -b && vite build`）通过，0 error。
- 打开设置：默认停在常规 tab，看到音频 / 演员 / 语言；切到开发者 tab，看到服务器连接 + 剧本注入 + 一键跑完整 take。
- token 保存仍触发重连 + scenes/takes 重取（连接 pill 翻 open）。
- 一键跑完整 take 仍跑通整条链路（start → 注入 → end → L2 → History 出 take），重排后回归一次。
- prod 构建产物里开发者 tab 无剧本注入 / 一键跑 take 代码（DEV 门控生效）。

## 工作流

走 SOP 委派：Lead 不碰源码，前端 subagent 实现 → 构建 + 视觉回归 → codex review diff → 合并。UI 改动按 [手动测试] 记，无组件测试基建（与 1.J–1.L 一致）。
