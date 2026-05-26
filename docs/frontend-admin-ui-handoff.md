# 前端 Admin UI 对接说明

更新时间：2026-05-26（v2：底部控制条与可访问性修订）

## 本地运行

前端目录是 `frontend/`，当前是 React + TypeScript + Vite + Tailwind CSS 4 + shadcn/Radix UI 组件。

```bash
cd frontend
pnpm install
pnpm dev
```

启动后访问：

- Admin 主界面：`http://localhost:5173/admin`
- 根路径：`http://localhost:5173/`，会自动跳转到 `/admin`
- 共享视图占位页：`http://localhost:5173/view`

`vite.config.ts` 已配 `server.host: true`，dev server 默认监听所有网卡。终端会同时打印 `Network: http://<本机 IP>:5173/`，同 Wi-Fi 下手机/平板直接打开该地址即可调试移动视图。如果连不上，先排查 macOS 防火墙是否拦 node、路由器是否启用 AP 隔离。

如果本机没有 `pnpm`：

```bash
corepack enable
corepack prepare pnpm@latest --activate
```

提交或交付前建议跑：

```bash
cd frontend
pnpm build
pnpm lint
```

当前已验证：`pnpm build` 和 `pnpm lint` 均通过。

## 页面定位

当前 `/admin` 是电影同期录音部门使用的本地场记工作台。核心目标不是做通用聊天或文档工具，而是把录音现场最常用的信息压在一个屏幕内：

- 现场输入状态：输入设备、声道电平、LLM pipeline 状态。
- 实时转录：正在录制的 take 与最近一条历史 take。
- 剧本对照：当前 scene 的剧本台词。
- 历史 take 管理：Scene / Shot / Take / 状态快速修正。
- LLM 反馈：汇总、台词差异、Ch2/现场提示。
- 底部操作：memo、scene/shot/take 切换、状态标记、next take、删除、录制开关。

当前 UI 是前端 mock 状态，交互主要存在本地 React state 中，还没有接后端持久化或真实音频/ASR/LLM 数据流。

## 信息架构

桌面布局：

```text
┌────────────────────────────────────────────────────────────────────┐
│ 顶部状态条：导入文件 | Input + 电平 | LLM 状态 | 观察者 | 导出 | 设置 │
├───────────────────────────────────┬────────────────────────────────┤
│ Live Transcript                   │ 右侧 Tabs                       │
│ 最近历史 take + 当前录制 take       │ 剧本 / History / LLM 反馈          │
├───────────────────────────────────┴────────────────────────────────┤
│ Memo 输入                                                           │
│ Scene / Shot / Take / Mark      Next take / Delete        REC       │
│ ASR / LLM / dB / observer 日志                                      │
└────────────────────────────────────────────────────────────────────┘
```

移动端布局：

```text
┌──────────────────────────────────────────────┐
│ 顶部状态条                                    │
├──────────────────────────────────────────────┤
│ Tabs：Live / 剧本 / History / LLM 反馈          │
│ 当前 tab 内容                                  │
├──────────────────────────────────────────────┤
│ 底部控制区                                    │
└──────────────────────────────────────────────┘
```

响应式规则：

- `md` 以下：主内容合并为一个卡片，用顶部 tabs 切换四个视图。
- `md` 及以上：左侧固定显示 Live Transcript，右侧固定宽度 `420px` 显示剧本、历史、LLM 反馈 tabs。
- 底部控制条始终固定在页面底部区域，不跟随主内容滚动。
- 底部 Scene / Shot / Take / Mark 四个 chip 在 `sm` 以下用 `flex-1` 等分宽度强制不换行；`sm` 及以上恢复紧凑左对齐。REC 圆采用 `absolute right-3 sm:right-5 bottom-2`，独立于左侧 chip 行的高度变化。

## 当前已接入页面和组件

当前 `/admin` 实际渲染入口：

