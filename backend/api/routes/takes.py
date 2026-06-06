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

take/end 的 NP Pipeline 已接入（4.x）：POST /notes 触发 NP，归置 note 到对应 take。

TakeDTO 有意省略 performer_issues / audio_quality（codex P2）：
  这两个字段属 NP Pipeline 输出，1.J-1.L 不暴露，故意不进 DTO。
  前端从 script_diff.line_matches 读行比对（codex P1），不单列 line_matches 字段。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import require_admin
from backend.api.routes.query import schedule_qp_broadcast
from backend.core.events import (
    SCENE_CHANGED,
    TAKE_CHANGED,
    TAKE_DELETED,
    TAKE_END,
    TAKE_START,
    SceneChangedPayload,
    TakeChangedPayload,
    TakeDeletedPayload,
    TakeEndPayload,
    TakeStartPayload,
)
from backend.pipelines.memo_route import classify_memo
from backend.pipelines.note_parse import NoteParseError, parse_note
from backend.pipelines.voice_dispatch import run_voice_dispatch

logger = logging.getLogger(__name__)

# voice dispatch fire-and-forget task 持有集：防 asyncio.create_task 结果被 GC（Python 文档建议）。
# 对齐 query.py 的 _qp_tasks 模式。
_voice_dispatch_tasks: set[asyncio.Task] = set()

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
    """take 行的响应投影（12 字段，有意省略 performer_issues / audio_quality）。

    有意省略字段：performer_issues、audio_quality 属 NP Pipeline 输出，1.J-1.L 不暴露。
    前端从 script_diff.line_matches 读行比对（codex P1），不单列 line_matches 字段。
    take_suffix：冲突后缀，默认 ''；显示时前端拼接为 'Take 3+'，后端只存值。
    deleted_at：软删时间戳，NULL 表示未删除；restore 后返回 None。
    """

    take_id: int
    scene_id: int
    shot: str | None
    take_number: int
    take_suffix: str
    start_ts: float
    end_ts: float | None
    status: str
    script_diff: dict | None
    notes: str | None
    deleted_at: float | None
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
    # 用户手动指定的待录 take 号（底部 Take 弹窗）。None → 后端按 (scene,shot) 自动 MAX+1。
    # ge=1：take 号从 1 起，挡掉 0/负数。
    take_number: int | None = Field(default=None, ge=1)


