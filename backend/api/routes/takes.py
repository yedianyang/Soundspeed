"""take 端点（contract C3）：POST /api/v1/take/start | /api/v1/take/end + GET 端点（1.J-1.L）。

请求契约（design §3）：
  - POST /take/start body = {scene_id:int, shot:str|null}
  - POST /take/end   body 空（end_ts 服务端生成）
  - ts 一律服务端 time.time() 生成
  - 映射到 TakeStartPayload(scene_id, shot, start_ts) / TakeEndPayload(end_ts)

端点必须 async def（决策 1）：
  take.end 的 fire-and-forget L2 靠 asyncio.get_running_loop()
  （orchestrator.py，仅在注入 llm_service/l2_runner 时走到）。async 端点由
  FastAPI 在 event loop 内直接 await，get_running_loop() 能拿到 loop，L2 task 被调度。
  若改成 def，FastAPI 丢线程池跑 → 无 running loop → get_running_loop() 抛 RuntimeError
  → 被 publish() 吞掉 → 生产 L2 静默不触发。
  GET 端点同理：DAL 单连接 check_same_thread=False，同步路由跑线程池会与 event-loop 线程
  上的 L2 async 任务并发操作同一 _conn（SQLite 对同一连接对象的并发调用不安全）。

本切片不做 take/end 的 NP Pipeline（architecture §10.1 「L2+NP」中的 NP 属后续 ticket）。

TakeDTO 有意省略 performer_issues / audio_quality（codex P2）：
  这两个字段属 NP Pipeline 输出，1.J-1.L 不暴露，故意不进 DTO。
  前端从 script_diff.line_matches 读行比对（codex P1），不单列 line_matches 字段。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from backend.api.auth import require_admin
from backend.core.events import TAKE_END, TAKE_START, TakeEndPayload, TakeStartPayload

router = APIRouter(prefix="/api/v1", tags=["takes"])


# ── DTO 定义（1.J-1.L GET 端点响应结构）──────────────────────────────────────


class SegmentOut(BaseModel):
    """transcript_segments 行的响应投影（spec §2.1 shape）。

    有意省略 take_id：前端 TranscriptSegmentDTO 不含此字段，减少响应体冗余。
    """

    segment_id: int
    ch: int
    speaker: str | None
    text: str
    start_frame: int
    end_frame: int

    model_config = ConfigDict(from_attributes=True)


class TakeDTO(BaseModel):
    """take 行的响应投影（11 字段，有意省略 performer_issues / audio_quality）。

    有意省略字段：performer_issues、audio_quality 属 NP Pipeline 输出，1.J-1.L 不暴露。
    前端从 script_diff.line_matches 读行比对（codex P1），不单列 line_matches 字段。
    """

    take_id: int
    scene_id: int
    take_number: int
    shot: str | None
    start_ts: float
    end_ts: float | None
    status: str
    script_diff: dict | None
    notes: str | None
    created_at: float
    updated_at: float
    # diarization 回填后的结构化转录（ASR + speaker 整合，v4）；未回填时为 None
    structured_transcript: dict | None = None

    model_config = ConfigDict(from_attributes=True)


class TakeDetailDTO(TakeDTO):
    """take 详情：TakeDTO + segments 列表。"""

    segments: list[SegmentOut]


# ── POST 端点（contract C3）────────────────────────────────────────────────────


class TakeStartBody(BaseModel):
    """POST /take/start 请求体。"""

    scene_id: int
    shot: str | None = None
    # 本 take 在场的已注册演员 id（diarization 回填只在这些演员里匹配；空 → 全匿名说话人N）
    speaker_ids: list[int] = []


@router.post("/take/start")
async def take_start(
    body: TakeStartBody,
    request: Request,
    _: None = Depends(require_admin),
) -> dict[str, str]:
    """起一个 take：服务端生成 start_ts，publish TAKE_START。"""
    orchestrator = request.app.state.orchestrator
    payload = TakeStartPayload(
        scene_id=body.scene_id,
        shot=body.shot,
        start_ts=time.time(),
        speaker_ids=tuple(body.speaker_ids),
    )
    orchestrator.publish(TAKE_START, payload)
    return {"status": "ok"}


@router.post("/take/end")
async def take_end(
    request: Request,
    _: None = Depends(require_admin),
) -> dict[str, str]:
    """结束当前 take：服务端生成 end_ts，publish TAKE_END（body 空）。

    async def 是决策 1 的硬约束——见模块 docstring。
    """
    orchestrator = request.app.state.orchestrator
    payload = TakeEndPayload(end_ts=time.time())
    orchestrator.publish(TAKE_END, payload)
    return {"status": "ok"}


# ── GET 端点（1.J-1.L）────────────────────────────────────────────────────────


@router.get("/takes")
async def list_takes(
    request: Request,
    scene_id: int | None = None,
    _: None = Depends(require_admin),
) -> dict[str, list[TakeDTO]]:
    """列出 take（可按 scene_id 过滤），不含 segments（避免 N+1）。

    省略 scene_id 返回全部（dal.list_takes(None)）。
    """
    dal = request.app.state.orchestrator.dal
    takes = dal.list_takes(scene_id)
    return {"takes": [TakeDTO.model_validate(t, from_attributes=True) for t in takes]}


@router.get("/takes/{take_id}")
async def get_take(
    take_id: int,
    request: Request,
    _: None = Depends(require_admin),
) -> TakeDetailDTO:
    """返回 take 详情（含 segments），take 不存在 → 404。"""
    dal = request.app.state.orchestrator.dal
    take = dal.get_take(take_id)
    if take is None:
        raise HTTPException(status_code=404, detail="take not found")
    segments = dal.list_segments(take_id)
    return TakeDetailDTO.model_validate(
        {**take.__dict__, "segments": segments},
        from_attributes=True,
    )


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


@router.get("/scenes")
async def list_scenes(
    request: Request,
    _: None = Depends(require_admin),
) -> dict[str, list[dict]]:
    """列出所有场次（含 slugline 三列：int_ext / time_of_day / location）。

    dal.list_scenes() 返回 list[dict]，直接透传（不需要 DTO 投影）。
    """
    dal = request.app.state.orchestrator.dal
    return {"scenes": dal.list_scenes()}


# ── 读剧本端点（scene heading 票加，2026-06-01）────────────────────────────────


class ScriptLineOut(BaseModel):
    """剧本行投影（spec §2.3）。"""

    line_no: int
    character: str | None
    text: str


class ScriptOut(BaseModel):
    """单场次剧本响应（spec §2.3）。"""

    script_id: int
    version: int
    lines: list[ScriptLineOut]


@router.get("/scenes/{scene_id}/script")
async def get_scene_script(
    scene_id: int,
    request: Request,
    _: None = Depends(require_admin),
) -> dict[str, ScriptOut | None]:
    """取指定场次最新剧本及行列表。

    无剧本或 scene 不存在 → {"script": null}（200，不 404）。
    行按 line_no 升序（dal.list_script_lines 已保证）。
    """
    dal = request.app.state.orchestrator.dal
    script_meta = dal.get_latest_script(scene_id)
    if script_meta is None:
        return {"script": None}
    script_id = script_meta["script_id"]
    version = script_meta["version"]
    raw_lines = dal.list_script_lines(script_id)
    lines = [ScriptLineOut(line_no=r["line_no"], character=r["character"], text=r["text"]) for r in raw_lines]
    return {"script": ScriptOut(script_id=script_id, version=version, lines=lines)}
