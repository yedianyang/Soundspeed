# Spec: LLM 推理后端选型（0.C spike）

版本：v0.3
日期：2026-05-27
状态：spike 执行完成，Lead 评审通过待 commit

变更记录：
- v0.3（2026-05-27）：补 Q4_K_M vs Q6_K 对照 + 16 GB Mac mini 内存预算；锁定量化档为 Q4_K_M。结论见 §11.2。Lead 评审修订：RTF 换算说明（§5）、Windows 措辞（§7）。
- v0.2（2026-05-27）：spike 执行完毕，macOS M1 Max + Q4_K_M 4 项红线全过。结论见 §11，原始数据 `experiments/2026-05-27-gemma-backend-bench/README.md`。
- v0.1（2026-05-27）：初稿。聚焦 llama-cpp-python 单方案验证，mlx-vlm 排除（理由见 §3），Ollama 列对照备选。

依赖 spec：
- llm-service-design v1.0（`docs/specs/2026-05-25-llm-service-design.md`）开放问题 1
- development-plan v0.2（`docs/specs/2026-05-27-development-plan.md`）§2 跨平台、§5
- system-architecture v0.1（`docs/specs/2026-05-26-system-architecture.md`）

对接 Notion ticket：0.C spike: llm — Gemma 推理后端选型（Owner 境熙，P0）

---

## 1. 背景

`llm-service-design` v1.0 定了 LLMService 单例 + PriorityQueue + asyncio.Lock 架构，但「Gemma 4 E4B 推理后端用哪个」留作开放问题 1。0.D 升 spec 到 v1.1 之前，必须先把后端选型钉下来。

候选三个：

| 方案 | 形态 | 跨平台 | 多模态 |
|---|---|---|---|
| llama-cpp-python | Python 绑定，GGUF + Metal/CUDA | macOS + Windows + Linux | Gemma 4 GGUF 多模态需额外 mmproj 文件 |
| Ollama | 独立 daemon + HTTP 接口 | macOS + Windows + Linux | 同上 |
| mlx-lm / mlx-vlm | Apple MLX，仅 Apple Silicon | macOS only | mlx-vlm 原生支持 |

本 spec 与 spike 聚焦 **llama-cpp-python** 单方案验证。

## 2. 目标

回答 llm-service-design v1.0 开放问题 1：Gemma 4 E4B 推理后端选 llama-cpp-python 是否可行？

可行的判据是 §5 的 4 项验收指标都达到 §6 的红线。

## 3. 范围

**In scope**：
- llama-cpp-python 在 macOS（M1 Max）上跑通 Gemma 4 E4B Q4_K_M 推理
- 测量加载时间、RTF（实时率）、内存峰值、prefill/decode tps
- 验证 `asyncio.to_thread(model.generate, prompt)` 串行化方案可工作
- 文档化 6 个 task_type（l1/l2/sp/np/qp/agent）的 prompt 长度与生成参数对推理时延的影响
- Windows 平台留 TODO，spec 与 README 显式标注本轮未验证

**Out of scope**：
- **mlx-vlm**：用户明确排除。理由：mlx-vlm 仅 Apple Silicon，跨平台兼容直接违反 development-plan v0.2 §2 跨平台一致性约束（任一平台不通则方案否决）。即使 macOS 上更快，Windows 缺位就不达验收。
- **Ollama**：本轮不主动跑。Notion ticket 把 Ollama 定为「超时降级兜底」，触发条件是 llama-cpp-python 不达红线。如果 llama-cpp-python 在 §6 红线内，Ollama 验证延后到 LLMService 实际落地时再做。
- **mlx-lm**（纯文本 MLX 后端）：同 mlx-vlm 跨平台不达标排除。
- 函数调用 / tool_use 集成：留给 1.F LLMService 实现 ticket。
- L1 Pipeline 是否真用 LLM：留给 llm-service-design 开放问题 2，本 spike 仅给数据。

## 4. 测试环境

### 硬件
- macOS：M1 Max（用户当前机器），统一内存 ≥32GB
- Windows：本轮不验证，spec 与实验 README 留 TODO

