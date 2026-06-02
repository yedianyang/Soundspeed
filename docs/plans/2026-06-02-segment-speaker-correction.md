# take detail 说话人归属纠正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 take detail 里把单条 segment 的 speaker 改判给正确说话人，落库改 `transcript_segments.speaker`，L2 不重跑。

**Architecture:** 后端加 DAL 读/改方法 + 嵌套 PATCH 端点（处理顺序定死 404/422 边界）；前端加 API 客户端 + 复用改造死代码组件 `SpeakerLabel` + 接进 `HistoryTakes` 的 take 详情，纠正标记放父组件本地态。

**Tech Stack:** 后端 FastAPI + SQLite + pytest；前端 React + Vite + TS + @tanstack/react-query（无测试框架，前端走手动验收）。

**配套 spec:** `docs/specs/2026-06-02-segment-speaker-correction.md`

---

## 文件结构

后端：
- Modify `backend/db/dal.py` — 加 `get_segment`、`update_segment_speaker`。
- Modify `backend/api/routes/takes.py` — 加 `PatchSegmentBody` + `PATCH /takes/{take_id}/segments/{segment_id}`。
- Modify `backend/tests/test_dal.py` — DAL 两方法测试。
- Modify `backend/tests/test_api.py` — 端点测试。

前端（无测试框架，每任务以 `pnpm build` 类型检查 + lint 把关）：
- Modify `frontend/src/lib/constants.ts` — 共用 hash helper + `speakerDot`。
- Modify `frontend/src/lib/api.ts` — `correctSegmentSpeaker`。
- Modify `frontend/src/routes/admin/components/SpeakerLabel.tsx` — props 化、配色哈希、`null`=未知、`disabled`。
- Modify `frontend/src/routes/admin/components/HistoryTakes.tsx` — `correctedTakes` 父态、接 `SpeakerLabel`、L2 stale 提示。

文档：
- Modify `docs/2026-05-29-1.J-1.L-browser-runbook.md` — 补手动验收节。

> **两个 lane 可并行**：后端（Task 1-2）与前端（Task 3-6）唯一耦合是 API 契约 `PATCH /api/v1/takes/{take_id}/segments/{segment_id}` body `{speaker: string|null}` → 200 `SegmentOut`，已在本计划锁定，前端可照契约先行。联调在两 lane 都完成后。

---

## 后端 lane

### Task 1: DAL — get_segment + update_segment_speaker

**Files:**
- Modify: `backend/db/dal.py`（`# ── transcript_segments ──` 段，`list_segments` 之后，约 414 行后）
- Test: `backend/tests/test_dal.py`

- [ ] **Step 1: 写失败测试**

加到 `backend/tests/test_dal.py` 末尾：

