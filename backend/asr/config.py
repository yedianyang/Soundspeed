"""ASR 配置（Phase 1：实时 whisper.cpp 转录）。"""
from __future__ import annotations

from dataclasses import dataclass

# 默认 ASR 模型：large-v3-turbo 的 q8_0 量化（~834MB）。turbo 是 large-v3 的蒸馏版，
# 解码层大幅精简（4 层 vs 32），GPU 上速度接近 medium 而精度更高。单一真相源——
# dataclass 默认与 entrypoint 的 env 兜底都引用它，避免「改一处漏一处」的默认值漂移。
# 切回量化 medium：SOUNDSPEED_ASR_MODEL=medium-q8_0；fp16 基线：medium。
DEFAULT_ASR_MODEL = "large-v3-turbo-q8_0"


@dataclass(frozen=True)
class ASRConfig:
    """whisper.cpp（pywhispercpp）运行参数。

    model_size: ggml 模型大小。默认 "large-v3-turbo-q8_0"（large-v3 蒸馏版量化，精度高
      且 GPU 上接近实时）；可切 "medium-q8_0" / "medium"（fp16 基线）/ "small"。
    language: 转录语言，中文固定 "zh"。
    n_threads: whisper.cpp CPU 线程数（GPU 推理时影响较小）。
    models_dir: 模型文件存放目录；None 时用 pywhispercpp 默认缓存路径。

    --- whisper 解码调参（默认 = 库默认 = 当前生产行为，不擅自改；调优需真实录音量 CER）---
    beam_size: 束搜索宽度。默认 0/1 = 贪心（与当前生产一致）。>1 才启用 beam search。
      注意：pywhispercpp 的采样策略枚举在 Model() 构造时钉死，只设 beam_size 经 transcribe
      传是空操作；故 beam_size>1 时由 _ensure_model 用 params_sampling_strategy=1 构造模型。
      实测（CV zh-CN 200 条）turbo 上 beam5 相对贪心 CER 仅降 ~1%，且 L2 下游已对剧本纠错，
      默认不开；留作 opt-in，等真实同期录音再量值决定。
    temperature: 初始解码温度。0.0 = 确定性起点，配合 temperature_inc 失败时递增回退。
    temperature_inc: 解码失败（触发下方阈值）时的温度递增步长。0 关闭回退；保留默认 0.2
      让卡住的段落有退路。注意 entropy/logprob 阈值是回退触发器，三者耦合，别只调一个。
    entropy_thold: 压缩比/熵阈值（≈OpenAI compression_ratio_threshold）。超阈视为重复/退化，
      触发温度回退。默认 2.4；降到 1.8-2.0 更敏感抓重复，但会更频繁触发回退。
    logprob_thold: 平均对数概率阈值。低于此视为低置信，触发回退。默认 -1.0。
    no_speech_thold: 无语音概率阈值，超过则判定该段为静音。默认 0.6。
    initial_prompt: 解码前置提示词，偏置输出风格/用词。默认 None（不偏置）。中文可设
      "以下是普通话的句子，使用简体中文输出。" 强制简体、压繁体；也可塞场记术语热词。
    注：whisper.cpp 内置 vad 不在此暴露——上游 backend/vad/ 已切段，开内置 vad 会双重咬字。
    """

    model_size: str = DEFAULT_ASR_MODEL
    language: str = "zh"
    n_threads: int = 4
    models_dir: str | None = None
    beam_size: int = 0
    temperature: float = 0.0
    temperature_inc: float = 0.2
    entropy_thold: float = 2.4
    logprob_thold: float = -1.0
    no_speech_thold: float = 0.6
    initial_prompt: str | None = None