@router.post("/take/start")
async def take_start(
    body: TakeStartBody,
    request: Request,
    _: None = Depends(require_admin),
) -> dict[str, str]:
    """起一个 take：服务端生成 start_ts，publish TAKE_START。

    校验 scene_id == get_active_scene_id()，不一致 → 409 scene_not_active。
    校验必须在路由层同步执行，不能推进 orchestrator 异步 handler。
    """
    orchestrator = request.app.state.orchestrator
    dal = orchestrator.dal
    active_scene_id = dal.get_active_scene_id()
    if body.scene_id != active_scene_id:
        raise HTTPException(
            status_code=409,
            detail={"error": "scene_not_active", "active_scene_id": active_scene_id},
        )
    payload = TakeStartPayload(
        scene_id=body.scene_id,
        shot=body.shot,
        start_ts=time.time(),
        speaker_ids=tuple(body.speaker_ids),
        take_number=body.take_number,
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


# ── Note 端点（4.C）────────────────────────────────────────────────────────────


class NoteCreateBody(BaseModel):
    """POST /notes 请求体。"""

    text: str
    ts: float | None = None
    # 前端生成的乐观 pending 去重键（crypto.randomUUID），原样回传到 note.processed。
    client_id: str | None = None
    # WS 连接标识：query 分支据此把答案广播到 qp.answer.{conn_id}（前端按前缀认领）。
    conn_id: str | None = None


class NoteOut(BaseModel):
    """单条 note 事件响应投影。"""

    event_id: int
    take_id: int
    scene_code: str | None
    take_number: int | None
    category: str
    content: str
    raw_text: str
    ts: float


class NoteListOut(BaseModel):
    """GET /takes/{take_id}/notes 响应。"""

    take_id: int
    notes_aggregated: str | None
    events: list[NoteOut]


@router.post("/notes", status_code=202)
async def create_note(
    body: NoteCreateBody,
    request: Request,
    _: None = Depends(require_admin),
) -> dict:
    """提交 note：解析 @category → fire-and-forget NP Pipeline → 返回 202。

    NP Pipeline 在后台通过 LLM 判断归属 take 并写库。
    """
    orchestrator = request.app.state.orchestrator
    ts = body.ts or time.time()

    # 1. 规则解析（只提取 @category，不定位 take）
    try:
        note = parse_note(body.text, ts)
    except NoteParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 入口调度器（块③）：query → QP fire-and-forget 广播；note/任何失败 → 现有 NP。
    # 仅当有 conn_id（query 答案要广播到 qp.answer.{conn_id}）且 LLM 就绪、且非 @显式类别时分类。
    # classify 有意留在 202 关键路径（route 须在 202 时知道 kind 才能告诉前端 query/note，
    # 决定要不要撤掉乐观 pending）；其延迟由前端乐观 pending 提前到 await 前藏掉，故后端保持简单。
    service = getattr(request.app.state, "llm_service", None)
    if service is not None and body.conn_id and not body.text.lstrip().startswith("@"):
        kind = await classify_memo(note.raw_text, service)
        if kind == "query":
            schedule_qp_broadcast(
                note.raw_text,
                body.conn_id,
                dal=orchestrator.dal,
                service=service,
                cm=request.app.state.connection_manager,
            )
            return {"status": "processing", "kind": "query"}

    # 2. fire-and-forget NP Pipeline
    try:
        orchestrator.run_np_async(
            raw_text=note.raw_text,
            parsed_category=note.category,
            ts=note.ts,
            client_id=body.client_id,
        )
    except RuntimeError:
        # 不在 event loop 中（如同步测试环境），fallback 到当前活跃 take。
        # 端点是 async def → 生产恒在 loop 内，此路不可达；若哪天被改成 def 触发，
        # 这里会绕过整条 NP（无 LLM 归类、无 take 消歧、无 note.processed WS），故吼一声。
        logger.warning(
            "create_note fallback 命中：无 event loop，绕过 NP 直接落当前活跃 take（仅应出现在同步测试）"
        )
        dal = request.app.state.orchestrator.dal
        session = request.app.state.orchestrator.session
        if session.take_active and session.take_id is not None:
            event_id = dal.insert_note(
                take_id=session.take_id,
                category=note.category,
                content=note.content,
                raw_text=note.raw_text,
                ts=note.ts,
            )
            return {
                "status": "ok",
                "event_id": event_id,
                "take_id": session.take_id,
                "category": note.category,
                "content": note.content,
            }
        raise HTTPException(status_code=409, detail="no active take and no event loop for NP Pipeline")

    return {
        "status": "processing",
        "category": note.category,
        "content": note.content,
    }


# 后端语音上限：48kHz/30s mono PCM16 ≈ 2.9MB，给到 10MB 安全冗余（前端另有 30s/2MB 限制，§3.2）。
_MAX_VOICE_BYTES = 10 * 1024 * 1024


@router.post("/notes/voice", status_code=202)
async def create_voice_note(
    request: Request,
    file: UploadFile = File(...),
    client_id: str = Form(...),
    ts: float | None = Form(None),
    conn_id: str | None = Form(None),
    _: None = Depends(require_admin),
) -> dict:
    """提交语音 note（4.K / C1）：浏览器麦 WAV 直传 → fire-and-forget → 返回 202。

    有 conn_id → 走 voice dispatch（hop A/B 两步走判 note/query，query 广播 qp.answer.{conn_id}）。
    无 conn_id → 原有 run_np_voice_async 路径（只归置 NP，不判 query）。

    与 POST /notes（文本）对称：均 202，结果经 WS（note.processed / note.failed）回灌。
    音频来源是前端浏览器麦（getUserMedia，#19），非后端现场录音。
    """
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="音频为空")
    if len(audio) > _MAX_VOICE_BYTES:
        raise HTTPException(status_code=400, detail="音频超限（>10MB）")

    orchestrator = request.app.state.orchestrator
    resolved_ts = ts if ts is not None else time.time()
    service = getattr(request.app.state, "llm_service", None)

    # 有 conn_id → 走 voice dispatch（判 note/query）
    if conn_id is not None and service is not None:
        # 构建 NPInput（含 current_take_id/take_context）供 note 分支委托 run_np_voice
        np_input = orchestrator._build_np_input("", "note", resolved_ts)
        # voice_runner 来自 orchestrator._deps（须有 llm_service 才绑定）
        voice_runner = getattr(orchestrator._deps, "voice_runner", None)
        # fire-and-forget：不 await，不阻塞 202，对齐块③ schedule_qp_broadcast 模式。
        # asyncio.create_task（async 函数内直接调用）等价 get_running_loop().create_task。
        # _voice_dispatch_tasks 持有 task 引用防 GC（对齐 query.py _qp_tasks 模式）。
        task = asyncio.create_task(
            run_voice_dispatch(
                audio,
                conn_id=conn_id,
                ts=resolved_ts,
                client_id=client_id,
                dal=orchestrator.dal,
                service=service,
                cm=request.app.state.connection_manager,
                scene_context="",  # 简化：不预取场次文本，hop A 系统内联说明已足
                np_input=np_input,
                voice_runner=voice_runner,
            )
        )
        _voice_dispatch_tasks.add(task)

        def _done(t: asyncio.Task) -> None:
            _voice_dispatch_tasks.discard(t)
            exc = None if t.cancelled() else t.exception()
            if exc is not None:
                logger.warning("voice dispatch task 异常: %r", exc)

        task.add_done_callback(_done)
        return {"status": "processing", "client_id": client_id, "kind": "dispatching"}

    # 无 conn_id → 原有 run_np_voice_async 路径
    try:
        orchestrator.run_np_voice_async(
            audio=audio,
            ts=resolved_ts,
            client_id=client_id,
        )
    except RuntimeError:
        # 不在 event loop 中（如同步测试环境）：语音必须经多模态推理，无直接落库 fallback。
        raise HTTPException(status_code=503, detail="语音 NP 需在 event loop 中运行")

    return {"status": "processing", "client_id": client_id}