```python
# ── get_segment / update_segment_speaker ──────────────────────────────────


def test_get_segment_returns_row(tmp_dal: DAL) -> None:
    sid = tmp_dal.create_scene("S1")
    tid = tmp_dal.start_take(sid, 1, 1000.0)
    seg_id = tmp_dal.insert_segment(tid, 1, "SPEAKER_00", "你好", 0, 16000)
    seg = tmp_dal.get_segment(seg_id)
    assert seg is not None
    assert seg.take_id == tid
    assert seg.ch == 1
    assert seg.speaker == "SPEAKER_00"


def test_get_segment_missing_returns_none(tmp_dal: DAL) -> None:
    assert tmp_dal.get_segment(99999) is None


def test_update_segment_speaker_changes_value(tmp_dal: DAL) -> None:
    sid = tmp_dal.create_scene("S1")
    tid = tmp_dal.start_take(sid, 1, 1000.0)
    seg_id = tmp_dal.insert_segment(tid, 1, "SPEAKER_00", "你好", 0, 16000)
    affected = tmp_dal.update_segment_speaker(seg_id, "SPEAKER_01")
    assert affected == 1
    seg = tmp_dal.get_segment(seg_id)
    assert seg is not None and seg.speaker == "SPEAKER_01"


def test_update_segment_speaker_to_none(tmp_dal: DAL) -> None:
    sid = tmp_dal.create_scene("S1")
    tid = tmp_dal.start_take(sid, 1, 1000.0)
    seg_id = tmp_dal.insert_segment(tid, 1, "SPEAKER_00", "你好", 0, 16000)
    affected = tmp_dal.update_segment_speaker(seg_id, None)
    assert affected == 1
    seg = tmp_dal.get_segment(seg_id)
    assert seg is not None and seg.speaker is None


def test_update_segment_speaker_missing_returns_zero(tmp_dal: DAL) -> None:
    assert tmp_dal.update_segment_speaker(99999, "X") == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run（worktree 根目录执行）: `/Users/yedianyang/Documents/GitHub/Soundspeed/.venv/bin/python -m pytest backend/tests/test_dal.py -k "get_segment or update_segment_speaker" -v`
Expected: FAIL — `AttributeError: 'DAL' object has no attribute 'get_segment'`。

- [ ] **Step 3: 实现 DAL 方法**

在 `backend/db/dal.py` 的 `list_segments` 方法之后插入：

```python
    def get_segment(self, segment_id: int) -> TranscriptSegment | None:
        """按 segment_id 获取单条片段，不存在返回 None。"""
        row = self._conn.execute(
            "SELECT * FROM transcript_segments WHERE segment_id = ?;",
            (segment_id,),
        ).fetchone()
        return _row_to_segment(row) if row else None

    def update_segment_speaker(self, segment_id: int, speaker: str | None) -> int:
        """改单条片段的 speaker，返回受影响行数（0 = segment 不存在）。

        归属（take 匹配）与 ch1 限制由 route 层先用 get_segment 校验，不进 WHERE。
        speaker=None 表示置「未知」（schema 允许 NULL）。
        """
        with self._write_tx() as conn:
            cur = conn.execute(
                "UPDATE transcript_segments SET speaker = ? WHERE segment_id = ?;",
                (speaker, segment_id),
            )
        return cur.rowcount
```

> `_row_to_segment` 已存在（`list_segments` 在用）；`_write_tx` 是现成写事务上下文。

- [ ] **Step 4: 跑测试确认通过**

Run: `/Users/yedianyang/Documents/GitHub/Soundspeed/.venv/bin/python -m pytest backend/tests/test_dal.py -k "get_segment or update_segment_speaker" -v`
Expected: PASS（5 passed）。

- [ ] **Step 5: Commit**

```bash
git add backend/db/dal.py backend/tests/test_dal.py
git commit -m "feat(dal): get_segment + update_segment_speaker（说话人纠正）"
```

---

### Task 2: 端点 — PATCH /takes/{take_id}/segments/{segment_id}

**Files:**
- Modify: `backend/api/routes/takes.py`（`get_take` 端点之后，约 160 行后）
- Test: `backend/tests/test_api.py`

- [ ] **Step 1: 写失败测试**

加到 `backend/tests/test_api.py` 末尾（复用文件顶部 `_TOKEN`、`_make_client`、`create_orchestrator`、`tmp_dal`）：

```python
# ── PATCH /takes/{take_id}/segments/{segment_id} ───────────────────────────

_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


def _seeded(tmp_dal: DAL) -> tuple[int, int, int]:
    """造 scene + take + ch1(有 speaker) + ch2(无 speaker)；返回 (take_id, ch1_seg, ch2_seg)。"""
    sid = tmp_dal.create_scene("scene_patch")
    tid = tmp_dal.start_take(sid, 1, 1000.0)
    ch1 = tmp_dal.insert_segment(tid, 1, "SPEAKER_00", "你好", 0, 16000)
    ch2 = tmp_dal.insert_segment(tid, 2, None, "杂音", 0, 16000)
    return tid, ch1, ch2


