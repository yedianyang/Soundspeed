"""ASR 配置（Phase 1：实时 whisper.cpp 转录）。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ASRConfig:
    """whisper.cpp（pywhispercpp）运行参数。

    model_size: ggml 模型大小。"medium" 为默认，中文精度较好；
      可选 "small" / "large"，GPU 上 medium 已超实时。
    language: 转录语言，中文固定 "zh"。
    n_threads: whisper.cpp CPU 线程数（GPU 推理时影响较小）。
    models_dir: 模型文件存放目录；None 时用 pywhispercpp 默认缓存路径。
    """

    model_size: str = "medium"
    language: str = "zh"
    n_threads: int = 4
    models_dir: str | None = None