- `frontend/src/App.tsx`
- `frontend/src/routes/admin/AdminHome.tsx`
- `frontend/src/components/admin/BottomControlBar.tsx`

`/view` 当前只是共享视图占位：

- `frontend/src/routes/view/ViewHome.tsx`

## 视觉设计原则

| 设计点 | 当前实现 | 设计意图 |
|---|---|---|
| 全屏工作台 | `h-dvh w-screen overflow-hidden` | 现场使用时避免页面整体滚动，主内容和底部控制区稳定。 |
| 低噪音背景 | 页面背景 `bg-muted/50`，卡片白底 | 让 transcript 和控制按钮优先，而不是做营销式视觉。 |
| 圆角胶囊控件 | 顶部状态 chip、底部 Scene/Shot/Take、Mark、REC | 适合触屏和现场快速点击，减少小控件误触。底部 chip 加淡色外框（`border-border/60`），与背景区分更清晰。 |
| 单屏高密度信息 | 头部状态、双栏主内容、底部控制 | 录音现场需要扫视，不适合多页跳转。 |
| 状态颜色 | 绿色 keeper/input ok，红色 recording/ng，主色 hold/pass/LLM warn | 用颜色承担状态识别，文字只做确认。 |
| 等宽数字 | 时间、take 编号、状态日志使用 `font-mono` | 场记编号和时间更容易纵向扫描。 |
| 动态电平 | `LevelMeter` 每 80ms 刷新随机柱状高度 | 表示输入通道正在活动；当前为 mock 动画。 |
| 当前录制强调 | 当前 take 有时间分隔线、partial 文本和光标动画 | 区分已完成转录和实时 ASR 流式结果。 |
| 历史内容弱化 | 最近历史 take 使用 muted 颜色 | 用户焦点留给正在录制的 take。 |

## 顶部状态条

位置：`AdminHome.tsx` header 第一行。

| 控件 | 图标 / 文案 | 当前行为 | 设计意图 | 后端对接目标 |
|---|---|---|---|---|
| 导入已录制文件 | `Folder` 图标 | 只有按钮和 title，没有绑定逻辑 | 允许导入已有录音进行离线分析或补录处理 | 打开文件选择器，进入 file source 流程 |
| Input 状态 chip | 绿色点 + `Input` + `MacBook Microphone` | 展示 mock 输入设备（无 onClick 时渲染为 `<div>`） | 告诉用户当前录音输入源是否正常 | 显示真实设备名、采样率、声道数、错误状态 |
| Ch1 电平 | 绿色小电平柱 | mock 随机跳动 | 表示主收音声道有输入 | 接入实时 RMS/峰值电平 |
| Ch2 电平 | 主色小电平柱 | mock 随机跳动 | 表示第二声道或辅助输入状态 | 接入 Ch2/boom/lav 等通道电平 |
| LLM 状态 chip | 状态点 + `LLM` + detail | 点击后在 `Idle/L1/L2/L3/Voice/Photo/Script` 间循环（带 onClick 时渲染为 `<button>`，可键盘聚焦） | 演示 pipeline 状态可视化和异常提示入口 | 显示真实 LLM/ASR/脚本/照片上下文队列状态 |
| 观察者人数 | `Eye` 图标 + `3` | 静态展示 | 表示当前有 3 个旁观端或共享视图连接 | 接入 websocket/session observer 数 |
| 导出 | `Upload` 图标 | 只有按钮和 title | 场记单、字幕、LLM 汇总导出入口 | 导出 CSV/PDF/SRT/场记单 |
| 设置 | `Settings` 图标 | 只有按钮 | 设备、模型、项目设置入口 | 打开设置面板或路由 |

## 主内容：Live Transcript

位置：桌面左侧卡片；移动端 `Live` tab。

