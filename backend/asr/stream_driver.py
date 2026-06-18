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
from backend.vad.models import SpeechSegment, VadConfig, frames_to_ms
from backend.vad.segmenter import ChannelVADSegmenter

logger = logging.getLogger(__name__)


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
        partial_runner: Any = None,
        engine: str = "whisper",
    ) -> None:
        self._runner = runner
        # paraformer(funasr) 声学忠实、不吐 whisper 式幻觉 → 信任输出，final 跳过短文本 / 幻觉门
        # （whisper 才需要门：它在静音 / 噪声段脑补训练集高频文本）。
        self._trust_asr = engine == "funasr"
        self._publish = publish
        self._vad_config = vad_config
        self._detector_factory = detector_factory
        # ch1 连续音频写入（用于 TakeAudioBuffer → diarization）。
        # 签名 (pcm, start_frame)：喂逐 chunk 的全量 ch1（含静音），保证 buffer
        # 时间轴 == take 绝对时间轴，diarization turn 才能与 ASR 绝对帧对齐（B2）。
        self._audio_sink = audio_sink
        # 只处理这些声道索引（None = 全部）。生产暂设 (0,) 只跑 ch1：避免设备双声道
        # 同源导致每句重复转录；ch2（场记 voice note）路径待基础链路跑通后再开。
        self._process_channels = process_channels
        # 2pass 流式 partial(spec 2026-06-11-funasr-2pass-streaming):None 时下面的
        # partial 代码一行不跑(whisper / funasr 关流式 / online 不可用 —— 零回归红线 R3)。
        # 单实例:仅支持单 partial 声道(生产 process_channels=(0,));开 ch2 需改 per-channel runner
        self._partial_runner = partial_runner
        self._partial_dead = False              # 熔断:本 take 余下停发 partial(含墓碑)
        self._turn_keys: dict[int, int] = {}    # ch -> 在制 turn 的起始绝对帧(== final 键)
        self._partial_counts: dict[int, int] = {}  # ch -> 本 turn 已发 partial 条数
        self._stop = threading.Event()

    def stop(self) -> None:
        """请求停止 run() 循环（线程安全）。DeviceSource 下一块读完后退出（≤chunk_ms 延迟）。"""
        self._stop.set()

    def run(self, source: Iterable) -> None:
        """阻塞驱动：消费 source 的 AudioChunk 直到耗尽（FileSource）或被 stop() 停掉（DeviceSource）。"""
        self._stop.clear()
        # 注:partial 状态(_partial_dead/_turn_keys)不在此重置 —— driver 实例 = 单 take(live_session 每 take 新建)
        draining = False
        segmenters: list[ChannelVADSegmenter] = []
        with source as src:  # type: ignore[attr-defined]
            for chunk in src:
                if self._stop.is_set() and not draining:
                    if hasattr(src, "begin_drain"):
                        # BufferedAudioSource：停读新块、排空已缓冲尾巴再退出
                        # （尾段 PCM 进 TakeAudioBuffer、尾句进 VAD/final —— C1/C2）。
                        src.begin_drain()
                        draining = True
                    else:
                        break
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
                    finals_pub: dict[int, bool] = {}  # seg.start_frame -> 是否真的 publish 了
                    for seg in segmenters[i].push(ch_audio, chunk.start_frame):
                        finals_pub[seg.start_frame] = self._emit(seg)
                    if self._partial_runner is not None:
                        self._partial_step(i, segmenters[i], ch_audio, chunk.start_frame, finals_pub)
        for i, sm in enumerate(segmenters):
            finals_pub = {}
            for seg in sm.flush():
                finals_pub[seg.start_frame] = self._emit(seg)
            if self._partial_runner is not None and self._turn_keys.get(i) is not None:
                self._settle_turn(i, self._turn_keys[i], finals_pub)

    def _emit(self, seg: SpeechSegment) -> bool:
        """转录并 publish 一段 final。返回是否真的 publish 了（空文本/幻觉被滤 → False，
        partial 墓碑记账需要这个信号）。"""
        # 注意：ch1 音频在 run() 的 chunk 循环里逐块全量写 buffer（含静音），不在此处
        # 按语音段写——否则 buffer 是去静音的压缩时间轴，diarization 无法与 ASR 绝对帧对齐。
        text = self._runner.transcribe_pcm(seg.audio)
        text = _normalize_to_simplified(text)  # 繁→简，幻觉过滤前（繁体幻觉转简后能被简体表拦截）
        if not text.strip():
            return False  # 空转录（静音误触/噪声）不推
        if _is_hallucination(text, trust_asr=self._trust_asr):
            logger.debug("丢弃疑似幻觉转录: %r", text)
            return False
        topic = ASR_FINAL_CH1 if seg.ch == 0 else ASR_FINAL_CH2
        payload = AsrFinalPayload(
            text=text,
            start_frame=frames_to_ms(seg.start_frame),  # 16k 帧 → 毫秒（contract C1）
            end_frame=frames_to_ms(seg.end_frame),
            speaker=None,            # Phase 1 不出 speaker；take.end 后回填
            take_id=None,            # orchestrator 由 session.take_id 回退
            is_partial=False,
        )
        self._publish(topic, payload)
        return True

    # ---- 2pass 流式 partial(spec 2026-06-11-funasr-2pass-streaming §3)----

    def _partial_step(self, ch: int, segmenter, ch_audio, chunk_start: int, finals_pub: dict[int, bool]) -> None:
        """每 chunk 一次:结清旧 turn → (在语音中)开 turn/喂料/publish partial。
        必须在本 chunk 的 final _emit 之后调用 —— 同 chunk close+reopen 时
        旧 final 先于新 turn 首条 partial(前端 replace 语义依赖此序)。"""
        cur_key = segmenter.segment_start_frame  # 语音态 = _seg_start_abs;否则 None
        prev_key = self._turn_keys.get(ch)
        if prev_key is not None and cur_key != prev_key:
            self._settle_turn(ch, prev_key, finals_pub)
        if self._partial_dead or cur_key is None:
            return
        try:
            if self._turn_keys.get(ch) != cur_key:
                self._partial_runner.start_turn()
                self._turn_keys[ch] = cur_key
                self._partial_counts[ch] = 0
            # 喂料折衷(spec §3 Q4):VAD 进语音后的 chunk 整块 —— 不含 pre-roll、
            # 首块含起音前静音,首词 partial 可能缺/糊;final 吃完整段音频会纠正。不是 bug。
            text = self._partial_runner.feed(ch_audio)
            if text:
                payload = AsrPartialPayload(
                    text=text,
                    start_frame=frames_to_ms(cur_key),  # 与 final 逐位同键
                    # end 可超 final 的 end(尾静音裁剪 + 600ms 块量化);前端不拿 end 做键,无害
                    end_frame=frames_to_ms(chunk_start + len(ch_audio)),
                    speaker=None,
                    take_id=None,
                    is_partial=True,
                )
                self._publish(ASR_PARTIAL_CH1 if ch == 0 else ASR_PARTIAL_CH2, payload)
                self._partial_counts[ch] += 1
        except Exception:  # noqa: BLE001
            logger.warning("partial 路径异常,本 take 熔断(final 不受影响)", exc_info=True)
            self._fuse(ch)

    def _settle_turn(self, ch: int, key_frames: int, finals_pub: dict[int, bool]) -> None:
        """turn 结清:释放 runner 状态;发过 partial 但 final 未 publish → 墓碑(恰一条)。"""
        try:
            self._partial_runner.end_turn()
        except Exception:  # noqa: BLE001
            logger.debug("end_turn 异常,忽略(start_turn 自带清态)", exc_info=True)
        if self._partial_counts.get(ch, 0) > 0 and not finals_pub.get(key_frames, False):
            self._publish_tombstone(ch, key_frames)
        self._turn_keys.pop(ch, None)
        self._partial_counts[ch] = 0

    def _fuse(self, ch: int) -> None:
        """熔断:墓碑收尾活跃 turn 后,本 take 不再发任何 partial。final 与采集不受影响。"""
        try:
            key = self._turn_keys.get(ch)
            if key is not None and self._partial_counts.get(ch, 0) > 0:
                self._publish_tombstone(ch, key)
            self._partial_runner.end_turn()
        except Exception:  # noqa: BLE001
            logger.debug("熔断清理异常,忽略", exc_info=True)
        self._turn_keys.pop(ch, None)
        self._partial_counts[ch] = 0
        self._partial_dead = True

    def _publish_tombstone(self, ch: int, key_frames: int) -> None:
        """空文本 partial = 前端清除信号(同 turn 键;end 不被消费,取同值)。"""
        payload = AsrPartialPayload(
            text="", start_frame=frames_to_ms(key_frames), end_frame=frames_to_ms(key_frames),
            speaker=None, take_id=None, is_partial=True,
        )
        self._publish(ASR_PARTIAL_CH1 if ch == 0 else ASR_PARTIAL_CH2, payload)


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


