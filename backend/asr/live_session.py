"""LiveAsrSession：take 期间的实时 ASR 会话生命周期。

take.start → start()：起后台守护线程跑 StreamDriver.run(source_factory())。
take.end   → stop()：driver.stop() 置停止标志 + join 线程。

后台线程里 driver 调 orchestrator.publish(asr.final.chN) → ConnectionManager.broadcast
经 run_coroutine_threadsafe 跨线程投递到 WS（ws.py 已支持 asr 线程路径）。
source_factory 注入 DeviceSource 工厂（测试可注入假源）。
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable

from backend.asr.funasr_online import FunAsrOnlineRunner  # 模块自身懒 import funasr,顶层安全
from backend.asr.funasr_runner import FunAsrRunner
from backend.asr.stream_driver import StreamDriver
from backend.asr.whisper_runner import WhisperRunner
from backend.diarization.buffer import TakeAudioBuffer
from backend.vad.detector import VadDetector
from backend.vad.models import VadConfig

logger = logging.getLogger(__name__)


class LiveAsrSession:
    """单实例、可重入 start/stop 的实时 ASR 线程管理者。"""

    def __init__(
        self,
        runner: WhisperRunner,
        publish: Callable[[str, object], None],
        source_factory: Callable[[object], Iterable],
        vad_config: VadConfig,
        detector_factory: Callable[[], VadDetector],
        default_device: object = None,
        process_channels: tuple[int, ...] | None = (0,),
        funasr_runner_factory: Callable[[], object] | None = None,
        funasr_online_factory: Callable[[], object] | None = None,
        funasr_partials: bool = True,
    ) -> None:
        self._runner = runner
        self._engine = "whisper"
        self._whisper_runner = runner  # 切回 whisper 时复用,不重建
        self._funasr_runner: object | None = None  # 首切 funasr 时构造
        self._funasr_runner_factory = funasr_runner_factory or FunAsrRunner
        self._funasr_online_factory = funasr_online_factory or FunAsrOnlineRunner
        self._funasr_partials = funasr_partials  # env 杀开关(False 时不构造/不下载 online,spec §3 Q3)
        self._funasr_online: object | None = None
        self._online_available = False
        self._publish = publish
        self._source_factory = source_factory  # (device) -> AudioSource
        self._vad_config = vad_config
        self._detector_factory = detector_factory
        self._device = default_device  # None = 系统默认输入；否则设备索引/名
        # 默认只跑 ch1（避免双声道同源重复转录）；ch2 voice note 待基础链路跑通后再开。
        self._process_channels = process_channels
        self._driver: StreamDriver | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._switch_lock = threading.Lock()  # 串行化 set_engine(warmup 可能分钟级,禁止并发切换)
        self.audio_buffer = TakeAudioBuffer()  # ch1 PCM 累积，供 diarization 使用

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def device(self) -> object:
        return self._device

    def set_device(self, device: object) -> None:
        """设置下次 take 使用的输入设备（运行中的 take 不受影响，停后重起生效）。"""
        with self._lock:
            self._device = device

    def make_source(self) -> Iterable:
        """按当前设备构造一个 AudioSource（与 take 同设备解析，跟随 set_device）。

        供 EnrollRecorder 复用 —— 保证录声纹和 Capture 永远用同一支后端麦。
        """
        with self._lock:
            device = self._device
        return self._source_factory(device)

    @property
    def language(self) -> str:
        return self._runner.language

    @property
    def model_size(self) -> str:
        return self._runner.model_size

    def set_language(self, language: str) -> None:
        """切换转录语言（即时生效，下一段起）。"""
        self._runner.set_language(language)

    @property
    def engine(self) -> str:
        return self._engine

    @property
    def streaming(self) -> bool:
        """2pass 流式 partial 是否激活(funasr 引擎 ∧ online 可用;env 关时 online 恒不可用)。"""
        return self._engine == "funasr" and self._online_available

    def set_engine(self, engine: str) -> None:
        """切换 ASR 引擎。仅非录制时可调;funasr 首切时构造并 warmup(同步加载模型,
        首次含 modelscope 下载)。重启后归位 whisper(不持久化,见设计文档)。"""
        if engine not in ("whisper", "funasr"):
            raise ValueError(f"未知 ASR 引擎: {engine}")
        if not self._switch_lock.acquire(blocking=False):
            raise RuntimeError("引擎切换进行中,稍后重试")
        try:
            if self.running:  # 快速失败;锁内还会复检
                raise RuntimeError("录制中不可切换引擎")
            if engine == self._engine:
                return
            if engine == "funasr":
                if self._funasr_runner is None:
                    self._funasr_runner = self._funasr_runner_factory()
                self._funasr_runner.warmup()  # FunAsrNotInstalled 在此抛出,引擎状态不变
                # online 流式模型:仅 env 开启时构造/warmup;失败软降级 final-only(R6),
                # 每次 whisper→funasr 切换重试(瞬态网络故障跨切换自愈,spec §3 Q4)。
                self._online_available = False
                if self._funasr_partials:
                    try:
                        if self._funasr_online is None:
                            self._funasr_online = self._funasr_online_factory()
                        self._funasr_online.warmup()
                        self._online_available = True
                    except Exception:  # noqa: BLE001
                        logger.warning("FunASR 流式模型不可用,降级 final-only", exc_info=True)
                new_runner = self._funasr_runner
            else:
                new_runner = self._whisper_runner
            with self._lock:
                # 复检:warmup 阻塞期间(首次 ~1GB 下载)可能有 take.start 抢先起线程,
                # 锁内直接读 _thread(不经 running property,语义更明确)。
                if self._thread is not None and self._thread.is_alive():
                    raise RuntimeError("录制中不可切换引擎")
                self._runner = new_runner
                self._engine = engine
        finally:
            self._switch_lock.release()

    def start(self) -> None:
        """起后台线程。已在运行则忽略（幂等）。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                logger.warning("live ASR 已在运行，忽略重复 start")
                return
            self.audio_buffer.clear()  # 清空上一 take 残留
            # 2pass partial 注入:三条件(engine=funasr ∧ online 可用 ∧ env 开)收敛在 streaming
            partial_runner = self._funasr_online if self.streaming else None
            driver = StreamDriver(
                runner=self._runner,
                publish=self._publish,
                vad_config=self._vad_config,
                detector_factory=self._detector_factory,
                audio_sink=self.audio_buffer.append,
                process_channels=self._process_channels,
                partial_runner=partial_runner,
                engine=self._engine,
            )
            self._driver = driver
            self._thread = threading.Thread(
                target=self._run_safe,
                args=(driver, partial_runner is not None),
                name="live-asr",
                daemon=True,
            )
            self._thread.start()

    def _run_safe(self, driver: StreamDriver, wrap_buffered: bool = False) -> None:
        try:
            source = self._source_factory(self._device)
            if wrap_buffered:
                # 局部 import:backend.audio.__init__ 顶层拉 sounddevice(PortAudio 初始化),
                # 保住 live_session 无音频栈可导入
                from backend.audio.buffered_source import BufferedAudioSource
                source = BufferedAudioSource(source)  # 仅 partial 激活时包裹(R3/R5)
            driver.run(source)
        except Exception:
            logger.exception("live ASR 驱动线程异常退出")

    def stop(self, timeout: float = 5.0) -> None:
        """停止采集并 join 线程。未运行时安全 no-op。"""
        with self._lock:
            driver, thread = self._driver, self._thread
            self._driver, self._thread = None, None
        if driver is not None:
            driver.stop()
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning("live ASR 线程 %.1fs 内未收束（source 可能卡在阻塞读）", timeout)