| 元素 | 当前行为 | 设计意图 | 后端对接目标 |
|---|---|---|---|
| 最近历史 take | 显示 `HISTORY_TAKES.slice(-1)`，弱化颜色 | 给当前 take 提供上一条上下文，方便判断连续性 | 显示最近完成 take 或可配置历史窗口 |
| take 编号 | 左侧 `T4` / `T5` | 快速锚定正在看的 take | 使用后端 take id / slate 编号 |
| 当前 take 分隔线 | `Take 5 · 14:31:24` | 明确当前实时段落开始时间 | 使用真实录制开始时间 |
| speaker 标签 | 已完成 take 显示 `SZA：` / `YY：`，录制中不显示 | ASR 流式阶段先不强行分配说话人，LLM 完成后再显示 | 接入 diarization / LLM speaker assignment |
| speaker 下拉 | 点击 speaker 可选 `SZA`、`YY`、`Unknown` | 现场快速修正说话人，不进入复杂编辑页 | 持久化 speaker override |
| partial 文本 | 当前 `因为我担心你会`，斜体 + 光标闪烁 | 表示 ASR 正在流式输出，尚未 finalize | 接入实时 ASR partial result |
| take 状态点 | 非 recording take 右侧显示状态圆点 | 不占用文字空间也能扫出 keeper/ng/hold | 接入 take status |

状态颜色：

| 状态 | 标签 | 颜色 | 含义 |
|---|---|---|---|
| `keeper` | `KEEP` | 绿色 | 可用条 / 推荐保留 |
| `ng` | `NG` | 红色 | 不可用或明显问题 |
| `hold` | `PASS` | 主色 | 暂不决定 / 待复核 |
| `recording` | `REC` | 红色闪烁 | 正在录制 |

## 主内容：剧本 tab

位置：桌面右侧 `剧本` tab；移动端 `剧本` tab。

| 元素 | 当前内容 | 设计意图 | 后端对接目标 |
|---|---|---|---|
| 标题 | `剧本内容` | 当前 panel 类型说明 | 可替换成 scene 名称或页码 |
| scene 信息 | `SCENE 3 / 室内 / 夜 / 客厅` | 给 transcript 提供拍摄上下文 | 接入剧本解析和 scene metadata |
| 动作描述 | `SZA 坐在沙发上，YY 站在窗边。` | 保留非对白信息，帮助判断表演和调度 | 接入剧本正文 |
| 台词 | SZA / YY 三句台词 | 给 LLM 和用户做台词对照 | 接入当前 scene 的锁定剧本 |
| 底部说明 | `剧本由制片部门上传，拍摄前锁定` | 强调剧本是对照基准，不是现场自由编辑稿 | 对接剧本版本和锁定状态 |

## 主内容：History tab

位置：桌面右侧 `History` tab；移动端 `History` tab。

每张历史 take 卡片是一个 `<div>` 容器（视觉上保留 hover/rounded 样式），当前没有整体点击逻辑。卡片内部的 badge 是真正的 Radix DropdownMenuTrigger，避免嵌套交互。badge 下拉用本地 state override。

| 控件 | 当前行为 | 设计意图 | 后端对接目标 |
|---|---|---|---|
| `Scene N` badge | 点击下拉，可选 Scene 1-4 | 现场发现 slate 记错时快速改 scene | 更新 take metadata |
| `Shot N` badge | 点击下拉，可选 Shot 1-4 | 快速修正 shot | 更新 take metadata |
| `Take N` badge | 点击下拉，可选 Take 1-5 | 快速修正 take 编号 | 更新 take metadata，并处理编号冲突 |
| 状态 badge | 点击下拉，可选 KEEP/NG/PASS | 快速给历史 take 改状态 | 更新 take status |
| 时间 | `14:30` | 快速定位录制时间 | 显示真实结束时间或开始时间 |
| 文本摘要 | 拼接该 take 的台词，最多两行 | 不进详情页也能看出该条内容 | 接入 ASR final transcript summary |

## 主内容：LLM 反馈 tab

位置：桌面右侧 `LLM 反馈` tab；移动端 `LLM 反馈` tab。

