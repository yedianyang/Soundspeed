"""ASR 配置（Phase 1：实时 whisper.cpp 转录）。"""
from __future__ import annotations

from dataclasses import dataclass

# 默认 ASR 模型：medium 的 q8_0 量化（~770MB，约 medium fp16 一半的磁盘/RAM，
# 换 ~1% WER）。单一真相源——dataclass 默认与 entrypoint 的 env 兜底都引用它，
# 避免「改一处漏一处」的默认值漂移。切回 fp16：SOUNDSPEED_ASR_MODEL=medium。
DEFAULT_ASR_MODEL = "medium-q8_0"


@dataclass(frozen=True)
class ASRConfig:
    """whisper.cpp（pywhispercpp）运行参数。

    model_size: ggml 模型大小。默认 "medium-q8_0"（量化版，中文精度仍好，省一半内存）；
      可切 "medium"（fp16，精度基线）/ "small" / "large"，GPU 上 medium 已超实时。
    language: 转录语言，中文固定 "zh"。
    n_threads: whisper.cpp CPU 线程数（GPU 推理时影响较小）。
    models_dir: 模型文件存放目录；None 时用 pywhispercpp 默认缓存路径。
    """

    model_size: str = DEFAULT_ASR_MODEL
    language: str = "zh"
    n_threads: int = 4
    models_dir: str | None = None