def test_patch_segment_speaker_valid(tmp_dal: DAL, monkeypatch) -> None:
    tid, ch1, _ = _seeded(tmp_dal)
    client = _make_client(create_orchestrator(tmp_dal), monkeypatch)
    resp = client.patch(
        f"/api/v1/takes/{tid}/segments/{ch1}", json={"speaker": "SPEAKER_01"}, headers=_AUTH
    )
    assert resp.status_code == 200
    assert resp.json()["speaker"] == "SPEAKER_01"
    assert tmp_dal.get_segment(ch1).speaker == "SPEAKER_01"


def test_patch_segment_speaker_null(tmp_dal: DAL, monkeypatch) -> None:
    tid, ch1, _ = _seeded(tmp_dal)
    client = _make_client(create_orchestrator(tmp_dal), monkeypatch)
    resp = client.patch(
        f"/api/v1/takes/{tid}/segments/{ch1}", json={"speaker": None}, headers=_AUTH
    )
    assert resp.status_code == 200
    assert resp.json()["speaker"] is None
    assert tmp_dal.get_segment(ch1).speaker is None


def test_patch_segment_speaker_blank_422(tmp_dal: DAL, monkeypatch) -> None:
    tid, ch1, _ = _seeded(tmp_dal)
    client = _make_client(create_orchestrator(tmp_dal), monkeypatch)
    resp = client.patch(
        f"/api/v1/takes/{tid}/segments/{ch1}", json={"speaker": "   "}, headers=_AUTH
    )
    assert resp.status_code == 422


def test_patch_segment_missing_404(tmp_dal: DAL, monkeypatch) -> None:
    tid, _, _ = _seeded(tmp_dal)
    client = _make_client(create_orchestrator(tmp_dal), monkeypatch)
    resp = client.patch(
        f"/api/v1/takes/{tid}/segments/99999", json={"speaker": "X"}, headers=_AUTH
    )
    assert resp.status_code == 404


def test_patch_segment_wrong_take_404(tmp_dal: DAL, monkeypatch) -> None:
    tid, ch1, _ = _seeded(tmp_dal)
    other = tmp_dal.start_take(tmp_dal.create_scene("other"), 1, 2000.0)
    client = _make_client(create_orchestrator(tmp_dal), monkeypatch)
    resp = client.patch(
        f"/api/v1/takes/{other}/segments/{ch1}", json={"speaker": "X"}, headers=_AUTH
    )
    assert resp.status_code == 404


def test_patch_segment_take_not_found_404(tmp_dal: DAL, monkeypatch) -> None:
    _, ch1, _ = _seeded(tmp_dal)
    client = _make_client(create_orchestrator(tmp_dal), monkeypatch)
    resp = client.patch(
        f"/api/v1/takes/99999/segments/{ch1}", json={"speaker": "X"}, headers=_AUTH
    )
    assert resp.status_code == 404


def test_patch_segment_ch2_422(tmp_dal: DAL, monkeypatch) -> None:
    tid, _, ch2 = _seeded(tmp_dal)
    client = _make_client(create_orchestrator(tmp_dal), monkeypatch)
    resp = client.patch(
        f"/api/v1/takes/{tid}/segments/{ch2}", json={"speaker": "X"}, headers=_AUTH
    )
    assert resp.status_code == 422


def test_patch_segment_no_token_401(tmp_dal: DAL, monkeypatch) -> None:
    tid, ch1, _ = _seeded(tmp_dal)
    client = _make_client(create_orchestrator(tmp_dal), monkeypatch)
    resp = client.patch(f"/api/v1/takes/{tid}/segments/{ch1}", json={"speaker": "X"})
    assert resp.status_code == 401