| 反馈类型 | 当前示例 | 设计意图 | 后端对接目标 |
|---|---|---|---|
| `summary` | `T4 表演完整，台词与剧本一致。本场建议 keeper。` | 给场记一个 take 级别结论 | Gemma agent 汇总 |
| `diff` | `L102 改词：...` | 标出与剧本的差异 | 剧本对齐 + ASR final 文本比较 |
| `note` | `Ch2 提示：...` | 纳入现场或第二声道提示 | Ch2/人工 memo/LLM 事件融合 |
| 底部说明 | `每次 take 结束后由 L2 / NP / SP Pipeline 推送` | 说明反馈不是实时逐字生成，而是 take 完成后推送 | 对齐 pipeline 生命周期 |

## 底部控制条

位置：`BottomControlBar.tsx`，当前已接入 `/admin`。

### Memo 输入

| 控件 | 图标 / 文案 | 当前行为 | 设计意图 | 后端对接目标 |
|---|---|---|---|---|
| memo 输入框 | placeholder：`Typing memo · 例：第三条结尾好，可以用` | 可输入但不保存 | 现场快速记录人工判断 | 保存到当前 take 或当前时间点 |
| 录音 memo | `Mic` 图标 | 只有按钮和 title | 支持口述 memo，减少打字 | 触发短录音或语音 memo ASR |

### Slate 与状态控制

| 控件 | 图标 / 文案 | 当前行为 | 设计意图 | 后端对接目标 |
|---|---|---|---|---|
| Scene 下拉 | `Scene 3` + `ChevronDown` | 打开 mock 菜单，显示 1/当前/4/新建 | 快速切换或新建 scene | 接入项目 slate 树 |
| Shot 下拉 | `Shot 2` + `ChevronDown` | 同上 | 快速切换或新建 shot | 接入 shot metadata |
| Take 下拉 | `Take 5` + `ChevronDown`，高亮白底 | 同上 | 当前 take 是最常操作项，所以视觉更突出 | 接入当前 take 编号 |
| Mark 状态按钮 | 状态点 + `NG`/`KEEP`/`PASS` | 点击按 `NG -> KEEP -> PASS` 循环 | 单击快速标记当前 take，不打开菜单 | 更新当前 take status |

### Take 操作

| 控件 | 图标 / 文案 | 当前行为 | 设计意图 | 后端对接目标 |
|---|---|---|---|---|
| Next take | `Plus` 图标 + `Next take` | 只有按钮样式 | 结束当前条并准备下一条 | finalize 当前 take，创建下一条 |
| Delete last | `Trash2` 图标 | 只有按钮和 title | 删除上一条误触或错误 take | 删除或软删除最后 take，需二次确认策略 |
| REC 圆形按钮 | `m:ss` 或 `REC` | 点击切换本地 `isRecording`：录制中按 `Date.now` 基线每 250ms 刷新经过秒数（防 setInterval 漂移），停止后归零；样式上用 `absolute right-3 sm:right-5 bottom-2` 固定到底栏右下，不受左侧 chip 行高度变化影响 | 最大、最醒目的主操作，适合现场快速开始/停止 | 控制真实录音 session，显示真实时长 |

### 状态日志

底部最下方目前是 `debug log` 占位（绿色点 + 文字），等接入真实指标前先压缩到最低视觉权重。

历史方案里曾包含横向滚动的指标条（ASR 延迟、LLM 队列、Ch1/Ch2 电平、observer 数），后端对接时建议沿用这个思路：只放低频关键指标，不放长日志。

## 图标清单

| 图标 | 出现位置 | 含义 |
|---|---|---|
| `Folder` | 顶部状态条 | 导入已录制文件。 |
| `Eye` | 顶部状态条 | 当前观察者/共享视图连接数。 |
| `Upload` | 顶部状态条 | 导出场记或分析结果。 |
| `Settings` | 顶部状态条 | 设置入口。 |
| `Mic` | 底部 memo 输入框 | 语音 memo。 |
| `ChevronDown` | 底部 Scene/Shot/Take，下拉选择器 | 表示可展开选择。 |
| `Plus` | `Next take`、下拉菜单新建项 | 新建或进入下一条 take。 |
| `Trash2` | 底部删除按钮 | 删除上一条或最后一条 take。 |

