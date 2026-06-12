"""NP 一步式提取 pipeline（替代 np_note 的 runner 角色）。

模型一次调 extract_np（扁平全 required 哨兵）→ 解析成 NPExtraction → 交确定性解析（np_resolve）
→ 多目标 apply（np_apply）。文本/语音共用，语音先 ASR。

公共 API：
  NPExtraction          解析后的扁平意图 dataclass
  NPParseError          tool_call 解析/校验失败
  NPConfirmNeeded       语音双跑承重字段分歧（非失败），携带第一跑结果与分歧字段
  parse_extract_tool_call(tool_call) -> NPExtraction
  run_extract_np(...)        文本路径（PART 5）
  run_extract_np_voice(...)  语音路径（PART 5）
  np_voice_adapter(...)      语音 adapter（双跑自一致性，两入口共用）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.llm.service import LLMService

logger = logging.getLogger(__name__)

_VALID_DEICTIC = ("none", "current", "prev")
_VALID_MARK = ("pass", "ng", "keep", "tbd", "none")
_VALID_NOTE_CATEGORY = ("note", "issue")


class NPParseError(Exception):
    """extract_np 输出解析/校验失败。"""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class NPConfirmNeeded(Exception):
    """语音双跑承重字段分歧 → 需人确认(非失败)。携带第一跑结果与分歧字段。

    由 np_voice_adapter 双跑后上抛，orchestrator._finalize_np 接住发 note.confirm
    （不落库）。不是失败语义：不经 note.failed 映射分支。

    选异常而非联合返回：runner 契约（await→NPExtraction）不变，_finalize_np 的
    异常通道直接承载，无需拓宽所有 caller 的返回类型。
    """

    def __init__(self, extraction: "NPExtraction", disagreement: list[str]) -> None:
        super().__init__(f"语音自一致性分歧: {disagreement}")
        self.extraction = extraction
        self.disagreement = disagreement


@dataclass(frozen=True)
class NPExtraction:
    scene_ordinal: int
    shot_ordinal: int
    take_ordinals: list[int]
    deictic: str  # none|current|prev
    mark: str  # pass|ng|keep|tbd|none
    note_text: str
    note_category: str  # note|issue


_CONSISTENCY_FIELDS = ("scene_ordinal", "shot_ordinal", "take_ordinals", "deictic", "mark")


def consistency_disagreement(e1: NPExtraction, e2: NPExtraction) -> list[str]:
    """语音双跑承重字段比对（自一致性触发，design doc 2026-06-11-np-voice-design §3）。

    note_text/note_category 跑间自然波动，不参与比对。take_ordinals 排序后比。
    """
    diff = []
    for f in _CONSISTENCY_FIELDS:
        a, b = getattr(e1, f), getattr(e2, f)
        if f == "take_ordinals":
            a, b = sorted(a), sorted(b)
        if a != b:
            diff.append(f)
    return diff


def _as_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise NPParseError(f"{field} 非整数: {value!r}")
    return value


def _validate_extraction(data: dict) -> NPExtraction:
    if not isinstance(data, dict):
        raise NPParseError(f"extract_np 输出非对象: {str(data)[:200]}")

    scene_ordinal = _as_int(data.get("scene_ordinal"), "scene_ordinal")
    shot_ordinal = _as_int(data.get("shot_ordinal"), "shot_ordinal")

    raw_takes = data.get("take_ordinals")
    if not isinstance(raw_takes, list):
        raise NPParseError(f"take_ordinals 非数组: {raw_takes!r}")
    take_ordinals = [_as_int(t, "take_ordinals[]") for t in raw_takes]

    deictic = data.get("deictic")
    if deictic not in _VALID_DEICTIC:
        raise NPParseError(f"deictic 非法: {deictic!r}")

    mark = data.get("mark")
    if mark not in _VALID_MARK:
        raise NPParseError(f"mark 非法: {mark!r}")

    note_text = data.get("note_text")
    if not isinstance(note_text, str):
        raise NPParseError(f"note_text 非字符串: {note_text!r}")

    note_category = data.get("note_category")
    if note_category not in _VALID_NOTE_CATEGORY:
        raise NPParseError(f"note_category 非法: {note_category!r}")

    return NPExtraction(
        scene_ordinal=scene_ordinal,
        shot_ordinal=shot_ordinal,
        take_ordinals=take_ordinals,
        deictic=deictic,
        mark=mark,
        note_text=note_text,
        note_category=note_category,
    )


def parse_extract_tool_call(tool_call: dict) -> NPExtraction:
    """tool_calls[0] → NPExtraction：解析 arguments JSON 字符串 → 校验枚举/类型。"""
    try:
        args_json: str = tool_call["function"]["arguments"]
        data = json.loads(args_json)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise NPParseError("extract_np tool_call arguments 解析失败", cause=exc)
    return _validate_extraction(data)


def _build_extract_system_prompt() -> str:
    """提取 system prompt（spike 21/24 复跑版 + note_category 句）。上下文行由 orchestrator 拼接追加。"""
    return (
        "你是场记助手。把录音师这句话提取成固定结构，每个字段都要填（用哨兵值表示'没有'）。"
        "映射：第N进/第N镜=shot_ordinal，第N条/第N次=take_ordinal，第N场=scene_ordinal；"
        "当前场/镜填0；这条=deictic current，上一条/刚才那条=deictic prev，用了编号=deictic none；"
        "过/通过=mark pass，保/留=keep，废/NG/不行=ng，没打标意图=mark none；"
        "描述性内容放 note_text，没有填空串；"
        "技术问题(收音小/灯光暗/穿帮/对焦虚)note_category=issue，否则 note。"
    )


def build_extract_messages(raw_text: str, context_line: str) -> list[dict]:
    """文本提取 messages：system（语义 + 上下文行）+ user（原话）。"""
    system = _build_extract_system_prompt()
    if context_line:
        system = system + "\n" + context_line
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": raw_text},
    ]


async def run_extract_np(
    raw_text: str,
    llm_service: "LLMService",
    timeout: float = 30.0,
    context_line: str = "",
) -> NPExtraction:
    """文本 NP 提取：forced extract_np → NPExtraction。解析失败 → NPParseError。"""
    messages = build_extract_messages(raw_text, context_line)
    try:
        tool_call = await llm_service.infer_tool(
            messages, task_type="note_extract", priority=2, timeout=timeout
        )
    except LookupError as exc:
        raise NPParseError("extract_np tool_calls 缺失，模型未走 FC", cause=exc)
    return parse_extract_tool_call(tool_call)


def _build_voice_asr_messages(context_line: str) -> list[dict]:
    """ASR messages：text 提示 + 音频哨兵（多模态通道）。无强制工具（content 路径）。"""
    from backend.llm.multimodal import AUDIO_SENTINEL  # noqa: PLC0415

    prompt = "把这段中文语音逐字转写成文本，只输出转写内容，不要解释。"
    if context_line:
        prompt = context_line + "\n" + prompt
    return [
        {"role": "system", "content": "你是中文语音转写助手。"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": AUDIO_SENTINEL}},
            ],
        },
    ]


async def run_extract_np_voice(
    audio: bytes,
    llm_service: "LLMService",
    timeout: float = 60.0,
    context_line: str = "",
) -> NPExtraction:
    """语音 NP（设计 §3.3 两步）：① 多模态 ASR 转写 ② 转写当 raw_text 走同一 extract_np。

    ASR 用无强制工具 task（voice_dispatch_free），避免 forced tool 撞 content-path guardrail。
    """
    asr_messages = _build_voice_asr_messages(context_line)
    transcript = await llm_service.infer_voice(
        asr_messages, audio, task_type="voice_dispatch_free", priority=1, timeout=timeout
    )
    if not transcript or not transcript.strip():
        raise NPParseError("语音转写为空")
    return await run_extract_np(
        transcript.strip(), llm_service, timeout=timeout, context_line=context_line
    )


# ── 适配器（调度器-兼容签名）────────────────────────────────────────────────────
# voice_dispatch.py（§9 不可碰）调用 voice_runner(input_data, audio, service) 位置参数固定；
# 以下适配器保持该签名，内部转调新 extract runner。duck-typed on input_data。


def _np_context_line(input_data) -> str:
    """从 input_data 拼一行提取上下文（spike 格式）。current_* 为 None 时省略活跃条。"""
    scene = input_data.current_scene_code or (
        f"场id{input_data.current_scene_id}" if input_data.current_scene_id is not None else "未知场"
    )
    if input_data.current_take_id is not None:
        shot = input_data.current_shot or "无镜"
        return f"当前：{scene} / {shot} / 当前活跃第{input_data.current_take_number}条。"
    return f"当前：{scene} / 当前无活跃录制。"


async def np_text_adapter(input_data, svc, timeout: float = 30.0):
    """文本 NP 适配器：保持 (input_data, svc) 位置签名，转调 run_extract_np。"""
    return await run_extract_np(
        input_data.raw_text, svc, timeout=timeout, context_line=_np_context_line(input_data)
    )


async def np_voice_adapter(input_data, audio: bytes, svc, timeout: float = 60.0):
    """语音 NP 适配器（自一致性双跑）：保持 (input_data, audio, svc) 位置签名。

    双跑下沉在此处的原因（架构定案）：两个生产入口——dispatch 委托路径
    （voice_dispatch._handle_note_branch，前端 MemoInput 恒走此路）与 orchestrator
    直跑路径（_run_np_voice_async）——都以本 adapter 为 voice_runner，一处实现全覆盖。

    延迟代价：语音链路总耗时 ×2（同一音频 ASR+提取各跑两遍）。有意取舍：语音听写
    天然不稳定，双跑是检测该不稳定性的最低成本手段；两跑一致时用户感知与单跑相同。

    双跑语义：
      第一跑异常 → 原样上抛（_finalize_np 既有映射 → note.failed，主流程报错路径不变）。
      第二跑异常 → fail-open：logger.warning + 视同一致直落第一跑结果。
        设计权衡：第二跑只是检测增强，它挂了不应阻塞主流程（单跑语义兜底）。
      分歧（consistency_disagreement 非空）→ raise NPConfirmNeeded(e1, diff)，
        由 _finalize_np 接住发 note.confirm 待人确认（不落库）。
        第一跑结果为准：确认卡预填值取 e1（模型第一次判断），第二跑只用于触发检测。
      一致 → return e1（单跑语义）。
    """
    context_line = _np_context_line(input_data)
    # 第一跑：异常原样上抛（_finalize_np 既有 parse/timeout/model_unavailable 映射处理）
    e1 = await run_extract_np_voice(audio, svc, timeout=timeout, context_line=context_line)

    # 第二跑：try/except 全包（fail-open——检测增强挂了不阻塞主流程）
    try:
        e2 = await run_extract_np_voice(audio, svc, timeout=timeout, context_line=context_line)
    except Exception as exc:
        logger.warning("语音 NP 第二跑失败，fail-open 直落第一跑结果: %r", exc)
        return e1

    diff = consistency_disagreement(e1, e2)
    if diff:
        raise NPConfirmNeeded(e1, diff)
    return e1
