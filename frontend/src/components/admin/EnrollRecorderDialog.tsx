import { useEffect, useRef, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { enrollSpeaker } from "@/lib/api"
import { blobToWav16kMono } from "@/lib/wav"
import type { SpeakerDTO } from "@/types/api"
import { Loader2, Mic, Square } from "lucide-react"

type Phase = "idle" | "recording" | "saving" | "done" | "error"

// 录声纹时照着念的样例对白（内容中性，约 15–25 秒，音素覆盖均衡）。
const SAMPLE_SCRIPT =
  "大家好，我现在正在录制我的声音样本。这段话没有特别的含义，" +
  "只是为了让系统记住我说话的声音和语调。我会用平时聊天的语气，" +
  "自然地把这几句话念完。今天天气不错，适合出门走走，" +
  "也适合安静地待在房间里看书。好，就到这里，谢谢。"

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  speaker: SpeakerDTO | null
  onEnrolled: () => void
}

// 录制声纹：浏览器录音 → 转 16k 单声道 WAV → enrollSpeaker（覆盖该演员旧声纹）。
export default function EnrollRecorderDialog({ open, onOpenChange, speaker, onEnrolled }: Props) {
  const [phase, setPhase] = useState<Phase>("idle")
  const [elapsed, setElapsed] = useState(0)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const releaseMic = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    recorderRef.current = null
    chunksRef.current = []
  }

  // 卸载时释放麦克风。父组件用 key 在每次打开时重挂本组件，故状态天然从 idle 复位。
  useEffect(() => releaseMic, [])

  const startRecording = async () => {
    if (!speaker) return
    setErrorMsg(null)
    try {
      // 声纹采集必须拿"原始"麦克风音频：关掉浏览器默认的语音通话 DSP
      // （回声消除/降噪/自动增益）——它们会抹掉说话人辨识依赖的频谱细节，
      // 导致不同人的声纹互相靠拢、识别塌成一个人。
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      })
      streamRef.current = stream
      const rec = new MediaRecorder(stream)
      chunksRef.current = []
      rec.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data)
      }
      rec.onstop = () => void handleStopped(rec.mimeType)
      rec.start()
      recorderRef.current = rec
      setPhase("recording")
      setElapsed(0)
      timerRef.current = setInterval(() => setElapsed((e) => e + 0.2), 200)
    } catch (e) {
      console.error("getUserMedia failed", e)
      setPhase("error")
      setErrorMsg("无法访问麦克风（检查浏览器权限）")
    }
  }

  const stopRecording = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    recorderRef.current?.stop() // 触发 onstop → handleStopped
  }

  const handleStopped = async (mimeType: string) => {
    const sp = speaker
    if (!sp) return
    setPhase("saving")
    try {
      const blob = new Blob(chunksRef.current, { type: mimeType || "audio/webm" })
      streamRef.current?.getTracks().forEach((t) => t.stop())
      streamRef.current = null
      const wav = await blobToWav16kMono(blob)
      const file = new File([wav], `enroll_${sp.speaker_id}.wav`, { type: "audio/wav" })
      await enrollSpeaker(sp.speaker_id, file)
      setPhase("done")
      onEnrolled()
    } catch (e) {
      const text = e instanceof Error ? e.message : "声纹录入失败"
      setPhase("error")
      setErrorMsg(text)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>录制声纹{speaker ? ` · ${speaker.display_name}` : ""}</DialogTitle>
          <DialogDescription>
            对着麦克风念下面这段话，自然语气即可（建议 15–30 秒，<b>请勿超过 30 秒</b>）。
            停止后自动保存，覆盖该演员旧声纹。
          </DialogDescription>
        </DialogHeader>

        {(phase === "idle" || phase === "recording") && (
          <div className="rounded-xl bg-muted/60 px-4 py-3 text-sm leading-relaxed text-foreground max-h-40 overflow-y-auto">
            {SAMPLE_SCRIPT}
          </div>
        )}

        <div className="flex flex-col items-center gap-4 py-4">
          {phase === "recording" ? (
            <>
              <div
                className={
                  "flex items-center gap-2 " +
                  (elapsed > 30 ? "text-amber-600" : "text-destructive")
                }
              >
                <span
                  className={
                    "size-2.5 rounded-full animate-pulse " +
                    (elapsed > 30 ? "bg-amber-500" : "bg-destructive")
                  }
                />
                <span className="font-mono text-2xl tabular-nums">{elapsed.toFixed(1)}s</span>
              </div>
              {elapsed > 30 && (
                <span className="text-xs text-amber-600">已超过 30 秒，建议停止</span>
              )}
              <Button variant="destructive" size="lg" className="gap-2 rounded-full" onClick={stopRecording}>
                <Square className="size-4" />停止并保存
              </Button>
            </>
          ) : phase === "saving" ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />正在转码 + 提取声纹…
            </div>
          ) : phase === "done" ? (
            <>
              <div className="text-sm text-green-600">声纹已录入 ✓</div>
              <Button variant="secondary" onClick={() => onOpenChange(false)}>完成</Button>
            </>
          ) : (
            <>
              <Button variant="default" size="lg" className="gap-2 rounded-full" onClick={startRecording}>
                <Mic className="size-5" />开始录制
              </Button>
              {errorMsg && <div className="text-xs text-destructive text-center">{errorMsg}</div>}
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
