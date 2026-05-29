import type { Take } from "@/types/take"

export const HISTORY_TAKES: Take[] = [
  {
    id: "t1",
    scene: 3,
    shot: 2,
    no: 1,
    status: "keeper",
    lines: [
      { speaker: "SZA", text: "你昨天为什么没有告诉我真相。" },
      { speaker: "YY", text: "因为我不想让你再卷进来。" },
    ],
    note: "表演自然，情绪到位",
  },
  {
    id: "t2",
    scene: 3,
    shot: 2,
    no: 2,
    status: "ng",
    lines: [
      { speaker: "SZA", text: "你昨天为什么没告诉我真相？" },
      { speaker: "YY", text: "因为我..." },
    ],
    note: "YY 最后一句忘词，需补拍",
  },
  {
    id: "t3",
    scene: 3,
    shot: 2,
    no: 3,
    status: "hold",
    lines: [
      { speaker: "SZA", text: "你昨天为什么没有告诉我真相。" },
      { speaker: "YY", text: "因为我不想让你再卷进来。" },
    ],
    note: "台词准确但情绪偏淡，待导演确认",
  },
  {
    id: "t4",
    scene: 3,
    shot: 2,
    no: 4,
    status: "keeper",
    lines: [
      { speaker: "SZA", text: "你昨天为什么没有告诉我真相。" },
      { speaker: "YY", text: "因为我不想让你再卷进来。" },
      { speaker: "SZA", text: "那你打算什么时候告诉我。" },
    ],
    note: "完整表演，台词与剧本一致",
  },
  {
    id: "t5",
    scene: 3,
    shot: 2,
    no: 5,
    status: "keeper",
    lines: [
      { speaker: "SZA", text: "你昨天为什么没有告诉我真相。" },
      { speaker: "YY", text: "因为我担心你会更难过。" },
    ],
    note: "即兴改词，导演认可",
  },
]

export const CURRENT_TAKE: Take = {
  id: "t6",
  scene: 3,
  shot: 2,
  no: 6,
  status: "recording",
  lines: [{ speaker: "SZA", text: "那你打算什么时候告诉我。" }],
}

export const CURRENT_PARTIAL = "等你准备好"

export const INPUT_DEVICE = "MacBook Microphone"
export const INPUT_CHANNELS = 2

export const LLM_STATES = [
  { key: "idle", detail: "Idle", tone: "ok" as const },
  { key: "l1", detail: "L1", tone: "warn" as const },
  { key: "l2", detail: "L2", tone: "warn" as const },
  { key: "l3", detail: "L3", tone: "warn" as const },
  { key: "voice", detail: "Voice", tone: "warn" as const },
  { key: "photo", detail: "Photo", tone: "warn" as const },
  { key: "script", detail: "Script", tone: "warn" as const },
]

export const LLM_FEEDBACK = [
  { kind: "summary", text: "T4 表演完整，台词与剧本一致。本场建议 keeper。" },
  { kind: "diff", text: "L102 改词：『再卷进来』→『卷进来』" },
  { kind: "note", text: "Ch2 提示：T2 二号最后一句漏词，先 hold。" },
]
