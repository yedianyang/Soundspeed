"""StreamDriver：把音频源驱动成实时 ASR 事件。

AudioSource（DeviceSource 实时 / FileSource 批）→ 每声道 ChannelVADSegmenter 端点切段
→ 每个 SpeechSegment 经 WhisperRunner 转文字 → 组 AsrFinalPayload → publish 到 orchestrator
→（既有 WS 转发）前端实时上屏。

Phase 1：段级 final（turn 收尾出一条），speaker 一律 None（ch1 的 speaker 由 take.end
后批量 diarization 回填，见 2026-06-02-realtime-diarization-voicenote-design）。
run() 是阻塞循环，意在后台线程里跑（接入见 take 生命周期）。
"""
from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable, Iterable
from typing import Any

import numpy as np
import opencc

from backend.asr.whisper_runner import WhisperRunner
from backend.core.events import (
    AUDIO_LEVEL,
    ASR_FINAL_CH1,
    ASR_FINAL_CH2,
    ASR_PARTIAL_CH1,
    ASR_PARTIAL_CH2,
    AsrFinalPayload,
    AsrPartialPayload,
    AudioLevelPayload,
)
from backend.vad.detector import VadDetector
from backend.vad.models import SpeechSegment, VadConfig
from backend.vad.segmenter import ChannelVADSegmenter

logger = logging.getLogger(__name__)

_FRAMES_PER_MS = 16  # 16kHz → 每毫秒 16 帧


