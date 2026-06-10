# whisper beam_size 经 transcribe 传是空操作 + turbo 上 beam 增益微弱

日期：2026-06-09
分支：feat/asr-whisper-tuning

## 结论

1. **pywhispercpp 的采样策略枚举在 `Model()` 构造时钉死**（`whisper_full_default_params(strategy)`，默认 `params_sampling_strategy=0`=GREEDY）。之后经 `transcribe(beam_search=...)` 传只写 `beam_search.beam_size` 字段、不切策略枚举，whisper.cpp 直接无视、跑贪心。**只设 beam_size 经 transcribe 传 = 空操作。** 要真启用 beam search 必须 `Model(params_sampling_strategy=1, beam_search={...})` 在构造时切。

2. **turbo 模型上 beam 增益微弱。** 正确启用后（strategy=1），large-v3-turbo-q8_0 上 beam5 相对贪心 CER 仅降约 1%，且有改有坏。符合「turbo 解码层只 4 层、beam 收益递减」的已知现象。

3. 据此默认回**贪心**（与当前生产一致），beam 作为正确接线的 **opt-in**（`beam_size>1` 时 `_ensure_model` 用 strategy=1 构造）。L2 下游已对剧本纠错，原始 CER 降 1% 价值有限；留 opt-in 等真实同期录音再量值决定。

## 实测数据（Common Voice zh-CN test，代理集）

CER 归一化：opencc 转简 + 去标点空格 + jiwer 字级 CER。模型 large-v3-turbo-q8_0。

| 配置 | CER | 备注 |
|---|---|---|
| baseline 贪心（当前生产） | 0.1321 | N=200 |
| beam5（经 transcribe，**空操作**） | 0.1321 | 与贪心逐条相同 → 证实空操作 |
| beam8（经 transcribe，**空操作**） | 0.1321 | 同上 |
| beam5+中文 initial_prompt | 0.1349 | +2.1%，**变差**（prompt attention zero-sum） |
| 贪心（strategy=0） | 0.1155 | N=40 子集 |
| beam5（**strategy=1，正确启用**） | 0.1141 | -1.2%，6/40 条不同、有改有坏 |

## 方法学坑（留给后人）

- **accepted-kwarg ≠ applied**：`transcribe(**params)` 接受未生效的 kwarg 不报错。单测 + 「不抛错」smoke 抓不到空操作；是 CER harness（输出逐条相同）抓出来的。验参数时要验「真改变了输出」，不只是「没崩」。
- per-call 实测生效的参数：temperature（0.0 vs 1.0 改 8/10 条）、initial_prompt（CER 变了）、阈值类同路径。
- Common Voice 是众包**朗读**，跟带噪多说话人同期录音不是一个分布；CER 是可复现代理，不等于现场。阈值类参数对噪声敏感、迁移性更弱。最优值仍需真实录音量。

## 复现

harness 在 `experiments/2026-06-09-whisper-cer/run_cer.py`（不进 git）。数据源 `fsicoli/common_voice_17_0`（官方 `mozilla-foundation/*` 仓已掏空，社区 parquet 镜像未 gated）。依赖 `datasets`/`jiwer`/`librosa` 装在 .venv（未进 pyproject）。
