export type Status = "keeper" | "ng" | "hold" | "recording"

export interface Line {
  speaker: string
  text: string
}

export interface Take {
  id: string
  scene: number
  shot: number
  no: number
  status: Status
  lines: Line[]
}