class StreamDriver:
    """把 AudioSource 驱动成 asr.final.chN 事件流。"""

    def __init__(
        self,
        runner: WhisperRunner,
        publish: Callable[[str, object], None],
        vad_config: VadConfig,
        detector_factory: Callable[[], VadDetector],
        audio_sink: Callable[[Any, int], None] | None = None,
        process_channels: tuple[int, ...] | None = None,
        partial_audio_ctx: int | None = None,
    ) -> None:
        self._runner = runner
        self._publish = publish
        self._vad_config = vad_config
        self._detector_factory = detector_factory
        # partial 重转用的 audio_ctx（spec §3.4）；None=满窗。final 路径不受此影响。
        self._partial_audio_ctx = partial_audio_ctx
        # ch1 连续音频写入（用于 TakeAudioBuffer → diarization）。
        # 签名 (pcm, start_frame)：喂逐 chunk 的全量 ch1（含静音），保证 buffer
        # 时间轴 == take 绝对时间轴，diarization turn 才能与 ASR 绝对帧对齐（B2）。
        self._audio_sink = audio_sink
        # 只处理这些声道索引（None = 全部）。生产暂设 (0,) 只跑 ch1：避免设备双声道
        # 同源导致每句重复转录；ch2（场记 voice note）路径待基础链路跑通后再开。
        self._process_channels = process_channels
        self._stop = threading.Event()
        # 流式 partial 节流记账（spec §3.2-3.3）：per-channel chunk 计数 + 是否有悬挂 partial。
        self._since_partial: dict[int, int] = {}
        self._partial_active: dict[int, bool] = {}
        # 安全阀（spec §3.5）：采集溢出即停发 partial，回退 final-only，护住 C1/C2。
        # 带启动宽限（吞 PortAudio 启动毛刺）+ 平稳重启（不被单次溢出永久焊死）。
        self._partials_enabled = True
        self._last_overflow = 0
        self._grace_left = 0      # take 起始宽限剩余 chunk
        self._overflow_free = 0   # 跳闸后连续无溢出 chunk 计数（够 rearm 则重启）

    def stop(self) -> None:
        """请求停止 run() 循环（线程安全）。DeviceSource 下一块读完后退出（≤chunk_ms 延迟）。"""
        self._stop.set()

    def run(self, source: Iterable) -> None:
        """阻塞驱动：消费 source 的 AudioChunk 直到耗尽（FileSource）或被 stop() 停掉（DeviceSource）。"""
        self._stop.clear()
        # 每个 take 起始复位 partial 状态（run() 是单 take 入口）。
        self._partials_enabled = True
        self._last_overflow = 0
        self._grace_left = self._vad_config.partial_grace_chunks
        self._overflow_free = 0
        self._since_partial.clear()
        self._partial_active.clear()
        segmenters: list[ChannelVADSegmenter] = []
        draining = False
        with source as src:  # type: ignore[attr-defined]
            for chunk in src:
                if self._stop.is_set() and not draining:
                    # take.end：buffered 源先排空已缓冲的 chunk 再停（别丢尾巴，护 C1/C2）；
                    # 非 buffered 源（FileSource/测试）无 begin_drain → 原行为立即停。
                    begin = getattr(src, "begin_drain", None)
                    if begin is None:
                        break
                    begin()
                    draining = True
                # 安全阀（spec §3.5）：按 DeviceSource.overflow_count 决定是否停发 partial。
                self._update_valve(getattr(src, "overflow_count", 0))
                if not segmenters:
                    segmenters = [
                        ChannelVADSegmenter(ch=i, detector=self._detector_factory(), config=self._vad_config)
                        for i in range(len(chunk.channels))
                    ]
                # ch1（index 0）全量连续音频送入 buffer（含静音），供 take.end 后 diarization。
                # 在 VAD 之前、逐 chunk 写入：buffer 时间轴 = take 绝对时间轴。
                if chunk.channels:
                    ch1 = chunk.channels[0]
                    if self._audio_sink is not None:
                        self._audio_sink(ch1, chunk.start_frame)
                    # 旁路：对 ch1 算 RMS 并推 audio.level（每 chunk 一条，约 5 Hz）。
                    # publish 内部吞异常，失败不影响采集主循环。
                    try:
                        samples = ch1.astype(np.float64)
                        mean_sq = float(np.mean(samples * samples))
                        rms = math.sqrt(mean_sq) / 32768.0
                        rms = min(rms, 1.0)
                        self._publish(AUDIO_LEVEL, AudioLevelPayload(rms=rms))
                    except Exception:  # noqa: BLE001
                        logger.debug("audio.level RMS 计算失败，跳过", exc_info=True)
                for i, ch_audio in enumerate(chunk.channels):
                    if self._process_channels is not None and i not in self._process_channels:
                        continue
                    segs = segmenters[i].push(ch_audio, chunk.start_frame)
                    for seg in segs:
                        self._emit(seg)
                    # final 出了就跟既有路径走；否则按节流发 partial（纯显示，不落库）。
                    self._maybe_emit_partial(i, segmenters[i], final_emitted=bool(segs))
        for sm in segmenters:
            for seg in sm.flush():
                self._emit(seg)

    def _update_valve(self, overflow: int) -> None:
        """安全阀去抖（spec §3.5）：启动宽限吞毛刺 + 单次溢出暂停 partial + 平稳后重启。

        反应式止血不变（溢出已发生才动作），但不再「首次溢出永久焊死」：take 起始 grace 个
        chunk 内的溢出忽略（PortAudio stream.start 启动毛刺），跳闸后连续 rearm 个 chunk 无新
        溢出则重新启用。FileSource 无 overflow_count，传 0 → 永不跳闸。
        """
        new_overflow = overflow > self._last_overflow
        self._last_overflow = overflow
        if self._grace_left > 0:
            self._grace_left -= 1
            return  # 启动宽限期：吞掉毛刺，不跳闸
        if new_overflow:
            if self._partials_enabled:
                self._partials_enabled = False
                logger.warning(
                    "采集溢出（overflow_count=%d）→ 暂停 partial，回退 final-only（C1/C2 优先）",
                    overflow,
                )
            self._overflow_free = 0
        elif not self._partials_enabled:
            self._overflow_free += 1
            if self._overflow_free >= self._vad_config.partial_rearm_chunks:
                self._partials_enabled = True
                self._overflow_free = 0
                logger.info(
                    "采集平稳 %d chunk → 重新启用 partial", self._vad_config.partial_rearm_chunks
                )

    def _maybe_emit_partial(
        self, ch: int, segmenter: ChannelVADSegmenter, *, final_emitted: bool
    ) -> None:
        """节流触发 partial（spec §3.2）。final 出了即清计数；否则到 K 个 chunk peek 重转。"""
        if final_emitted:
            # final 替换前端最后一条 partial（session.ts），无需额外清除。
            self._since_partial[ch] = 0
            self._partial_active[ch] = False
            return
        if not self._partials_enabled:
            return  # 安全阀已跳闸（采集溢出），本 take 不再发 partial
        # turn 结束但没出 final（被 min_speech 门掉）→ 清前端悬挂 partial（spec §3.3）。
        if self._partial_active.get(ch) and not segmenter.in_speech:
            self._emit_partial_clear(ch)
            self._partial_active[ch] = False
            self._since_partial[ch] = 0
            return
        k = self._vad_config.partial_every_chunks
        if k <= 0:
            return  # partial 关闭
        self._since_partial[ch] = self._since_partial.get(ch, 0) + 1
        if self._since_partial[ch] >= k:
            self._since_partial[ch] = 0
            pend = segmenter.peek_pending()
            if pend is not None and self._emit_partial(pend, ch):
                self._partial_active[ch] = True

    def _emit_partial_clear(self, ch: int) -> None:
        """发空文本 partial 作为清除信号（spec §3.6）：前端 applyAsr 据此移除该声道悬挂 partial。"""
        topic = ASR_PARTIAL_CH1 if ch == 0 else ASR_PARTIAL_CH2
        self._publish(
            topic,
            AsrPartialPayload(
                text="", start_frame=0, end_frame=0, speaker=None, take_id=None, is_partial=True
            ),
        )

    def _emit_partial(self, pend: tuple[np.ndarray, int], ch: int) -> bool:
        """重转在制段、发 asr.partial.chN（is_partial=True）。纯显示：不落库、不碰 audio_sink。

        发完整猜测文本（先猜后改：whisper 多听一点会自己改对，前端就地替换）。返回是否真发出。
        """
        pcm, win_start_abs = pend
        text = self._runner.transcribe_pcm(pcm, audio_ctx=self._partial_audio_ctx)
        text = _normalize_to_simplified(text)  # 与 final 同一 t2s 管线，partial/final 同形
        if _is_hallucination_partial(text):
            return False
        logger.info("[cmp] partial ch%d win=%dms %r", ch, len(pcm) // _FRAMES_PER_MS, text)
        topic = ASR_PARTIAL_CH1 if ch == 0 else ASR_PARTIAL_CH2
        payload = AsrPartialPayload(
            text=text,
            start_frame=round(win_start_abs / _FRAMES_PER_MS),
            end_frame=round((win_start_abs + len(pcm)) / _FRAMES_PER_MS),
            speaker=None,
            take_id=None,
            is_partial=True,
        )
        self._publish(topic, payload)
        return True

    def _emit(self, seg: SpeechSegment) -> None:
        # 注意：ch1 音频在 run() 的 chunk 循环里逐块全量写 buffer（含静音），不在此处
        # 按语音段写——否则 buffer 是去静音的压缩时间轴，diarization 无法与 ASR 绝对帧对齐。
        text = self._runner.transcribe_pcm(seg.audio)
        text = _normalize_to_simplified(text)  # 繁→简，幻觉过滤前（繁体幻觉转简后能被简体表拦截）
        if not text.strip():
            return  # 空转录（静音误触/噪声）不推
        if _is_hallucination(text):
            logger.debug("丢弃疑似幻觉转录: %r", text)
            return
        logger.info(
            "[cmp] FINAL ch%d [%d-%dms] %r",
            seg.ch, round(seg.start_frame / _FRAMES_PER_MS), round(seg.end_frame / _FRAMES_PER_MS), text,
        )
        topic = ASR_FINAL_CH1 if seg.ch == 0 else ASR_FINAL_CH2
        payload = AsrFinalPayload(
            text=text,
            start_frame=round(seg.start_frame / _FRAMES_PER_MS),  # 16k 帧 → 毫秒（contract C1）
            end_frame=round(seg.end_frame / _FRAMES_PER_MS),
            speaker=None,            # Phase 1 不出 speaker；take.end 后回填
            take_id=None,            # orchestrator 由 session.take_id 回退
            is_partial=False,
        )
        self._publish(topic, payload)


# Whisper 在噪声/静音段上的典型幻觉输出（大小写不敏感，含即过滤）
_HALLUCINATION_PATTERNS = [
    "谢谢",
    "谢谢观看",
    "请订阅",
    "字幕",
    "thank you",
    "thanks for watching",
    "subscribe",
    "please subscribe",
    "[music]",
    "[applause]",
    "[noise]",
    "[blank_audio]",
    "♪",
]


# 片场高频单字指令：whisper 转对了也是单字，不能被短文本门当幻觉丢。
_COMMAND_WORDS = {
    "停", "走", "好", "过", "卡", "开", "收", "准", "行", "来", "上", "下", "对",
    "cut", "action", "ok", "go",
}

# 句末/句中标点（全角 + 半角），判长度/匹配白名单前先剥掉。
_PUNCT_CHARS = "。，！？、；：·…—～「」『』（）《》,.!?;:~ \t\r\n\"'"


def _strip_punct(text: str) -> str:
    """剥掉首尾标点与空白（全角半角）。让「停。」「走！」按「停」「走」判定。"""
    return text.strip().strip(_PUNCT_CHARS)


def _is_hallucination(text: str) -> bool:
    """检测 Whisper 常见幻觉输出（final 路径）。

    先剥标点。空文本丢。单字（len<=1）只放行片场指令白名单、其余当幻觉丢（噪声常吐孤立单字）。
    双字及以上走 _HALLUCINATION_PATTERNS 模式过滤。修复点：旧版 len<=2 一刀切把「停/走/过来」
    这类真短词全丢，正是短词漏检的主凶。
    """
    t = _strip_punct(text).lower()
    if not t:
        return True
    if len(t) <= 1:
        return t not in _COMMAND_WORDS  # 孤立单字：仅放行指令白名单
    return any(pat in t for pat in _HALLUCINATION_PATTERNS)


def _is_hallucination_partial(text: str) -> bool:
    """partial 放宽幻觉门（spec §3.4）：丢空文本 + 模式匹配，但**不**做 len<=2 短文本过滤。

    去掉短文本门：partial 每句开头 1-2 字正是要尽早上屏的，不能被吞。final 路径仍走
    严格的 _is_hallucination。
    """
    t = text.strip().lower()
    if not t:
        return True
    return any(pat in t for pat in _HALLUCINATION_PATTERNS)


# 模块级 opencc 单例，避免每次 _emit 重复初始化
_CC = opencc.OpenCC("t2s")


def _normalize_to_simplified(text: str) -> str:
    """繁体转简体（opencc t2s）。已是简体或英文时完全幂等。"""
    return _CC.convert(text)
