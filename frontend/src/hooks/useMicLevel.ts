import { useEffect, useRef, useState } from "react"

// 实时麦克风电平 [0,1]。浏览器 getUserMedia + AnalyserNode 取时域 RMS，rAF 刷新。
// 与后端采集是各自独立的流（Windows 共享模式可同时打开同一麦克风），仅用于 header 监看
// “麦克风是否在收声”。无权限/无设备时静默返回 0。
export function useMicLevel(enabled = true): number {
  const [level, setLevel] = useState(0)
  const rafRef = useRef<number | null>(null)
  const ctxRef = useRef<AudioContext | null>(null)
  const streamRef = useRef<MediaStream | null>(null)

  useEffect(() => {
    if (!enabled) return
    let cancelled = false

    const start = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop())
          return
        }
        streamRef.current = stream
        const ctx = new AudioContext()
        ctxRef.current = ctx
        const src = ctx.createMediaStreamSource(stream)
        const analyser = ctx.createAnalyser()
        analyser.fftSize = 512
        src.connect(analyser)
        const buf = new Uint8Array(analyser.fftSize)

        const tick = () => {
          analyser.getByteTimeDomainData(buf)
          let sum = 0
          for (let i = 0; i < buf.length; i++) {
            const v = (buf[i] - 128) / 128
            sum += v * v
          }
          const rms = Math.sqrt(sum / buf.length)
          setLevel(Math.min(1, rms * 3)) // 放大便于观察
          rafRef.current = requestAnimationFrame(tick)
        }
        rafRef.current = requestAnimationFrame(tick)
      } catch {
        // 无麦克风权限/设备：保持 0，不报错
      }
    }
    void start()

    return () => {
      cancelled = true
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
      ctxRef.current?.close()
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [enabled])

  return level
}