## 交互状态清单

| State | 所在文件 | 当前用途 | 是否持久化 |
|---|---|---|---|
| `mobileTab` | `AdminHome.tsx` | 移动端 Live/剧本/History/LLM 切换 | 否 |
| `sideTab` | `AdminHome.tsx` | 桌面右侧剧本/History/LLM 切换 | 否 |
| `llmIndex` | `AdminHome.tsx` | 点击 LLM chip 循环 mock pipeline 状态 | 否 |
| `LevelMeter.heights` | `AdminHome.tsx` | mock 电平动画 | 否 |
| `TakeBlock.overrides` | `AdminHome.tsx` | speaker 手动改名 | 否 |
| `HistoryTakes.overrides` | `AdminHome.tsx` | take 状态手动覆盖 | 否 |
| `sceneOverrides` | `AdminHome.tsx` | 历史 take scene 覆盖 | 否 |
| `shotOverrides` | `AdminHome.tsx` | 历史 take shot 覆盖 | 否 |
| `noOverrides` | `AdminHome.tsx` | 历史 take 编号覆盖 | 否 |
| `isRecording` | `BottomControlBar.tsx` | REC 圆形按钮本地切换 | 否 |
| `elapsed` / `startRef` | `BottomControlBar.tsx` | REC 录制时长（基于 `Date.now`，每 250ms 刷新；停止归零） | 否 |
| `mark` | `BottomControlBar.tsx` | 当前 mark 在 NG/KEEP/PASS 间循环（类型为 `Status`，非 string） | 否 |

## 与后端/同事对接时需要补的契约

| 对接点 | 当前 mock | 建议契约 |
|---|---|---|
| 输入设备 | 固定 `MacBook Microphone` | 设备 id、设备名、声道数、采样率、错误状态。 |
| 电平 | 随机数动画 | 每声道 RMS/peak、更新时间、是否 clipping。 |
| ASR partial/final | 常量文本 | take id、channel、segment id、partial/final、timestamp。 |
| speaker assignment | 本地 dropdown override | speaker id/name、confidence、manual override 标记。 |
| take metadata | 本地 scene/shot/take override | scene、shot、take、状态、开始/结束时间、人工备注。 |
| LLM 状态 | 本地循环状态 | pipeline stage、queue length、error、last update、关联 take id。 |
| LLM 反馈 | 常量数组 | kind、text、severity、source、line/take reference。 |
| memo | 输入框不保存 | text/audio、timestamp、take id、author/source。 |
| observer count | 固定 `3` | session id、observer count、连接状态。 |
| 导出 | 无逻辑 | export format、scope、输出路径或下载 URL。 |

## 当前限制

- 所有业务数据都是 mock 常量或本地 state，刷新页面会丢失。
- 顶部导入、导出、设置、Next take、Delete last、语音 memo 尚未绑定真实业务逻辑。
- REC 只是切换按钮显示与本地计时，不控制真实录音；计时不持久化，刷新归零。
- 底部状态日志是 `debug log` 占位，未对接 ASR / LLM / 电平 / observer 真实指标。
- `/view` 只是占位页，尚未实现共享视图。

## 建议下一步

1. 先确定四个前后端契约：ASR 输出、take 信号、LLM feedback schema、SQLite take 表结构。
2. 把 `HISTORY_TAKES` / `CURRENT_TAKE` / `LLM_FEEDBACK` 从常量迁到 mock API 或 store，便于替换真实后端。
3. 给 `BottomControlBar` 的主操作定义命令接口：start recording、stop recording、next take、delete last、mark status。