def test_patch_segment_does_not_touch_script_diff(tmp_dal: DAL, monkeypatch) -> None:
    tid, ch1, _ = _seeded(tmp_dal)
    tmp_dal.update_take_l2_output(tid, {"script_diff_summary": "原始", "line_matches": [], "corrected_segments": []})
    client = _make_client(create_orchestrator(tmp_dal), monkeypatch)
    client.patch(f"/api/v1/takes/{tid}/segments/{ch1}", json={"speaker": "SPEAKER_09"}, headers=_AUTH)
    detail = client.get(f"/api/v1/takes/{tid}", headers=_AUTH).json()
    assert detail["script_diff"]["script_diff_summary"] == "原始"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/Users/yedianyang/Documents/GitHub/Soundspeed/.venv/bin/python -m pytest backend/tests/test_api.py -k patch_segment -v`
Expected: FAIL（405 Method Not Allowed 或 404，端点不存在）。

- [ ] **Step 3: 实现端点**

在 `backend/api/routes/takes.py` 的 `get_take` 端点之后（约 160 行后）插入：

```python
class PatchSegmentBody(BaseModel):
    """PATCH /takes/{take_id}/segments/{segment_id} 请求体。

    speaker 为必填字段（缺字段 → pydantic 422），值可为 null（置「未知」）。
    """

    speaker: str | None


@router.patch("/takes/{take_id}/segments/{segment_id}")
async def correct_segment_speaker(
    take_id: int,
    segment_id: int,
    body: PatchSegmentBody,
    request: Request,
    _: None = Depends(require_admin),
) -> SegmentOut:
    """纠正单条 segment 的 speaker（说话人归属）。L2 不重跑、不发 WS。

    处理顺序定死边界：空白串 422 → 不存在/不属该 take 404 → ch2 422 → update。
    """
    if isinstance(body.speaker, str) and not body.speaker.strip():
        raise HTTPException(status_code=422, detail="speaker must not be blank")

    dal = request.app.state.orchestrator.dal
    seg = dal.get_segment(segment_id)
    if seg is None or seg.take_id != take_id:
        raise HTTPException(status_code=404, detail="segment not found in take")
    if seg.ch == 2:
        raise HTTPException(status_code=422, detail="ch2 segment speaker is immutable")

    dal.update_segment_speaker(segment_id, body.speaker)
    updated = dal.get_segment(segment_id)
    return SegmentOut.model_validate(updated, from_attributes=True)
```

> `BaseModel` / `HTTPException` / `Depends` / `require_admin` / `SegmentOut` 已在文件顶部 import。

- [ ] **Step 4: 跑测试确认通过**

Run: `/Users/yedianyang/Documents/GitHub/Soundspeed/.venv/bin/python -m pytest backend/tests/test_api.py -k patch_segment -v`
Expected: PASS（9 passed）。

- [ ] **Step 5: 全后端回归**

Run: `/Users/yedianyang/Documents/GitHub/Soundspeed/.venv/bin/python -m pytest backend -q`
Expected: 全绿（既有 236 + 新增 14）。

- [ ] **Step 6: Commit**

```bash
git add backend/api/routes/takes.py backend/tests/test_api.py
git commit -m "feat(api): PATCH 嵌套端点纠正 segment 说话人归属"
```

---

## 前端 lane

> 无测试框架，每个任务以 `cd frontend && pnpm build`（含 `tsc -b` 类型检查）通过为完成标准；可选 `pnpm lint`。手动验收集中在 Task 6 后 + runbook（Task 7）。

### Task 3: constants — 共用 hash helper + speakerDot

**Files:**
- Modify: `frontend/src/lib/constants.ts`（35-56 行，`speakerColor` 那段）

- [ ] **Step 1: 替换 speakerColor 段**

把 `frontend/src/lib/constants.ts` 现有的 `SPEAKER_PALETTE` + `speakerColor`（35-56 行）整段替换为：

```ts
// ---- 实时转录 speaker 分色 ----
// 真实 diarize 输出是 SPEAKER_0x，确定性哈希把任意 speaker 字符串映射到固定调色板，
// 保证同一 speaker 每次渲染同色。text / dot 两套调色板按同一 hash index 取色（不漂移）。
// null（未知 / ch2 无 speaker）→ muted。

const SPEAKER_TEXT_PALETTE = [
  "text-primary",
  "text-secondary-foreground",
  "text-green-600",
  "text-orange-600",
  "text-purple-600",
] as const

