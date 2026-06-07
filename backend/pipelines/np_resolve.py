"""NP 确定性解析：NPExtraction + 上下文 → 具体 take_id 列表，或 clarify 信号（无模型）。

设计 §3.2：
  0/[]      → 当前场/镜
  current   → 当前活跃 take
  prev      → 该场镜下最近一条「已完成」take（end_ts 已落、take_number 最高且 < 活跃条）
  ordinals  → 该场镜下这些 take_number（经 get_take_by_coords 查存在）

查存在：解析不出 / 不唯一（带 suffix 多行）/ 目标不存在 → clarify（确定性，不写）。根治「硬标错」。
解析不引用 take_context（last-10、剔当前条），直接打 DAL，免遗漏当前条或较老条。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NPContext:
    """解析所需的会话上下文（orchestrator 从 session + DAL 组装）。"""

    current_scene_id: int | None
    current_scene_code: str | None
    current_shot: str | None
    current_take_id: int | None
    current_take_number: int | None


@dataclass(frozen=True)
class ClarifyCandidate:
    take_id: int
    scene_code: str
    shot: str
    take_number: int
    take_suffix: str
    status: str


@dataclass(frozen=True)
class ResolveResult:
    take_ids: list[int]
    clarify: bool
    message: str | None
    candidates: list[ClarifyCandidate] = field(default_factory=list)


def _candidate(take: Any, scene_code: str) -> ClarifyCandidate:
    return ClarifyCandidate(
        take_id=take.take_id,
        scene_code=scene_code,
        shot=take.shot,
        take_number=take.take_number,
        take_suffix=take.take_suffix,
        status=take.status,
    )


def _clarify(message: str, candidates: list[ClarifyCandidate]) -> ResolveResult:
    return ResolveResult(take_ids=[], clarify=True, message=message, candidates=candidates)


def resolve_targets(extraction: Any, ctx: NPContext, dal: Any) -> ResolveResult:
    """扁平意图 + 上下文 + DAL → take_id 列表 / clarify。"""
    # 1. 定场：scene_ordinal!=0 → resolve_scene_id；0 → 当前场。
    if extraction.scene_ordinal != 0:
        scene_id = dal.resolve_scene_id(str(extraction.scene_ordinal))
        if scene_id is None:
            return _clarify(f"找不到第{extraction.scene_ordinal}场", [])
    else:
        scene_id = ctx.current_scene_id
        if scene_id is None:
            return _clarify("当前没有活跃场次，请说明是第几场", [])

    # 2. 定镜：shot_ordinal!=0 → 该镜的 TEXT 值（约定数字字符串）；0 → 当前镜。
    if extraction.shot_ordinal != 0:
        shot = str(extraction.shot_ordinal)
    else:
        shot = ctx.current_shot if ctx.current_shot is not None else ""

    # 3. deictic 优先于 ordinals（用了编号则 deictic=none）。
    if extraction.deictic == "current":
        if ctx.current_take_id is None:
            return _clarify("当前没有活跃录制的 take，无法定位「这条」", [])
        take = dal.get_take(ctx.current_take_id)
        if take is None:
            return _clarify("当前活跃 take 已不存在", [])
        return ResolveResult(take_ids=[take.take_id], clarify=False, message=None)

    if extraction.deictic == "prev":
        prev = _resolve_prev(dal, scene_id, shot, ctx)
        if prev is None:
            return _clarify("找不到「上一条」已完成的 take", [])
        return ResolveResult(take_ids=[prev.take_id], clarify=False, message=None)

    # 4. ordinals：逐个按 (scene_id, shot, take_number) 查存在。
    if extraction.take_ordinals:
        take_ids: list[int] = []
        for num in extraction.take_ordinals:
            matches = dal.get_take_by_coords(scene_id, shot, num)
            if not matches:
                return _clarify(f"找不到该场镜下第{num}条", [])
            if len(matches) > 1:
                cands = [_candidate(t, _scene_code(dal, ctx, scene_id)) for t in matches]
                return _clarify(f"第{num}条有多条（带补拍），请说明是哪一条", cands)
            take_ids.append(matches[0].take_id)
        return ResolveResult(take_ids=take_ids, clarify=False, message=None)

    # 5. deictic=none 且无 ordinals：
    #    未显式点场/镜 → 兜底当前活跃 take（如「收音有点小」，spike Case 6）；
    #    显式点了场/镜却没说第几条 → clarify（不能硬标当前条，根治「硬标错」，设计 §3.2）。
    if extraction.scene_ordinal == 0 and extraction.shot_ordinal == 0:
        if ctx.current_take_id is not None:
            return ResolveResult(take_ids=[ctx.current_take_id], clarify=False, message=None)
        return _clarify("没有指明 take，且当前无活跃录制", [])
    return _clarify("指定了场/镜但没说第几条，请说明是哪一条", [])


def _resolve_prev(dal: Any, scene_id: int | None, shot: str, ctx: NPContext) -> Any | None:
    """该场镜下最近一条「已完成」take：end_ts 已落、排除当前活跃条、take_number 最高。"""
    if scene_id is None:
        return None
    takes = [
        t for t in dal.list_takes(scene_id)
        if t.shot == shot and t.end_ts is not None and t.take_id != ctx.current_take_id
    ]
    if not takes:
        return None
    return max(takes, key=lambda t: t.take_number)


def _scene_code(dal: Any, ctx: NPContext, scene_id: int | None) -> str:
    """候选展示用 scene_code：当前场用上下文 code，跨场尽力用 take 自带（fake DAL 测试覆盖）。"""
    if scene_id == ctx.current_scene_id and ctx.current_scene_code:
        return ctx.current_scene_code
    return str(scene_id) if scene_id is not None else "?"
