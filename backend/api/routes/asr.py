"""ASR 运行配置端点:读当前引擎/语言/模型 + 切换引擎/语言。

GET  /api/v1/asr           当前 engine / language / model + 引擎清单(各自语言列表)
POST /api/v1/asr/engine    切换 ASR 引擎(仅非录制时;funasr 未安装 → 409)
POST /api/v1/asr/language  切换转录语言(即时生效;须在当前引擎支持列表内)

状态存在 app.state.live_asr(LiveAsrSession)。未启用实时 ASR 时
GET 返回 enabled=False,POST 返回 409。引擎选择不持久化,重启归位 whisper。
"""
from __future__ import annotations

import asyncio
import importlib.util

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.api.auth import require_admin
from backend.asr.funasr_runner import FunAsrNotInstalled

router = APIRouter(prefix="/api/v1", tags=["asr"])

# 引擎清单:id → UI 标签 + 该引擎的语言下拉(whisper 实际支持 99 种;auto=自动检测)
_ENGINE_LANGUAGES: dict[str, list[str]] = {
    "whisper": ["zh", "en", "auto"],
    "funasr": ["zh"],  # paraformer-zh 仅中文
}
_ENGINE_LABELS = {"whisper": "whisper.cpp", "funasr": "FunASR"}

# 向后兼容:旧客户端依赖顶层 languages 字段(whisper 语言列表)
_UI_LANGUAGES = _ENGINE_LANGUAGES["whisper"]


def _funasr_installed() -> bool:
    return importlib.util.find_spec("funasr") is not None


def _engines_payload() -> list[dict[str, object]]:
    return [
        {
            "id": eng,
            "label": _ENGINE_LABELS[eng],
            "languages": langs,
            "installed": True if eng == "whisper" else _funasr_installed(),
        }
        for eng, langs in _ENGINE_LANGUAGES.items()
    ]


@router.get("/asr")
async def get_asr(
    request: Request,
    _: None = Depends(require_admin),
) -> dict[str, object]:
    """当前引擎 + 转录语言 + 模型(未启用实时 ASR 时 enabled=False)。"""
    session = getattr(request.app.state, "live_asr", None)
    if session is None:
        return {
            "enabled": False,
            "engine": None,
            "language": None,
            "model": None,
            "languages": _UI_LANGUAGES,
            "engines": _engines_payload(),
        }
    return {
        "enabled": True,
        "engine": session.engine,
        "language": session.language,
        "model": session.model_size,
        "languages": _UI_LANGUAGES,
        "engines": _engines_payload(),
    }


class EngineBody(BaseModel):
    engine: str


@router.post("/asr/engine")
async def set_engine(
    body: EngineBody,
    request: Request,
    _: None = Depends(require_admin),
) -> dict[str, object]:
    """切换 ASR 引擎。未启用 ASR / 录制中 / funasr 未安装 → 409;未知引擎 → 422。"""
    session = getattr(request.app.state, "live_asr", None)
    if session is None:
        raise HTTPException(status_code=409, detail="live ASR not enabled")
    engine = body.engine.strip()
    if engine not in _ENGINE_LANGUAGES:
        raise HTTPException(status_code=422, detail=f"未知 ASR 引擎: {engine}")
    if session.running:
        raise HTTPException(status_code=409, detail="录制中不可切换引擎")
    try:
        # warmup 可能分钟级(首次 modelscope 下载 ~1GB),移出事件循环避免冻死全部请求
        await asyncio.to_thread(session.set_engine, engine)
    except FunAsrNotInstalled as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except RuntimeError as e:  # set_engine 内部的录制中守卫(双保险)
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"status": "ok", "engine": engine, "language": session.language}


class LanguageBody(BaseModel):
    language: str


@router.post("/asr/language")
async def set_language(
    body: LanguageBody,
    request: Request,
    _: None = Depends(require_admin),
) -> dict[str, object]:
    """切换转录语言。未启用实时 ASR → 409;空/不在当前引擎支持列表 → 422。"""
    session = getattr(request.app.state, "live_asr", None)
    if session is None:
        raise HTTPException(status_code=409, detail="live ASR not enabled")
    lang = body.language.strip()
    if not lang:
        raise HTTPException(status_code=422, detail="language required")
    supported = _ENGINE_LANGUAGES.get(session.engine, [])
    if lang not in supported:
        raise HTTPException(status_code=422, detail=f"当前引擎不支持语言: {lang}")
    session.set_language(lang)
    return {"status": "ok", "language": lang}