### 软件
- Python 3.11 或 3.12，spike 目录内独立 venv（不污染项目 3.14 venv）
- llama-cpp-python 最新稳定版，Metal 后端（`CMAKE_ARGS="-DGGML_METAL=on"`）
- 模型：Gemma 4 E4B GGUF，Q4_K_M 量化档起步（用户提供本地副本路径）
- 控制变量：n_ctx=4096，n_gpu_layers=-1（全卸载到 GPU），seed 固定

### 输入语料
- 6 个 task_type 的代表性 prompt 各一条，覆盖短 prompt 短输出（l1_clean ~150 token）到长 prompt 长输出（script_parse ~2048 token）。原文存 `experiments/2026-05-27-gemma-backend-bench/prompts/`。

## 5. 验收指标

Notion ticket 要求 4 项对比，本 spec 把指标具体化：

| 指标 | 定义 | 测量方法 | 目标 |
|---|---|---|---|
| **加载时间** | `Llama(...)` 构造到首次可推理的墙钟时间 | 进程冷启动 3 次取均值 | <10s |
| **RTF（实时率）** | decode 时间 / 实时秒数，对 l1_clean 任务 | 3 轮取均值，单独记录 prefill/decode tps | l1_clean RTF < 0.5（实时） |
| **多模态支持** | Gemma 4 多模态走 mmproj 是否能装、能跑 | 本轮跳过测试，仅文档化加载路径 | spec 标 P3 延后 |
| **asyncio.to_thread 集成难度** | `await asyncio.to_thread(llm.create_completion, ...)` 在并发 6 路调用下是否串行可控、不死锁 | mock 6 路 task 并发入队，验证锁与队列顺序 | 无死锁、串行执行、L1 可插队 |

附加测量（不进验收，留作技术报告素材）：
- 内存峰值（`psutil.Process().memory_info().rss`，模型加载后稳态 + 推理峰值）
- 长 prompt 退化：script_parse 2048 token prompt 的 prefill tps
- 量化档对比（可选）：Q4_K_M vs Q5_K_M 各一轮，看是否需要升档

**RTF 换算口径**：本 spec 里 RTF 不是标准音频 RTF（生成耗时 / 输入音频时长）。L1 的输入是 ASR 已转录的文本而非音频，无法直接拿到「实时秒数」。换算方式：把 `decoded_tokens / 3` 当作对应口播秒数（中文每秒约 3 字 ≈ 3 token），RTF = `decode_seconds / 口播秒数`。该口径仅用于 spike 内横向对比，技术报告引用须同时附换算说明。

## 6. 决策红线

llama-cpp-python 通过 = 同时满足：

1. 加载时间 < 10s
2. l1_clean RTF < 0.5（M1 Max 上）
3. asyncio.to_thread 串行方案无死锁，6 路并发下 L1 (P1) 能在 Agent (P3) 任意一轮之间插队
4. 内存峰值 < 8GB（给音频 + ASR + 系统留余地）

任一不达：spike 结论标「不通过」，触发 Ollama 备选验证（另起 spike 或顺接做）。

## 7. 跨平台策略

development-plan v0.2 §2 要求所有 spike 跨平台一致，但本轮用户明确只跑 macOS。妥协方案：

- macOS 跑完出结论后，把脚本与 README 留 Windows TODO。
- 0.D 升 llm-service-design 到 v1.1 时，结论标「macOS 验证通过，Windows pending」，不阻塞 1.F LLMService 实现起步。
- Windows 验证作为后续 ticket（建议 0.C.1）单独排，由有 Windows 机器的人接。
- llama-cpp-python 有 prebuilt wheel for Windows（CPU + CUDA），跨平台风险主要在 Metal 优化不可迁移上，Windows 走 CPU 或 CUDA 后端基线行为**预计一致，待 0.C.1 实测确认**。

## 8. 集成点

spike 结论同步到：
- `docs/specs/2026-05-25-llm-service-design.md`：升到 v1.1，开放问题 1 给答案，决策 2「串行推理锁」补充 llama-cpp-python 验证证据。
- Notion 1.F ticket：LLMService.client 选型确认 llama-cpp-python。
- `experiments/2026-05-27-gemma-backend-bench/README.md`：留数据原始记录（不入库，本地存档）。

