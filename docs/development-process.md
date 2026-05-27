# Soundspeed 项目协作流程

版本：v1.0
日期：2026-05-27
状态：定稿

本文档定义 Soundspeed 项目的开发流程、Ticket 生命周期、Agent 委派协作、Spec 评审、TDD 节奏、分支与提交规范。所有团队成员（境熙、经纬、以及 AI agent）必须遵守。

---

# 1. 项目概要

Soundspeed 是面向电影同期录音部门的本地离线 AI 场记助手。比赛项目，两人小队，技术报告要靠代码和数据撑着。

**当前状态：骨架阶段。** 所有目录只有 `.gitkeep`，还没有实际代码。下面的命令和 `backend/` 包路径是约定与意图，等 `backend/` 真正初始化时落定。

---

# 2. 四个模块契约

后端是一条本地离线运行的音频处理流水线：

```
音频采集 → Cactus ASR → take 边界检测 → Gemma agent → SQLite
                                            ↓
                            前端：实时转录显示 + 场记单 UI
```

- 后端 Python；前端形式待定（大概率浏览器页面），前端相关约定暂缺。
- 模型权重不进 git，靠 `models/` 下的下载脚本获取，脚本里钉死版本。
- 录音 / 隐私数据不进 git；SQLite 运行库（`*.db`）不进，但 schema 和迁移脚本是源码、进 git。
- 密钥写 `.env`，仓库只留 `.env.example`。

### 四个模块契约（并行开发前先钉死）

并行开发前，这四个模块边界必须先由 Lead 协调、在 `docs/` 里写定：

1. **ASR 输出格式** — Cactus ASR 产出什么结构（backend-asr 产出）
2. **take 信号** — take 边界检测产出什么（backend-asr 产出）
3. **Gemma tool schema** — agent 能调哪些工具（backend-agent 负责）
4. **SQLite 表结构** — 数据怎么存（backend-agent 负责）

改动任一契约 = 同步更新 `docs/` + 通知下游 agent。

---

# 3. 技术栈与命令

后端 Python。工具链：pytest（测试）、ruff（lint）、mypy（类型检查）。测试放 `backend/tests/`，夹具放 `backend/tests/fixtures/`。

```bash
# 任务范围测试（开发 / commit 期默认）
pytest backend/tests/test_<模块>.py
pytest -k <关键字>

# 全量测试 + lint + 类型检查
pytest
ruff check backend/
mypy backend/
```

前端技术栈未定，命令待补。

---

# 4. 代码归位（四分类）

新建任何脚本前先对号入座。

| 类型 | 放哪 | 进 git |
|------|------|:------:|
| 生产代码（会被后端 import） | `backend/` | 是 |
| 可复用工具（会再跑，如下载模型、数据预处理） | `scripts/` | 是 |
| 一次性调研实验（如 ASR 框架 benchmark） | `experiments/<YYYY-MM-DD-名字>/` | 否（整体忽略） |
| 纯试错（无可引用产出） | `spike/` 分支，不合并 | 否 |

- `experiments/` 整体不进 git：`.gitignore` 用 `experiments/*` + `!experiments/.gitkeep` 排除全部实验内容——脚本、模型、音频、结论 README 都留本地。实验产物体积大（一个 benchmark 目录就上 GB），不入库。**因此实验结论必须另外留痕到 Notion 或 `docs/`**——git 不保存实验内容，结论只在一个人机器上，技术报告就接不上。
- 别从 `experiments/` import 到 `backend/`。`scripts/` 只留可复用工具，别混入一次性 benchmark。

完整目录说明见 `docs/structure.md`。

---

# 5. Ticket 完整流程

每个 ticket 走下面七步，按顺序、不跳步。中量与大变更（新模块 / 跨模块 / 改契约）必须走完整流程；单 bug fix / 文案 / 单文件小改可省 spec 与 plan，但不能省 review。

