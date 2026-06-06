"""3.D：剧本导入端点（上传 → 解析 → 分场 → 入库）。

端点（spec §7）：
  POST /api/v1/scripts/upload          上传文件主入口（multipart）。
  POST /api/v1/scripts/import/confirm  重复场确认替换（阶段 2）。

数据流（上传）：
  file bytes
    → script_extract.extract_text          （3.F，按扩展名提取纯文本）
    → sp_script.run_sp_parse               （3.B，Gemma 分块分场 → ParsedScene[]）
    → script_import.plan_import            （3.C 阶段 1，只读分类，零写）
    → 无重复 → apply_import 直接入库 → {status:"imported"}
       有重复 → 不写 → {status:"needs_confirmation", plan, conflicts}

依赖注入（沿用 takes.py 模式）：
  dal         = request.app.state.orchestrator.dal
  llm_service = request.app.state.llm_service
  鉴权        = Depends(require_admin)

target 取值（spec §6）：
  multi_scene   （默认，上传整本）：解析器自动分场，按 scene_code 建/匹配场。
  current_scene （粘贴到当前场）：所有场 lines 合并成 active scene 的一个新版本。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from backend.api.auth import require_admin
from backend.core.script_extract import (
    SUPPORTED_EXTENSIONS,
    ExtractError,
    UnsupportedFormatError,
    extract_text,
)
from backend.core.script_import import (
    ImportPlan,
    NoActiveSceneError,
    apply_import,
    import_single_scene,
    plan_import,
)
from backend.pipelines.sp_script import SPParseError, parse_scene_block, split_for_parse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scripts", tags=["scripts"])

# 上传体量上限（10 MB）：剧本纯文本/文档远小于此，挡住误传大文件。
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


# ── 响应/请求模型 ────────────────────────────────────────────────────────────


class ImportedSceneOut(BaseModel):
    """单个已入库场次的结果投影（供前端展示「创建成了什么」）。"""

    scene_id: int
    script_id: int
    scene_code: str | None
    int_ext: str | None
    time_of_day: str | None
    location: str | None
    line_count: int
    lines: list[dict]  # [{line_no, character, text}]


class ConflictOut(BaseModel):
    """重复场冲突项（前端做左右 diff）。"""

    scene_id: int
    scene_code: str | None
    original: dict  # {raw_text, lines}
    incoming: dict  # {raw_text, lines}


class UploadResult(BaseModel):
    """upload / confirm 统一响应。

    status="imported"           → scenes 填充，conflicts/plan 为空。
    status="needs_confirmation" → conflicts + plan 填充，scenes 为空。
    """

    status: Literal["imported", "needs_confirmation"]
    scenes: list[ImportedSceneOut] = []
    conflicts: list[ConflictOut] = []
    plan: dict | None = None  # needs_confirmation 时回传，confirm 原样带回


class ScriptUploadInfo(BaseModel):
    """一条上传记录的元信息（不含 raw_text）。"""

    upload_id: int
    filename: str
    char_count: int
    status: str  # uploaded | parsing | parsed | error
    detail: str | None = None
    created_at: float
    updated_at: float


class UploadSavedResult(BaseModel):
    """/upload 响应：只表示「已存入 DB」，不含解析结果。"""

    upload_id: int
    filename: str
    char_count: int
    status: str  # "uploaded"


class SceneDecision(BaseModel):
    scene_id: int
    action: Literal["replace", "skip"]


class ConfirmBody(BaseModel):
    plan: dict  # upload 阶段返回的 plan（原样回传，解析只发生一次）
    decisions: list[SceneDecision] = []


# ── 内部辅助 ─────────────────────────────────────────────────────────────────


def _build_imported_scenes(results: list[tuple[int, int]], dal) -> list[ImportedSceneOut]:
    """根据 apply_import 返回的 (scene_id, script_id)，回读 DB 构造展示结果。

    回读而非用 plan：保证返回的是真正落库的内容（含 DB 分配的 line_no）。
    """
    scene_meta = {s["scene_id"]: s for s in dal.list_scenes()}
    out: list[ImportedSceneOut] = []
    for scene_id, script_id in results:
        meta = scene_meta.get(scene_id, {})
        lines = dal.list_script_lines(script_id)
        out.append(
            ImportedSceneOut(
                scene_id=scene_id,
                script_id=script_id,
                scene_code=meta.get("scene_code"),
                int_ext=meta.get("int_ext"),
                time_of_day=meta.get("time_of_day"),
                location=meta.get("location"),
                line_count=len(lines),
                lines=[
                    {
                        "line_no": ln["line_no"],
                        "character": ln["character"],
                        "text": ln["text"],
                    }
                    for ln in lines
                ],
            )
        )
    return out


def _plan_to_dict(plan: ImportPlan) -> dict:
    """ImportPlan → JSON 可序列化 dict（confirm 原样回传，解析只发生一次）。"""
    return {
        "target": plan.target,
        "new_scenes": plan.new_scenes,
        "conflicts": plan.conflicts,
    }


def _plan_from_dict(data: dict) -> ImportPlan:
    """confirm body 的 plan dict → ImportPlan（JSON 把 tuple 转成 list，apply 解包兼容）。"""
    return ImportPlan(
        target=data.get("target", "multi_scene"),
        new_scenes=data.get("new_scenes", []),
        conflicts=data.get("conflicts", []),
    )


# ── 端点 ─────────────────────────────────────────────────────────────────────


@router.post("/upload", response_model=UploadSavedResult)
async def upload_script(
    request: Request,
    file: UploadFile = File(...),
    _: None = Depends(require_admin),
) -> UploadSavedResult:
    """阶段 1（上传）：提取文本 + 存入 DB。**不碰 Gemma**，秒回、永不超时。

    解析分场是独立的一步（POST /uploads/{id}/parse），见 spec：上传≠解析。
    """
    dal = request.app.state.orchestrator.dal

    # 读字节 + 体量保护
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件过大（上限 10 MB）")

    # 提取纯文本（3.F）
    filename = file.filename or "未命名"
    try:
        raw_text = extract_text(filename, data)
    except UnsupportedFormatError as exc:
        raise HTTPException(
            415, f"{exc}（支持：{', '.join(sorted(SUPPORTED_EXTENSIONS))}）"
        ) from exc
    except ExtractError as exc:
        raise HTTPException(422, str(exc)) from exc

    if not raw_text.strip():
        raise HTTPException(422, "文件无文本内容")

    upload_id = dal.insert_script_upload(filename, raw_text)
    logger.info("剧本上传：%s 提取 %d 字，已存 upload_id=%d", filename, len(raw_text), upload_id)
    return UploadSavedResult(
        upload_id=upload_id,
        filename=filename,
        char_count=len(raw_text),
        status="uploaded",
    )


@router.get("/uploads", response_model=list[ScriptUploadInfo])
async def list_uploads(
    request: Request,
    _: None = Depends(require_admin),
) -> list[ScriptUploadInfo]:
    """列出所有上传记录（含解析状态），最新在前。"""
    dal = request.app.state.orchestrator.dal
    return [ScriptUploadInfo(**u) for u in dal.list_script_uploads()]


async def run_parse_job(
    dal,
    llm_service,
    upload_id: int,
    raw_text: str,
    target: Literal["multi_scene", "current_scene"],
) -> None:
    """后台解析任务：瞬时切场 → 逐场 Gemma 结构化 → 每场即时入库（前端实时刷出）。

    进度写进 script_uploads.detail（"解析中 i/N 场…"）；逐场失败只跳过该场、不中断整本；
    全程异常兜底为 error（不抛给调用方）。
    multi_scene：逐场增量入库（import_single_scene，全局合成 code 保证无号场唯一）。
    current_scene：合并成一版需全量 → plan_import 批量。
    """
    try:
        blocks = split_for_parse(raw_text)
        total = len(blocks)
        if total == 0:
            dal.update_script_upload_status(upload_id, "error", "清洗后无有效内容")
            return

        if target == "current_scene":
            await _run_parse_batch(dal, llm_service, upload_id, blocks, total)
            return

        # multi_scene：逐场解析 → 逐场入库
        batch_id = uuid.uuid4().hex[:8]
        imported = skipped = synthetic_n = 0
        for i, block in enumerate(blocks, 1):
            dal.update_script_upload_status(upload_id, "parsing", f"解析中 {i}/{total} 场…")
            t0 = time.monotonic()
            try:
                scenes = await parse_scene_block(block, llm_service, timeout=180.0)
            except (SPParseError, TimeoutError) as exc:
                logger.warning("场块 %d/%d 解析失败（跳过）：%s", i, total, exc)
                continue
            logger.info(
                "场块 %d/%d 解析耗时 %.1fs（输入 %d 字，得 %d 场）",
                i, total, time.monotonic() - t0, len(block), len(scenes),
            )
            for scene in scenes:
                synth = f"import:{batch_id}:{synthetic_n}"
                if scene.scene_code is None:
                    synthetic_n += 1
                res = import_single_scene(
                    scene, target="multi_scene", synthetic_code=synth, dal=dal
                )
                if res is None:
                    skipped += 1
                else:
                    imported += 1

        if imported == 0 and skipped == 0:
            dal.update_script_upload_status(upload_id, "error", "未解析出任何场次")
            return
        detail = f"已导入 {imported} 场" + (f"，{skipped} 场跳过" if skipped else "")
        dal.update_script_upload_status(upload_id, "parsed", detail)
        logger.info("剧本解析完成 upload_id=%d：%s", upload_id, detail)
    except NoActiveSceneError:
        dal.update_script_upload_status(upload_id, "error", "无活跃场次，无法导入到当前场")
    except Exception as exc:  # 兜底：后台任务异常不得静默丢失
        logger.exception("解析任务异常 upload_id=%d", upload_id)
        dal.update_script_upload_status(upload_id, "error", f"解析异常：{exc}")


async def _run_parse_batch(dal, llm_service, upload_id, blocks, total) -> None:
    """current_scene 路径：解析全部场（带进度）后 plan_import 批量合并入库。"""
    scenes = []
    for i, block in enumerate(blocks, 1):
        dal.update_script_upload_status(upload_id, "parsing", f"解析中 {i}/{total} 场…")
        try:
            scenes.extend(await parse_scene_block(block, llm_service, timeout=180.0))
        except (SPParseError, TimeoutError) as exc:
            logger.warning("场块 %d/%d 解析失败（跳过）：%s", i, total, exc)
    if not scenes:
        dal.update_script_upload_status(upload_id, "error", "未解析出任何场次")
        return
    batch_id = uuid.uuid4().hex[:8]
    plan = plan_import(scenes, target="current_scene", batch_id=batch_id, dal=dal)
    new_only = ImportPlan(target=plan.target, new_scenes=plan.new_scenes, conflicts=[])
    results = apply_import(new_only, decisions=None, dal=dal)
    skipped = len(plan.conflicts)
    detail = f"已导入 {len(results)} 场" + (f"，{skipped} 场跳过" if skipped else "")
    dal.update_script_upload_status(upload_id, "parsed", detail)


@router.post("/uploads/{upload_id}/parse", response_model=ScriptUploadInfo)
async def parse_upload(
    request: Request,
    upload_id: int,
    target: Literal["multi_scene", "current_scene"] = "multi_scene",
    _: None = Depends(require_admin),
) -> ScriptUploadInfo:
    """阶段 2（解析）：启动后台解析任务，立即返回（status=parsing）。

    解析与上传解耦、且不阻塞请求：后台逐场处理并更新进度，前端轮询 GET /uploads 展示。
    """
    dal = request.app.state.orchestrator.dal
    llm_service = request.app.state.llm_service
    if llm_service is None:
        raise HTTPException(503, "LLM 服务未启用，无法解析剧本")

    info = dal.get_script_upload(upload_id)
    if info is None:
        raise HTTPException(404, "上传记录不存在")
    raw_text = dal.get_script_upload_raw(upload_id)
    if not raw_text:
        raise HTTPException(422, "上传记录无文本内容")

    # 切场瞬时完成 → 立刻拿到总数 N，置 parsing 进度起点
    blocks = split_for_parse(raw_text)
    if not blocks:
        dal.update_script_upload_status(upload_id, "error", "清洗后无有效内容")
        raise HTTPException(422, "清洗后无有效剧本内容（全为空行/噪声）")
    dal.update_script_upload_status(upload_id, "parsing", f"解析中 0/{len(blocks)} 场…")

    # 后台跑解析（保留 task 引用防 GC，完成后自动移除）
    tasks: set = getattr(request.app.state, "parse_tasks", None)
    if tasks is None:
        tasks = set()
        request.app.state.parse_tasks = tasks
    task = asyncio.create_task(
        run_parse_job(dal, llm_service, upload_id, raw_text, target)
    )
    tasks.add(task)
    task.add_done_callback(tasks.discard)

    logger.info("剧本解析：upload_id=%d 启动后台任务（%d 场块）", upload_id, len(blocks))
    return ScriptUploadInfo(**dal.get_script_upload(upload_id))


@router.post("/import/confirm", response_model=UploadResult)
async def confirm_import(
    request: Request,
    body: ConfirmBody,
    _: None = Depends(require_admin),
) -> UploadResult:
    """阶段 2：按用户对每个重复场的 replace/skip 决策写库。"""
    dal = request.app.state.orchestrator.dal
    plan = _plan_from_dict(body.plan)
    decisions = {d.scene_id: d.action for d in body.decisions}

    results = apply_import(plan, decisions=decisions, dal=dal)
    return UploadResult(
        status="imported",
        scenes=_build_imported_scenes(results, dal),
    )
