# Soundspeed

**面向电影同期录音部门的本地离线 AI 场记助手。**

用一个 **Gemma 4 E4B** 同时承担文本理解、剧本照片识别与语音查询，配合贯穿全栈的原生函数调用，在一台本地设备上完成「录音 → 理解 → 结构化场记」的全离线闭环：实时转写、实录↔剧本逐行对照、说话人分离、口头/语音备注归档、自然语言查询、剧本照片增量更新、场记单导出。

> 技术选型与架构细节见 [`TECHNICAL_REPORT.md`](TECHNICAL_REPORT.md)。

---

## 环境要求

| 组件 | 版本 / 说明 |
| --- | --- |
| Python | **3.12+** |
| Node.js | **≥ 20.19**（推荐 22 LTS），含 npm |
| 操作系统 | macOS（Apple Silicon，推荐）/ Windows 10+；纯 CPU 亦可运行（较慢） |
| 磁盘 | 预留 **约 8–10 GB** 给模型权重（首次运行自动下载） |
| 网络 | 仅**首次**下载模型权重需要联网；之后全程离线运行 |
| HuggingFace token | **可选**，仅说话人分离（pyannote）需要；不配则自动跳过分离，其余功能不受影响 |

> macOS 上若 `llama-cpp-python` / `pywhispercpp` 需要本地编译，请先装 Xcode Command Line Tools：`xcode-select --install`（多数情况直接装 PyPI 预编译 wheel，无需此步）。

---

## 快速开始

### 1. 获取代码

```sh
git clone https://github.com/yedianyang/Soundspeed.git
cd Soundspeed
```

### 2. 后端：创建虚拟环境并安装依赖

```sh
python3.12 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\Activate.ps1        # Windows PowerShell

pip install -r requirements.txt
```

**平台加速（重要）：**

- **macOS（Apple Silicon）**：`llama-cpp-python` / `pywhispercpp` 的 PyPI wheel 已自带 Metal，torch 自动取 CPU/MPS wheel —— 上一步即已就绪，**无需额外操作**。
- **Windows / Linux + NVIDIA GPU**：默认装的是 CPU 版。要启用 CUDA 加速，再运行：

  ```sh
  python scripts/install_accel.py
  ```

  （它会按显卡自动装对应的 CUDA wheel；细节见 [`docs/2026-06-02-windows-llama-cpp-runbook.md`](docs/2026-06-02-windows-llama-cpp-runbook.md)）
- **纯 CPU**：无需额外操作，直接可用，仅推理较慢。

### 3. 后端：启动服务

服务通过**环境变量**配置（注意：`.env` 文件不会被自动加载，需在 shell 中导出，或用你习惯的 dotenv 工具加载）。

最省事的本地启动（开发模式，使用固定 token `devtoken` 并自动播种一个演示场次）：

```sh
# macOS / Linux
SOUNDSPEED_DEV=1 python -m backend.api

# Windows PowerShell
$env:SOUNDSPEED_DEV = "1"; python -m backend.api
```

或自定义管理员 token：

```sh
# macOS / Linux
ADMIN_TOKEN=your-secret python -m backend.api
# Windows PowerShell
$env:ADMIN_TOKEN = "your-secret"; python -m backend.api
```

> 若都不设置，服务会随机生成一个 token 并打印到控制台——记下它，下一步要用。

启动后：

- 服务监听 `http://127.0.0.1:8000`（健康检查 `GET /healthz`）。
- **首次启动会自动从 HuggingFace 下载模型权重**（Gemma 4 E4B GGUF + 多模态投影器，共数 GB；以及 Whisper 模型）。下载期间前端会显示 `downloading / loading` 状态。
- 启用说话人分离（可选）：先在 HuggingFace 接受 pyannote 模型条款并拿到 token，再带上 `SOUNDSPEED_HF_TOKEN=hf_xxx` 启动。

### 4. 前端：启动 Web 界面

另开一个终端：

```sh
cd frontend
npm install
npm run dev
```

打开 `http://localhost:5173/admin`，进入「设置」对话框，把上一步的管理员 token（开发模式即 `devtoken`）填进去保存即可开始使用。

---

## 常用环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ADMIN_TOKEN` | 随机生成 | REST/WS 鉴权 token（前端「设置」需填同一个） |
| `SOUNDSPEED_DEV` | — | `=1` 使用固定 token `devtoken` + 挂载调试端点 + 空库时播种演示场 |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | 监听地址/端口 |
| `SOUNDSPEED_DB` | `./data/soundspeed.db` | SQLite 数据库路径（自动创建） |
| `SOUNDSPEED_PROFILE` | — | 运行档位：`import`（Gemma 占算力做解析/视觉）/ `record`（让算力给 ASR、Gemma 退 CPU） |
| `SOUNDSPEED_HF_TOKEN` | — | pyannote 说话人分离所需；不配则跳过分离 |
| `SOUNDSPEED_LIVE_ASR` | 启用 | `=0` 关闭实时转写 |
| `SOUNDSPEED_DIARIZATION` | 启用 | `=0` 关闭说话人分离 |
| `SOUNDSPEED_ASR_MODEL` | `medium-q8_0` | Whisper 模型大小 |
| `SOUNDSPEED_AUDIO_DEVICE` | 首个可用 | 指定输入设备索引或名称 |

完整列表见 [`backend/api/__main__.py`](backend/api/__main__.py) 顶部 docstring。

---

## 验证安装（可选）

跑后端测试套件确认环境就绪：

```sh
pip install pytest pytest-asyncio httpx        # 测试依赖
python -m pytest backend/tests -q
```

核心逻辑用 `StubClient` 替身覆盖，无需 GPU/真实权重即可跑；预期约 **1000+ 用例通过**。

---

## 项目结构

```text
Soundspeed/
├── backend/            FastAPI 后端
│   ├── api/            REST + WebSocket 路由、启动入口（python -m backend.api）
│   ├── core/           事件编排 orchestrator / session / events
│   ├── llm/            Gemma 4 客户端、多模态 handler、LLMService、工具 schema
│   ├── pipelines/      L2(实录↔剧本) / NP(备注) / QP(查询) / SP(剧本解析) / 语音调度
│   ├── asr/            Whisper 实时转写
│   ├── diarization/    Pyannote 说话人分离与回填
│   ├── db/             DAL / schema / migrations
│   └── tests/          约 1000+ 用例
├── frontend/           React + Vite + TypeScript（npm run dev → :5173）
├── scripts/            install_accel.py（GPU 加速兜底）/ detect_device.py 等
├── docs/               架构与设计文档、技术报告、平台 runbook
├── requirements.txt    后端运行依赖（pip）
└── pyproject.toml      项目元信息与依赖（uv / pip 同源）
```

---

## 故障排查

- **首次启动很慢 / 前端一直 `downloading`**：在下载模型权重（数 GB），耐心等待；确认能访问 HuggingFace。
- **说话人分离没生效**：需 `SOUNDSPEED_HF_TOKEN` 且已在 HuggingFace 接受 pyannote 模型条款；否则会自动跳过（其余功能正常）。
- **Windows/Linux 上 Gemma 跑在 CPU、很慢**：运行 `python scripts/install_accel.py` 装 CUDA 版，参见 [`docs/2026-06-02-windows-llama-cpp-runbook.md`](docs/2026-06-02-windows-llama-cpp-runbook.md)。
- **8GB 显存设备录制时卡顿**：用 `SOUNDSPEED_PROFILE=record` 启动，把显存让给 Whisper/Pyannote。
- **前端调不通后端（401）**：确认前端「设置」里填的 token 与后端启动时一致。

---

## 许可

Apache-2.0