# 单字短词白名单：转对了也只有一个字，不能被短文本门当幻觉丢。分两组，幻觉风险不同：
# - 片场动作指令：whisper 也极少误吐，放行无虞。
# - 应答/确认词：承载「稍等 / 知道了 / 确认」语义（场记说「嗯」常表示稍等）。paraformer 几乎不吐，
#   VAD 切段（min_speech + threshold）又挡掉多数 whisper 静音幻觉，故统一放行。若 whisper 路径
#   将来「嗯」幻觉刷屏，可改成仅 funasr 放行 _ANSWER_WORDS（判定门不变，只白名单内容按引擎分）。
#   纯迟疑填充（呃/额/唉）有意不收：对场记无信息价值，又是 whisper 高发幻觉，收进来只放大噪声。
_COMMAND_WORDS = {
    "停", "走", "好", "过", "卡", "开", "收", "准", "行", "来", "上", "下", "对",
    "cut", "action", "ok", "go",
}
_ANSWER_WORDS = {"嗯", "哦", "噢", "欸", "诶", "是"}
_SHORT_WHITELIST = _COMMAND_WORDS | _ANSWER_WORDS

# 句末/句中标点（全角 + 半角），判长度 / 匹配白名单前先剥掉。
_PUNCT_CHARS = "。，！？、；：·…—～「」『』（）《》,.!?;:~ \t\r\n\"'"