const SPEAKER_DOT_PALETTE = [
  "bg-primary",
  "bg-secondary-foreground",
  "bg-green-600",
  "bg-orange-600",
  "bg-purple-600",
] as const

function speakerHashIndex(speaker: string): number {
  let hash = 0
  for (let i = 0; i < speaker.length; i++) {
    hash = (hash * 31 + speaker.charCodeAt(i)) | 0
  }
  return Math.abs(hash) % SPEAKER_TEXT_PALETTE.length
}

export function speakerColor(speaker: string | null): string {
  if (!speaker) return "text-muted-foreground"
  return SPEAKER_TEXT_PALETTE[speakerHashIndex(speaker)]
}

export function speakerDot(speaker: string | null): string {
  if (!speaker) return "bg-muted-foreground"
  return SPEAKER_DOT_PALETTE[speakerHashIndex(speaker)]
}
```

> `SPEAKER_OPTIONS / SPEAKER_TEXT / SPEAKER_DOT`（21-33 行）**保留不动**（settings page 贴名 ticket 用）。

- [ ] **Step 2: 类型检查**

Run: `cd frontend && pnpm build`
Expected: build 成功，0 error。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/constants.ts
git commit -m "feat(frontend): speakerDot + 共用 hash helper（说话人纠正配色）"
```

---

### Task 4: api — correctSegmentSpeaker

**Files:**
- Modify: `frontend/src/lib/api.ts`（REST 段，`endTake` 之后）

- [ ] **Step 1: 加 API 函数**

在 `frontend/src/lib/api.ts` 的 `endTake()` 之后插入：

```ts
// 纠正某条 segment 的 speaker 归属（PATCH，落库）。speaker=null 表示置「未知」。
// null 必须显式带进 body（不能省略字段），后端用它区分「置未知」与「漏传」。
export function correctSegmentSpeaker(
  takeId: number,
  segmentId: number,
  speaker: string | null,
): Promise<TranscriptSegmentDTO> {
  return request<TranscriptSegmentDTO>(
    `/api/v1/takes/${takeId}/segments/${segmentId}`,
    { method: "PATCH", body: JSON.stringify({ speaker }) },
  )
}
```

> 本文件顶部 import 现为 `import type { SceneDTO, ScriptDTO, TakeDTO, TakeDetailDTO } from "@/types/api"`，**补上 `TranscriptSegmentDTO`** → `import type { SceneDTO, ScriptDTO, TakeDTO, TakeDetailDTO, TranscriptSegmentDTO } from "@/types/api"`。

- [ ] **Step 2: 类型检查**

Run: `cd frontend && pnpm build`
Expected: 0 error。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(frontend): correctSegmentSpeaker API 客户端"
```

---

### Task 5: 改造 SpeakerLabel

**Files:**
- Modify: `frontend/src/routes/admin/components/SpeakerLabel.tsx`（整文件重写）

- [ ] **Step 1: 重写组件**

把 `frontend/src/routes/admin/components/SpeakerLabel.tsx` 整文件替换为：

```tsx
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { speakerColor, speakerDot } from "@/lib/constants"
import { cn } from "@/lib/utils"

const UNKNOWN_LABEL = "未知"