## 9. 开放问题

1. **量化档**：Q4_K_M 是否够用？Gemma 4 E4B 在 Q4 下函数调用 / 结构化输出退化幅度未知，1.F LLMService 落地时若发现 schema 解析失败率高，回头考虑 Q5_K_M 或 Q6_K。
2. **多模态**：spec v1.1 是否需要在 0.D 阶段就支持视觉？拍照剧本流程在第二阶段才用，本轮先文档化加载路径，实测留给后续 ticket。
3. **n_ctx 上限**：4096 是否够覆盖 SP（script_parse）的剧本输入？长剧本可能要 8192+，context 越大内存越大，需在 §5 加测一组。
4. **Windows wheel**：llama-cpp-python Windows CUDA wheel 是否随版本及时更新，待 Windows 验证轮回答。

## 10. 验收

- [x] 实验目录 `experiments/2026-05-27-gemma-backend-bench/` 含 README、scripts、prompts、results
- [x] macOS 上跑通 Gemma 4 E4B Q4_K_M 推理，6 个 task_type 各 3 轮
- [x] 4 项指标全部测出并写入 README
- [x] asyncio.to_thread 并发测试脚本通过
- [ ] 选型推荐写回 `docs/specs/2026-05-25-llm-service-design.md` v1.1（属 0.D ticket，本 spike 不直接改）
- [x] Windows 验证留 TODO，理由文档化（§7）

## 11. 结论

### 11.1 红线对照

llama-cpp-python 0.3.23 prebuilt wheel（macOS arm64 自带 Metal + libmtmd），Gemma 4 E4B Q4_K_M：

| 指标 | 红线 | 实测 | 结果 |
|---|---|---|---|
| 加载时间 | < 10s | 0.87s | ✓ |
| l1_clean RTF | < 0.5 | 0.05 | ✓ |
| asyncio.to_thread 并发 | 无死锁、L1 可插队 | preemption 实证（agent round 1 等待 5.07s） | ✓ |
| 内存峰值 | < 8 GB | 5.3 GB | ✓ |

### 11.2 量化档选型（Q4_K_M vs Q6_K，目标 16 GB Mac mini）

| 量化档 | RSS（n_ctx=4096） | decode_tps（均值） | 16 GB Mac mini 余量 |
|---|---|---|---|
| Q4_K_M | 5.3 GB | 53.2 | ~4.7 GB（n_ctx 可扩到 16384） |
| Q6_K   | 7.3 GB | 49.0（−8%） | ~2.7 GB（n_ctx 8192 临界） |

肉眼对照 6 个 task_type 输出质量：Q6 在 l2_summarize 略胜（issues 更精炼），但在 agent_init 工具选择上反而 Q4 更稳；其他任务差异不显著（N=1，需 1.F 落地后跑评估集量化）。

**量化档锁定 Q4_K_M**：性能 +8%、内存省 2 GB、上下文扩展余地大、质量退化在肉眼对照下不可见。Q5_K_M 留作中间挡，待 1.F 真实负载下若 JSON schema 合规率退化再升档。Q6_K 不推荐。

16 GB Mac mini 预算（macOS 4 GB + 后端组件 ~2 GB + ASR small ~0.6 GB + 余量 0.5 GB ≈ 7 GB 占用 → LLM 预算约 10 GB），Q4_K_M 5.3 GB 含 KV cache 扩展余地。完整推导见 `experiments/2026-05-27-gemma-backend-bench/README.md`「16 GB Mac mini 内存预算」节。

### 11.3 选型最终结论

- 后端框架：`llama-cpp-python`，Metal 后端
- 量化档：`Q4_K_M`（unsloth/gemma-4-E4B-it-GGUF）
- 配置：`n_ctx=4096`（起步）、`n_gpu_layers=-1`、`seed=42`、Metal 全卸载

### 11.4 后续 ticket

- 0.C.1：Windows benchmark（CPU 或 CUDA wheel，复现 4 项红线 + Q4_K_M 验证）
- 0.D：升 `llm-service-design` 到 v1.1，链接本 spec，开放问题 1 给答案
- 1.F：LLMService 实现，量化档复审（评估集跑完看是否需要升 Q5_K_M）
