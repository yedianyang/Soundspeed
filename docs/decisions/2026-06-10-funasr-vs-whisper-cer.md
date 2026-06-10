# FunASR vs whisper.cpp 中文 CER 实测对照（Common Voice zh-CN）

日期：2026-06-10
分支：feat/asr-whisper-tuning
状态：实测留痕（实验未进 git，结论存此）

## 一句话结论

同一批真实中文音频上，**FunASR（paraformer-zh）字错率比当前生产的 whisper（large-v3-turbo-q8）低约 36%**（8.65% vs 13.57%）。但这是干净朗读语料代理，不等于带噪多说话人现场；是否换/加 FunASR 需真实同期录音再校准。

## 实测条件

- 数据：`fsicoli/common_voice_17_0` 的 zh-CN test 前 200 条（官方 mozilla 仓已掏空，用社区 parquet 镜像，需 `trust_remote_code=True`），总时长 22.3 分钟，干净单人朗读。固定存盘 `clips_200.npz` 保证两边同一批。
- 归一化（两边一致）：opencc t2s 转简 + 去标点空格，jiwer 字级 CER。
- whisper：large-v3-turbo-q8_0（GGML，Metal GPU），走生产 `WhisperRunner.transcribe_pcm`（int16 路径）。
- FunASR：funasr 1.3.9，独立 venv，paraformer-zh / seaco-paraformer / ct-punc，mps。只取主 ASR 模型，**不挂内置 vad**（避免与上游 silero 双重切段）。

## CER 总表（越低越好，N=200）

| 配置 | CER | Δ 贪心 | 备注 |
|---|---|---|---|
| whisper turbo-q8 贪心（当前生产） | 0.1357 | — | int16 生产路径 |
| whisper turbo-q8 beam5 | 0.1349 | -0.6% | beam 正确启用后仍几乎无用 |
| FunASR paraformer-zh | 0.0865 | **-36.3%** | |
| FunASR seaco + ct-punc（无热词） | 0.0865 | -36.3% | 标点不改 CER（被归一化去掉） |
| FunASR seaco + 热词上限（答案当热词） | 0.0847 | -37.6% | 不现实，仅天花板 |

补：whisper 经 `run_cer.py` 的 float32 直喂路径（非 int16）= 0.1321；与 0.1357 的差是 int16 量化，两个都对。

## 抽样对比（FunASR 修对了 whisper 的错）

| 正确 | whisper | FunASR |
|---|---|---|
| 正巧母亲往外探头 | **郑乔**母亲往外探头 | 正巧母亲往外探头 ✓ |
| 怕**风**…**身**疼痛…咳**嗽** | 怕**疯**…**深**疼痛…咳**嗣** | 全对 ✓ |
| 阿肯**色**州 | 阿肯**斯**州 | 阿肯色州 ✓ |
| 科斯捷维奇…**博士** | 科斯杰为其…**模式** | 科斯杰维奇…博士（无热词）→ 科斯**捷**维奇（喂热词修对） |

## 三个诚实边界

1. **代理集，非现场**：Common Voice 是干净单人朗读，跟带噪、多说话人、现场口音的同期录音不是一个分布。这个 CER 可复现、能比出差异，但不等于现场表现。FunASR 在朗读上赢 36% 不保证现场也赢这么多。
2. **数字写法占一小部分**：CV 答案写「十七年」，whisper 爱写「17年」，归一化不折算数字 → whisper 被算错，FunASR 输出汉字数字正好对上。但大头是真识别赢（同音字/专有名词），不是纯写法。
3. **热词是定点工具不是通用杠杆**：连「把答案当热词」的作弊上限也只多降 1.3%。CV 每句专名都不同、无固定词表，热词使不上劲。你们场景有固定演员名/术语表才是它主场（探针实证：喂「科斯捷维奇」把 杰→捷 修对）。

## FunASR 参数与内置模块（introspect funasr 1.3.9 源码）

关键区别：**paraformer 是非自回归（NAR），没有 whisper 的 beam_size/temperature/no_speech_thold 解码旋钮**。可调面是管线组合 + 识别模块：

- 管线组合：`vad_model` / `punc_model` / `spk_model`（挂不挂 VAD/标点/说话人，独立开关）
- 热词：`hotword`（seaco / contextual_paraformer，语义上下文偏置）
- 流式 2pass：`chunk_size=[0,10,5]` / `encoder_chunk_look_back` / `decoder_chunk_look_back`
- VAD 分段：`merge_length_s` / `vad_kwargs`
- 吞吐/设备：`batch_size_s` / `device`(cpu/cuda/mps) / `ncpu` / `ngpu`
- 说话人：`preset_spk_num` / `return_spk_res` / `spk_mode`
- 输出：`sentence_timestamp` / `return_raw_text`

内置模块（all-in-one，不只声学 ASR）：ASR（paraformer 多变体 / sense_voice / 内置 whisper）、VAD（fsmn_vad）、标点（ct_transformer）、说话人（campplus）、关键词唤醒（fsmn_kws），均 streaming 变体齐全。

## 集成与选型提醒

- 接 FunASR 需**独立 venv**：funasr 拖约 80 个包（transformers 5.10 + 阿里云 OSS SDK，含 crcmod 现场编 C），不能污染生产共享 .venv。
- paraformer-zh 是 Apache-2.0；numpy float32 16k 单声道直喂（与 SpeechSegment 链路对得上）。
- **别开 FunASR 内置 fsmn_vad**：上游 silero 已切段，双重 VAD 会咬字。
- 速度：FunASR 929 ms/条 vs whisper 501 ms/条（这批、mps vs Metal），FunASR 慢一点。
- A/B 不足以单独支撑「换栈」：朗读代理赢输都要配真实同期录音才有决策力。

## 复现

实验目录 `experiments/2026-06-09-whisper-cer/`（不进 git）：
- `clips_200.npz` 固定测试集；`run_cer.py` whisper 多配置；`whisper_beam5.py`；`funasr_run.py`（paraformer-zh）；`probe_funasr.py`（标点/热词探针）；`funasr_punc_hw.py`（seaco+标点+热词上限）；`ab_cer.py` / `ab2_cer.py` 汇总。
- 独立 venv：`experiments/2026-06-09-whisper-cer/funasr-venv`（funasr 1.3.9 / torch 2.12.0）。
- 模型：whisper `large-v3-turbo-q8_0`；FunASR `paraformer-zh` / `iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch` / `ct-punc`。