def _strip_punct(text: str) -> str:
    """剥掉首尾标点与空白（全角半角），让「停。」「好的！」按「停」「好的」判定。"""
    return text.strip().strip(_PUNCT_CHARS)


def _is_hallucination(text: str, *, trust_asr: bool = False) -> bool:
    """检测幻觉 / 噪声转录（final 路径）。

    先剥标点、空文本一律丢（不是有效转录）。其后按引擎信任度分流：

    trust_asr=True（paraformer：声学忠实、上游 silero VAD 已把关语音性、不吐 whisper 式幻觉）
    → 非空即放行，不设长度门 / 白名单 / 模式表，避免误伤白名单覆盖不到的真实短词（「三」「快」…）。

    trust_asr=False（whisper：会在静音 / 噪声段脑补训练集高频文本）→ 跑全套门：单字（len<=1）
    仅放行短词白名单，双字及以上走 _HALLUCINATION_PATTERNS 模式表。
    （whisper 旧版 len<=2 一刀切把「停 / 好 / 是的 / 知道」这类真短词全丢，是短词漏检的主凶。）
    """
    t = _strip_punct(text).lower()
    if not t:
        return True
    if trust_asr:
        return False  # 信任 ASR：非空转录一律放行
    if len(t) <= 1:
        return t not in _SHORT_WHITELIST  # 孤立单字：仅放行白名单
    return any(pat in t for pat in _HALLUCINATION_PATTERNS)


# 模块级 opencc 单例，避免每次 _emit 重复初始化
_CC = opencc.OpenCC("t2s")


def _normalize_to_simplified(text: str) -> str:
    """繁体转简体（opencc t2s）。已是简体或英文时完全幂等。"""
    return _CC.convert(text)