1. **spec**：负责 agent 写到 `docs/specs/YYYY-MM-DD-<名字>.md`，Lead 评审、用户审批后定稿。详见「Spec 与评审」节。
2. **plan**：spec 批后，负责 agent 出实现 plan（用 Plan subagent 或在 ticket 内列子任务），明确文件改动范围、依赖、测试入口。Lead 复核 plan 是否覆盖 spec 边界、是否有遗漏的契约消费方。
3. **TDD 红**：写测试 + 最小骨架，pytest 跑红，commit 一次。message 用 `test:` 或 `test(模块):` 前缀。
4. **TDD 绿**：写最小实现让测试通过，pytest 全绿 + ruff + mypy 全过，commit 一次。message 用 `feat:` / `fix:` 等前缀。
5. **codex review**：负责 agent 跑 `/codex:review`（或等价自动化 review），把意见整理回报 Lead。review 报 P0 / P1 必须修；P2 / P3 由 Lead 决定取舍。修复改动跟随到「simplify」节点。
6. **lead review**：Lead 读绿 commit 与 codex review 输出，做实现级 review。关注：spec 行为覆盖度、TDD 节奏（测试 commit 在前）、跨模块契约一致性、跨平台兼容、是否引入未明说的依赖。Lead 给「接受 / 修改 / 拒绝」+ 理由。
7. **simplify**：codex + lead 两轮 review 意见合并后，做行为不变的精简与重构。跑 pytest 仍绿 + ruff + mypy 全过，commit 一次。message 用 `refactor:` 前缀。如无可精简，跳过本步并在回报中显式说明。

完成后：ticket 状态切 Test，等 quality agent 跑全量 pytest + code review 通过后切 Done、合并到 main。

---

# 6. 开发流程：TDD

**每一个功能、每一个 bug fix 都必须走红-绿两步。跳过写测试这一步是严格禁止的。**

TDD 三步：

1. **红 —— 先写测试**：写一个描述期望行为的测试，运行，确认它**失败**（红）。测试描述行为，不描述实现。如果测试直接通过，说明测试写错了或功能已存在。
2. **绿 —— 写最小实现**：只写让测试通过所需的代码，不多写。运行，确认**通过**（绿）。
3. ~~精简~~ 已迁移到「Ticket 完整流程」第 7 步「simplify」，在两轮 review 之后做。原 TDD 三步去掉精简，红 → 绿 后直接进 codex review 阶段。

然后：跑任务范围的测试子集确认全绿 → commit 绿（TDD 节奏完成，进入「Ticket 完整流程」第 5 步 codex review）。

TDD 铁律：
- 先有失败的测试，才有实现代码。没有例外。
- 测试 commit 必须在实现 commit 之前。
- 开发者不得把写测试外包给 quality；quality 不补写别人的单元测试。
- quality 做 code review 时检查：commit 顺序（测试在前）+ 行为覆盖率。
- 测试命名对应所测行为，如 `test_asr_output_emits_timestamps`。

---

# 7. Spec 与评审

| 变更类型 | 流程 |
|---|---|
| 单 bug fix / 文案 | TDD 红 → 绿 → codex review → lead review →（必要时 simplify）。跳过 spec 与 plan。 |
| 单模块小功能 | TDD 红 → 绿 → codex review → lead review →（必要时 simplify），可选简短 spec。跳过 plan。 |
| 新模块 / 跨模块 / 改契约 | 完整流程：spec → plan → TDD 红 → 绿 → codex review → lead review → simplify。 |

新模块或跨模块功能：
1. 负责 agent 写简短 spec 到 `docs/specs/YYYY-MM-DD-<名字>.md`
2. Lead 评审：技术风险、漏掉的边界、契约缺口、未明说的假设
3. Lead 把评审意见整合进 spec，逐条标「接受 / 拒绝 / 修改」+ 一句理由，升版本号
4. 提交用户审批 → 批准后才开 plan、才开发

Spec 是原件，代码是复印件。不一致时改代码去匹配 spec，除非正式修订 spec。设计决策记录到 `docs/decisions/`。

---

# 8. Agent 委派协作

本项目使用 Claude Code 的 `Agent` 工具进行多 agent 协作。子代理是委派模式：主 session（Lead）spawn 子代理、分配任务，子代理完成后将结果回报给主 session。子代理之间**不能直接通讯**。

**前置条件**：Claude Code 默认已支持 `Agent` 工具，通过 `subagent_type` 参数选择子代理类型。除内置的 `general-purpose`/`Explore`/`Plan` 外，本项目还配置了 `backend-asr`/`backend-agent`/`quality`/`docs` 四个专用子代理。

**开团流程**：

1. Lead（主 session）分析任务，识别可并行的子任务
2. 使用 `Agent` 工具 spawn 子代理，在 `prompt` 中写明完整上下文（子代理不继承主 session 上下文）
3. 对于耗时任务，设 `run_in_background=true`，主 session 继续其他工作
4. 子代理完成后，Lead 整合结果，决定下一步

### 角色与文件 ownership