@router.get("/takes/{take_id}/notes")
async def get_take_notes(
    take_id: int,
    request: Request,
    _: None = Depends(require_admin),
) -> NoteListOut:
    """返回指定 take 的 note 汇总 + 事件列表。"""
    dal = request.app.state.orchestrator.dal

    take = dal.get_take(take_id)
    if take is None:
        raise HTTPException(status_code=404, detail="take not found")

    events = dal.list_notes(take_id)

    # 查 scene_code + take_number
    scene_code: str | None = None
    if take:
        scenes = dal.list_scenes()
        for s in scenes:
            if s["scene_id"] == take.scene_id:
                scene_code = s.get("scene_code")
                break
    take_number: int | None = take.take_number if take else None

    events_out: list[NoteOut] = []
    for evt in events:
        payload: dict = evt.payload if isinstance(evt.payload, dict) else {}
        cat = payload.get("category", "note")
        ct = payload.get("content", "")
        raw = payload.get("raw_text", "")
        if not (isinstance(cat, str) and isinstance(ct, str) and isinstance(raw, str)):
            cat = str(cat) if cat else "note"
            ct = str(ct) if ct else ""
            raw = str(raw) if raw else ""
        events_out.append(
            NoteOut(
                event_id=evt.event_id,
                take_id=evt.take_id,
                scene_code=scene_code,
                take_number=take_number,
                category=cat,
                content=ct,
                raw_text=raw,
                ts=evt.ts,
            )
        )

    return NoteListOut(
        take_id=take_id,
        notes_aggregated=take.notes if take else None,
        events=events_out,
    )


# ── 2.C：建场端点 ────────────────────────────────────────────────────────────


class CreateSceneBody(BaseModel):
    """POST /scenes 请求体。"""

    scene_code: str
    description: str | None = None
    shoot_date: str | None = None
    int_ext: str | None = None
    time_of_day: str | None = None
    location: str | None = None


@router.post("/scenes")
async def create_scene(
    body: CreateSceneBody,
    request: Request,
    _: None = Depends(require_admin),
) -> dict:
    """建场（get-or-create）：新建或复用已有 scene_code，均返回 200。

    created=True 表示本次新建，False 表示复用。is_active 反映 DB 当前状态。
    """
    dal = request.app.state.orchestrator.dal
    scene_id, created = dal.get_or_create_scene(
        body.scene_code,
        description=body.description,
        shoot_date=body.shoot_date,
        int_ext=body.int_ext,
        time_of_day=body.time_of_day,
        location=body.location,
    )
    active_id = dal.get_active_scene_id()
    return {
        "scene_id": scene_id,
        "scene_code": body.scene_code,
        "created": created,
        "is_active": scene_id == active_id,
    }


# ── 2.C：激活场次端点 ─────────────────────────────────────────────────────────


