# soxr GIL 释放 / 线程并行探针实验

日期：2026-05-20
分支：spike/audio-input-layer
目标：验证 Audio Input Layer 的「声道并行」设计是否成立 —— soxr 流式重采样器
（`soxr.ResampleStream`）原生计算时是否释放 GIL，多线程并行处理各声道能否拿到
真实的墙钟加速。

## 环境

- Python 3.14.5（Cactus venv，`/opt/homebrew/Cellar/cactus/1.14_1/libexec/venv`），
  标准 GIL 构建（`sys._is_gil_enabled()` 返回 `True`）
- soxr 1.1.0（`soxr-1.1.0-cp312-abi3` wheel，abi3 稳定 ABI，3.14 直接可用）
- numpy 2.4.4
- 机器：10 核 Apple Silicon

运行：

```bash
PY=/opt/homebrew/Cellar/cactus/1.14_1/libexec/venv/bin/python
$PY experiments/2026-05-20-soxr-gil-probe/benchmark.py
```

测试工况：48000 → 16000 Hz，200ms 块（9600 输入帧），float32，
用有状态的 `soxr.ResampleStream`（实时分块处理必须用流式重采样器，
不能对每块独立 one-shot，否则块边界有 artifact）。

## 核心结论

### 1. soxr 释放 GIL —— 但这不是重点

Part A 用一批固定的重采样工作量，顺序跑 vs T 线程跑：

| 方式 | 墙钟 | 加速比 |
|------|------|--------|
| 顺序（1 线程） | 4.13s | 1.00x（基线） |
| 2 线程 | 2.27s | 1.82x |
| 4 线程 | 1.90s | 2.17x |
| 8 线程 | 3.46s | 1.20x |

T=2 拿到 1.82x —— GIL 不释放不可能有这个数，所以 **soxr 原生计算时确实释放 GIL**。
加速比到不了理想的 2x/4x，是因为 `resample_chunk` 之间还有 Python 层的循环
（持 GIL）；线程越多，Python 部分争抢越凶，T=8 反而塌回 1.20x（10 核机上过度
订阅 + Python 层 GIL 争用）。

### 2. 真实工况下，并行反而更慢 —— 决定性结论

Part B 测真实工况（1-2 声道、单个 200ms 块）的绝对耗时：

| 工况 | 耗时 / 块 |
|------|-----------|
| 单声道，一个 200ms 块 | 22.3 微秒 |
| 双声道，顺序处理 | 45.7 微秒 |
| 双声道，线程池（pool=2） | 65.1 微秒（**慢 1.42 倍**） |

单声道重采样只要 22 微秒。双声道顺序做 46 微秒。用 `ThreadPoolExecutor` 并行做，
反而变成 65 微秒 —— 因为提交 2 个任务、`pool.map` 收集、future 处理这些线程调度
开销（几十微秒级）比重采样本身（22 微秒）还大。

46 微秒放在一个 200ms 的块预算里，占 0.02%。重采样的成本可以忽略不计。

## 对 Soundspeed 的影响

**Audio Input Layer 不要 ThreadPoolExecutor，声道按顺序循环处理。**

- soxr 释放 GIL 是真的，所以线程并行对**大批量**重采样有效（Part A）。但 Audio
  Input Layer 的真实工况是 1-2 声道、200ms 块，每声道 22 微秒。线程池的调度开销
  在这个量级上压倒收益 —— 并行只会更慢、更复杂。
- 设计改为：`ChannelProcessor` 仍是独立、无状态（除流式重采样器自身状态外）、
  可独立测试的模块化单元，但**各声道在一个顺序循环里依次跑**，不开线程池。
- 顺序循环对 N 声道扩展同样够用：N=8 也才约 180 微秒，相对 200ms 仍可忽略。
- 「soxr 是否释放 GIL」这个假设不再是设计的承重点，可以从风险清单里去掉。
- 如果将来单声道处理变重（譬如 ChannelProcessor 里加降噪、增益），并行才可能划算；
  到那时，模块化的 `ChannelProcessor` 让「顺序循环换成线程池」是一处局部改动。

## 待验证

- [x] soxr 在 Python 3.14 上可安装（abi3 wheel）
- [x] soxr `ResampleStream` 释放 GIL（Part A，T=2 → 1.82x）
- [x] 真实工况下并行 vs 顺序的绝对耗时（Part B，顺序胜）
- [ ] `ChannelProcessor` 接 int16 输入（设备路径）时 soxr 的精度表现（重采样选
      float32 内部精度还是 int16 内部，留待实现时定）