// 可切换说话人 label。speaker=null 显示「未知」；options 含 null 代表「未知」候选。
// disabled（ch2）→ 渲染为不可点纯文本。配色统一走 speakerColor/speakerDot 哈希。
export function SpeakerLabel({
  speaker,
  options,
  onChange,
  disabled = false,
}: {
  speaker: string | null
  options: (string | null)[]
  onChange: (speaker: string | null) => void
  disabled?: boolean
}) {
  const label = speaker ?? UNKNOWN_LABEL
  const textColor = speakerColor(speaker)
  const dotColor = speakerDot(speaker)

  if (disabled) {
    return (
      <span className={cn("inline-flex items-center gap-1 font-medium select-none", textColor)}>
        <span className={cn("size-1.5 rounded-full flex-shrink-0", dotColor)} />
        {label}：
      </span>
    )
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <span
          className={cn(
            "inline-flex items-center gap-1 font-medium cursor-pointer select-none rounded-md px-1 -ml-1 transition-colors hover:bg-muted",
            textColor,
          )}
        >
          <span className={cn("size-1.5 rounded-full flex-shrink-0", dotColor)} />
          {label}：
        </span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>切换说话人</DropdownMenuLabel>
        {options.map((opt) => (
          <DropdownMenuItem
            key={opt ?? "__unknown__"}
            className={cn(opt === speaker && "bg-accent")}
            onClick={() => onChange(opt)}
          >
            <span className={cn("size-1.5 rounded-full mr-2", speakerDot(opt))} />
            {opt ?? UNKNOWN_LABEL}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && pnpm build`
Expected: 0 error。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/admin/components/SpeakerLabel.tsx
git commit -m "feat(frontend): SpeakerLabel 改造为 props 化通用切换组件"
```

---

### Task 6: 接进 HistoryTakes（含 correctedTakes 父态 + L2 stale 提示）

**Files:**
- Modify: `frontend/src/routes/admin/components/HistoryTakes.tsx`

- [ ] **Step 1: 改 import + TakeDetail**

`HistoryTakes.tsx` 顶部 import 段补：

```tsx
import { useMemo, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useTake, correctSegmentSpeaker, takeQueryKey } from "@/lib/api"
import type { TakeDTO, TakeStatus, TranscriptSegmentDTO } from "@/types/api"
import { SpeakerLabel } from "./SpeakerLabel"
```

（原文件已 import `useState`/`useTake`/`speakerColor`/`cn` 等，合并去重；`useMemo`、`useQueryClient`、`correctSegmentSpeaker`、`takeQueryKey`、`TranscriptSegmentDTO`、`SpeakerLabel` 为新增。）

把 `TakeDetail` 整个函数替换为：

```tsx
// 展开后的详情：拉 getTake → segments（speaker 可纠正）+ L2 摘要 + line_matches。
function TakeDetail({
  takeId,
  corrected,
  onCorrected,
}: {
  takeId: number
  corrected: boolean
  onCorrected: () => void
}) {
  const { data, isLoading, isError } = useTake(takeId, true)
  const queryClient = useQueryClient()

  // 候选 = 本 take 出现过的 distinct speaker（含当前条自身）+ null（未知）。
  const candidates = useMemo<(string | null)[]>(() => {
    const ids = new Set<string>()
    for (const seg of data?.segments ?? []) {
      if (seg.speaker) ids.add(seg.speaker)
    }
    return [...ids, null]
  }, [data])

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">加载中…</p>
  }
  if (isError || !data) {
    return <p className="text-sm text-destructive">详情加载失败</p>
  }

  const diff = data.script_diff

  async function handleCorrect(seg: TranscriptSegmentDTO, next: string | null) {
    try {
      await correctSegmentSpeaker(takeId, seg.segment_id, next)
      await queryClient.invalidateQueries({ queryKey: takeQueryKey(takeId) })
      onCorrected()
    } catch (err) {
      // 失败：不改本地 UI（以 refetch 为准），仅记录。
      console.error("纠正说话人失败", err)
    }
  }

  return (
    <div className="space-y-3">
      {/* transcript segments：ch1 speaker 可点纠正；ch2 speaker 恒 null，沿用纯文本分支即不显示 label（达成 ch2 不可改，无需 disabled） */}
      {data.segments.length > 0 ? (
        <div className="space-y-1.5">
          {data.segments.map((seg) => (
            <p key={seg.segment_id} className="text-sm">
              {seg.ch === 1 ? (
                <SpeakerLabel
                  speaker={seg.speaker}
                  options={candidates}
                  onChange={(next) => handleCorrect(seg, next)}
                />
              ) : (
                seg.speaker && (
                  <span className={cn("font-medium mr-1", speakerColor(seg.speaker))}>
                    {seg.speaker}：
                  </span>
                )
              )}
              {seg.ch === 1 ? " " : null}
              {seg.text}
            </p>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground/60">无转录片段</p>
      )}

      {/* L2 diff */}
      <div className="flex items-center gap-2">
        <div className="flex-1 h-px bg-border" />
        <span className="text-[10px] text-muted-foreground whitespace-nowrap">L2</span>
        <div className="flex-1 h-px bg-border" />
      </div>
      {corrected && (
        <p className="text-[11px] text-muted-foreground/80">说话人已纠正，剧本分析未更新</p>
      )}
      <ScriptDiffView diff={diff} />
    </div>
  )
}
```

- [ ] **Step 2: 改 HistoryTakes 主体（correctedTakes 父态 + 传 prop）**

在 `HistoryTakes` 函数体内，`expanded` state 旁加：

```tsx
  // 本会话纠正过的 take（放父组件，避免 TakeDetail 折叠 unmount 丢失提示态）。
  const [correctedTakes, setCorrectedTakes] = useState<Set<number>>(new Set())
```

把渲染处的 `<TakeDetail takeId={take.take_id} />` 替换为：

```tsx
                <TakeDetail
                  takeId={take.take_id}
                  corrected={correctedTakes.has(take.take_id)}
                  onCorrected={() =>
                    setCorrectedTakes((prev) => new Set(prev).add(take.take_id))
                  }
                />
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend && pnpm build`
Expected: 0 error。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/admin/components/HistoryTakes.tsx
git commit -m "feat(frontend): take detail 说话人可纠正 + L2 stale 提示"
```

---

### Task 7: runbook 补手动验收节

**Files:**
- Modify: `docs/2026-05-29-1.J-1.L-browser-runbook.md`

- [ ] **Step 1: 追加一节**

在 runbook 末尾追加：

```markdown
## 7. 说话人归属纠正（segment speaker correction）手动验收

前置：后端起好（同 §1）、前端起好、有至少一条含 ch1 转录的 take（按 §4 录一条）。

- [ ] History 展开一条 take，ch1 segment 的 speaker 现在可点（hover 有底色），点开出「切换说话人」菜单。
- [ ] 菜单候选 = 本 take 出现过的 speaker（含当前值，当前值高亮）+「未知」，**不是** SZA/YY/Unknown。
- [ ] 选另一个 speaker → 列表即时更新为新值；刷新页面后仍是新值（已落库）。
- [ ] 选「未知」→ 该条 speaker 变为不显示（PATCH body 是 `null`，非字符串 "未知"）。
- [ ] 纠正后，L2 区上方出现小字「说话人已纠正，剧本分析未更新」；折叠再展开同一 take，提示仍在（本会话持续）；刷新后消失。
- [ ] ch2 segment（若有）的 speaker 不可点。
- [ ] 不同 SPEAKER_XX 颜色稳定区分（哈希分色）。
```

- [ ] **Step 2: Commit**

```bash
git add docs/2026-05-29-1.J-1.L-browser-runbook.md
git commit -m "docs: 说话人纠正手动验收 runbook 节"
```

---

## 联调（两 lane 完成后）

- [ ] 后端起 `SOUNDSPEED_DEV=1 ADMIN_TOKEN=devtoken SOUNDSPEED_DB=./soundspeed_dev.db GEMMA_MODEL_PATH=<主仓库 gguf> /Users/yedianyang/Documents/GitHub/Soundspeed/.venv/bin/python -m backend.api`；前端 `pnpm dev`。
- [ ] 按 runbook §7 全过一遍。
- [ ] `codex:review` 把关（工作树 diff）。

---

## 备注

- Commit 时机：仓库走 feat 攒批工作流，上面每任务 commit 可按需合并；commit message 结尾按仓库惯例可加 `Co-Authored-By` 行。
- `SpeakerLabel` 的 `disabled` prop 按 spec §3.2 保留（只读场景能力）；本接入 ch2 靠「不渲染 label」达成 gate，未传 disabled。
- 不碰：`LiveTranscript`、WS/ASR 链路、settings page 贴名配置、L2 重跑。