@router.post("/scenes/{scene_id}/activate")
async def activate_scene(
    scene_id: int,
    request: Request,
    _: None = Depends(require_admin),
) -> dict:
    """激活指定场次。

    步骤：录制中检查 → 404 → set_active_scene → 刷新 session → publish SCENE_CHANGED。
    """
    orchestrator = request.app.state.orchestrator
    dal = orchestrator.dal
    session = orchestrator.session

    # 录制中禁止切场（全局状态，用 session.take_active）
    if session.take_active:
        raise HTTPException(
            status_code=409,
            detail={"error": "take_in_progress"},
        )

    # 查 scene 是否存在（用 list_scenes 找，顺便取 scene_code）
    scenes = dal.list_scenes()
    target = next((s for s in scenes if s["scene_id"] == scene_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="scene not found")

    # 写 DB + 刷新内存
    dal.set_active_scene(scene_id)
    session.activate_scene(scene_id)

    # publish WS 事件
    orchestrator.publish(
        SCENE_CHANGED,
        SceneChangedPayload(
            scene_id=scene_id,
            scene_code=target["scene_code"],
            is_active=True,
        ),
    )

    return {"scene_id": scene_id, "scene_code": target["scene_code"]}


# ── 2.C：PATCH /takes/{take_id} ──────────────────────────────────────────────


class PatchTakeBody(BaseModel):
    """PATCH /takes/{take_id} 请求体（所有字段可选）。"""

    status: Literal["pass", "ng", "keep", "tbd"] | None = None
    shot: str | None = None
    scene_id: int | None = None
    take_number: int | None = None
    notes: str | None = None


@router.patch("/takes/{take_id}")
async def patch_take(
    take_id: int,
    body: PatchTakeBody,
    request: Request,
    _: None = Depends(require_admin),
) -> TakeDTO:
    """部分更新 take 元数据。

    录制中（end_ts IS NULL）：禁改 scene_id/take_number→409；允许改 notes。
    冲突处理：TakeNumberConflictError→409；目标 scene 不存在→404；take 不存在→404。
    成功后 publish TAKE_CHANGED，返回更新后的 TakeDTO。
    """
    orchestrator = request.app.state.orchestrator
    dal = orchestrator.dal

    # take 存在性检查
    take = dal.get_take(take_id)
    if take is None:
        raise HTTPException(status_code=404, detail="take not found")

    # 录制中限制（行级判断：end_ts IS NULL）
    is_recording = take.end_ts is None
    if is_recording and (body.scene_id is not None or body.take_number is not None):
        raise HTTPException(
            status_code=409,
            detail={"error": "take_in_progress"},
        )

    # 先处理 status（走 set_take_status，写 manual.mark 事件）
    if body.status is not None:
        dal.set_take_status(take_id, body.status)

    # 再处理其余字段（走 update_take_meta）
    # 冲突处理改为后缀追加，不再抛 TakeNumberConflictError（不再 409）
    has_meta_update = any(
        v is not None for v in (body.shot, body.scene_id, body.take_number, body.notes)
    )
    if has_meta_update:
        try:
            dal.update_take_meta(
                take_id,
                shot=body.shot,
                scene_id=body.scene_id,
                take_number=body.take_number,
                notes=body.notes,
            )
        except ValueError:
            raise HTTPException(status_code=404, detail="target scene not found")

    # 重新取（update 可能改了字段）
    updated = dal.get_take(take_id)
    assert updated is not None  # take 存在性已确认

    # publish TAKE_CHANGED
    orchestrator.publish(
        TAKE_CHANGED,
        TakeChangedPayload(
            take_id=updated.take_id,
            scene_id=updated.scene_id,
            take_number=updated.take_number,
            status=updated.status,
            script_diff=updated.script_diff,
        ),
    )

    return TakeDTO.model_validate(updated, from_attributes=True)


# ── 2.C：DELETE /takes/{take_id}（软删）─────────────────────────────────────


@router.delete("/takes/{take_id}", status_code=204)
async def delete_take(
    take_id: int,
    request: Request,
    _: None = Depends(require_admin),
) -> None:
    """软删 take。录制中→409；不存在（含已软删）→404；成功→204。

    子表数据保留（不触发 CASCADE），可通过 restore 端点撤销。
    删除后 publish TAKE_DELETED。
    """
    orchestrator = request.app.state.orchestrator
    dal = orchestrator.dal

    # 存在性检查（get_take 排除软删行）
    take = dal.get_take(take_id)
    if take is None:
        raise HTTPException(status_code=404, detail="take not found")

    # 录制中禁删（行级判断）
    if take.end_ts is None:
        raise HTTPException(
            status_code=409,
            detail={"error": "take_in_progress"},
        )

    scene_id = take.scene_id
    dal.delete_take(take_id)

    orchestrator.publish(
        TAKE_DELETED,
        TakeDeletedPayload(take_id=take_id, scene_id=scene_id),
    )


# ── 2.C：POST /takes/{take_id}/restore ───────────────────────────────────────


@router.post("/takes/{take_id}/restore")
async def restore_take(
    take_id: int,
    request: Request,
    _: None = Depends(require_admin),
) -> TakeDTO:
    """撤销软删。

    仅对已软删（deleted_at IS NOT NULL）的 take 有效；
    未软删或不存在→404；成功→200 + 恢复后的 TakeDTO。
    """
    orchestrator = request.app.state.orchestrator
    dal = orchestrator.dal

    # 查含软删的 take
    take = dal.get_take_any(take_id)
    if take is None:
        raise HTTPException(status_code=404, detail="take not found")
    if take.deleted_at is None:
        raise HTTPException(status_code=404, detail="take not deleted")

    dal.restore_take(take_id)

    restored = dal.get_take(take_id)
    assert restored is not None  # restore 后应可见
    return TakeDTO.model_validate(restored, from_attributes=True)