| 角色 | 文件 ownership | 职责 |
|---|---|---|
| **Lead**（主 session） | 不碰源码 | 建队、拆任务、分配、审批 plan、评审整合 |
| **backend-asr**（`Agent` spawn） | `backend/` 音频/ASR/take 子包、对应 `experiments/` | 音频采集、Cactus ASR、take 边界检测 |
| **backend-agent**（`Agent` spawn） | `backend/` Gemma/数据子包、SQLite schema | Gemma agent、tool schema、SQLite 访问 |
| **quality**（`Agent` spawn） | `backend/tests/**` | 契约测试、集成测试、code review |
| **docs**（`Agent` spawn） | `docs/**`、`README.md` | 接口契约文档、技术报告、调研结论留痕 |

两个子代理不要碰同一文件——按上表分清 ownership。`frontend` 角色待前端技术栈确定后补充；`backend/` 子包路径现在临时，等 `backend/` 初始化时定死。

### 子代理类型选择

- **`general-purpose`**：通用软件工程任务（读文件、改代码、跑命令）。没有更合适的专用 agent 时用这个。
- **`Explore`**：只读代码库探索。需要搜索大量文件、理解模块时使用。可并发启动多个 Explore agent 调查独立问题。
- **`Plan`**：只读实现规划和架构设计。进入 plan mode 前调研使用。
- **`backend-asr` / `backend-agent` / `quality` / `docs`**：本项目自定义的专用子代理，对应「角色与文件 ownership」表中的分工，任务命中其职责时优先用它们。

### 协作机制

- **任务分配**：Lead 在 `Agent` 的 `prompt` 中写明任务、相关文件路径、已知信息、期望输出格式。子代理不继承主 session 上下文，prompt 必须自包含。
- **Plan 审批**：跨 2+ 模块 / 改契约 / 新依赖 / 新模块的任务，Lead 先让子代理在 plan mode 出方案，Lead 批准后才动手。单文件 fix、单模块小功能不需要。
- **[BLOCKED]**：子代理遇到「契约要求但技术做不到」→ 立即停止编码，在最终回报中给 Lead 标 `[BLOCKED]`，由 Lead 决定改契约 / 替代方案 / 升级给用户。

### 并行与串行

- **可并行**：backend-asr + backend-agent（四个契约钉死之后）、docs 调研。用多个 `Agent` 并发 spawn。
- **必须串行**：契约定义 → 实现；quality 等功能完成后才测；docs 等测试通过后才更新文档

---

# 9. 任务管理

- **session 内**：用 `TaskCreate` 拆解和跟踪当前 session 的工作（`TaskUpdate` 更新状态、`TaskGet` / `TaskList` 查询）。task 完成 + 验证通过 → 立即标 completed 并 commit，不批量积攒。
- **跨 session**：用 GitHub Issues 当待办池。Lead 在 session 启动时读 open issues，session 内拆成 task。开发中发现新 bug → 新开 issue。
- 任务标题与 commit 一样带前缀：`feat / fix / spike / docs / chore`。

---

# 10. 验证标准

commit 前必须跑「技术栈与命令」里的 `pytest`（任务范围）、`ruff check`、`mypy`，全过才能 commit。**不通过不准 commit。**

按「Ticket 完整流程」commit 时机：

- 红 commit（步骤 3）：测试已写、实现还是 NotImplementedError。pytest 应红、ruff + mypy 全过（语法层）即可 commit。
- 绿 commit（步骤 4）：pytest + ruff + mypy 全过。
- simplify commit（步骤 7）：pytest + ruff + mypy 全过、行为不变。

何时跑全量 `pytest`：session 启动基线、跨模块 / 架构变更、开 PR 前、用户明确要求。其余时候只跑任务范围子集。

UI 布局 / 实时转录显示行为 / 音频采集行为类变更，commit message 末尾标 `[手动测试]`，并在对应 GitHub Issue 写人工验证步骤。

---

# 11. 分支与提交

- `main` 永远可跑、可演示，不直接在上面改。
- 功能拉独立分支：`<type>/<描述>`，type ∈ `feat / fix / spike / docs / chore`。
- commit 信息带前缀（`feat: ` / `fix: ` / …），小步提交，方便回溯和写技术报告。
- PR base 为 `main`，控制在 10 分钟能看完，squash and merge。
- 里程碑打 tag：`mvp` / `p2` / `p3`。

完整工作流见 `CONTRIBUTING.md`。

---

# 12. 沟通规范

- **不用 emoji**：回答、commit、文档、代码注释一律不用。人工验证标记用 ASCII `[手动测试]`。
- **不自问自答**：陈述事实即可，别在回答里编个问题再自己答。用户问什么答什么。
- **保证可懂度**：用完整句子，避免无解释的缩写和黑话，确保读者冷启动也能读懂。
