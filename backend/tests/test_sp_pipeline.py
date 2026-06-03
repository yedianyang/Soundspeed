"""3.B SP Pipeline 单元测试。

覆盖：
  ① 单场无 slugline 纯对白（全角冒号格式）
  ② 多场带 slugline + 场次号
  ③ 多场带 slugline 但无场次号
  ④ 脏数据（空行、页码、噪声）
  ⑤ 分块 loop：多次调用 infer（side_effect 列表）
  ⑥ JSON 容错路径：非法 JSON / 缺 lines / markdown fence 包裹
  ⑦ 空输入返回空列表

全部使用 AsyncMock 注入 llm_service.infer，不加载真实模型。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.llm.service import LLMService
from backend.pipelines.sp_script import (
    ParsedLine,
    ParsedScene,
    Slugline,
    SPParseError,
    run_sp_parse,
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _mock_llm(*responses: str) -> MagicMock:
    """创建注入 AsyncMock.infer 的 LLMService mock。

    传多个 response 时 infer 按顺序返回（side_effect），
    只传一个时 return_value 固定返回。
    """
    svc = MagicMock(spec=LLMService)
    if len(responses) == 1:
        svc.infer = AsyncMock(return_value=responses[0])
    else:
        svc.infer = AsyncMock(side_effect=list(responses))
    return svc


def _scenes_json(scenes: list[dict]) -> str:
    """把 scenes list 序列化为 LLM 返回格式。"""
    return json.dumps({"scenes": scenes}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Fixture ① 单场无 slugline 纯对白
# ---------------------------------------------------------------------------

# 仿 frontend/src/data/devFixtures.ts 的 DEV_SCRIPT_SAMPLE
SINGLE_SCENE_RAW = """罗湘：罗湘老师平时是一个不爱社交的人
罗湘：他喝多的时候会在深夜给我打电话
访谈者：你会刷短视频吗
罗湘：我刷过，但我觉得太上瘾了"""

SINGLE_SCENE_LLM_RESPONSE = _scenes_json(
    [
        {
            "scene_code": None,
            "slugline": {"int_ext": None, "time_of_day": None, "location": None},
            "lines": [
                {"character": "罗湘", "text": "罗湘老师平时是一个不爱社交的人"},
                {"character": "罗湘", "text": "他喝多的时候会在深夜给我打电话"},
                {"character": "访谈者", "text": "你会刷短视频吗"},
                {"character": "罗湘", "text": "我刷过，但我觉得太上瘾了"},
            ],
        }
    ]
)


@pytest.mark.asyncio
async def test_single_scene_no_slugline() -> None:
    """单场无 slugline：返回单个 ParsedScene，scene_code=None，slugline 全 None。"""
    svc = _mock_llm(SINGLE_SCENE_LLM_RESPONSE)
    scenes = await run_sp_parse(SINGLE_SCENE_RAW, svc)

    assert len(scenes) == 1
    scene = scenes[0]
    assert isinstance(scene, ParsedScene)
    assert scene.scene_code is None
    assert scene.slugline.int_ext is None
    assert scene.slugline.time_of_day is None
    assert scene.slugline.location is None
    assert len(scene.lines) == 4
    assert all(isinstance(ln, ParsedLine) for ln in scene.lines)
    # 第一行角色正确
    assert scene.lines[0].character == "罗湘"
    assert scene.lines[0].text == "罗湘老师平时是一个不爱社交的人"


# ---------------------------------------------------------------------------
# Fixture ② 多场带 slugline + 场次号
# ---------------------------------------------------------------------------

MULTI_SCENE_WITH_CODE_RAW = """场 3  内 咖啡馆 日
罗湘：我们先聊聊你的背景。
访谈者：好的。

场 4  外 广场 夜
罗湘：今晚的广场很安静。
（罗湘环顾四周）"""

MULTI_SCENE_WITH_CODE_LLM_RESPONSE = _scenes_json(
    [
        {
            "scene_code": "3",
            "slugline": {"int_ext": "内", "time_of_day": "日", "location": "咖啡馆"},
            "lines": [
                {"character": "罗湘", "text": "我们先聊聊你的背景。"},
                {"character": "访谈者", "text": "好的。"},
            ],
        },
        {
            "scene_code": "4",
            "slugline": {"int_ext": "外", "time_of_day": "夜", "location": "广场"},
            "lines": [
                {"character": "罗湘", "text": "今晚的广场很安静。"},
                {"character": None, "text": "罗湘环顾四周"},
            ],
        },
    ]
)


@pytest.mark.asyncio
async def test_multi_scene_with_scene_code() -> None:
    """多场带场次号：scene_code 正确抽取，slugline 三要素填充，舞台指示行 character=None。"""
    svc = _mock_llm(MULTI_SCENE_WITH_CODE_LLM_RESPONSE)
    scenes = await run_sp_parse(MULTI_SCENE_WITH_CODE_RAW, svc)

    assert len(scenes) == 2

    s1 = scenes[0]
    assert s1.scene_code == "3"
    assert s1.slugline.int_ext == "内"
    assert s1.slugline.time_of_day == "日"
    assert s1.slugline.location == "咖啡馆"
    assert len(s1.lines) == 2

    s2 = scenes[1]
    assert s2.scene_code == "4"
    assert s2.slugline.int_ext == "外"
    assert s2.slugline.time_of_day == "夜"
    assert s2.slugline.location == "广场"
    # 最后一行是舞台指示行
    stage_direction = s2.lines[-1]
    assert stage_direction.character is None
    assert stage_direction.text  # 非空字符串


# ---------------------------------------------------------------------------
# Fixture ③ 多场带 slugline 但无场次号
# ---------------------------------------------------------------------------

MULTI_SCENE_NO_CODE_RAW = """内 咖啡馆 日
罗湘：你好。

外 广场 夜
访谈者：再见。"""

MULTI_SCENE_NO_CODE_LLM_RESPONSE = _scenes_json(
    [
        {
            "scene_code": None,
            "slugline": {"int_ext": "内", "time_of_day": "日", "location": "咖啡馆"},
            "lines": [
                {"character": "罗湘", "text": "你好。"},
            ],
        },
        {
            "scene_code": None,
            "slugline": {"int_ext": "外", "time_of_day": "夜", "location": "广场"},
            "lines": [
                {"character": "访谈者", "text": "再见。"},
            ],
        },
    ]
)


@pytest.mark.asyncio
async def test_multi_scene_no_scene_code() -> None:
    """多场有 slugline 但无场次号：scene_code=None 是合法常态值，不报错。"""
    svc = _mock_llm(MULTI_SCENE_NO_CODE_LLM_RESPONSE)
    scenes = await run_sp_parse(MULTI_SCENE_NO_CODE_RAW, svc)

    assert len(scenes) == 2
    for scene in scenes:
        assert scene.scene_code is None
    assert scenes[0].slugline.location == "咖啡馆"
    assert scenes[1].slugline.location == "广场"


# ---------------------------------------------------------------------------
# Fixture ④ 脏数据（空行、页码、噪声）
# ---------------------------------------------------------------------------

DIRTY_RAW = """

第 3 页

罗湘：我们继续。
                        12

访谈者：好的。
---页眉---
罗湘：就这样。"""

DIRTY_LLM_RESPONSE = _scenes_json(
    [
        {
            "scene_code": None,
            "slugline": {"int_ext": None, "time_of_day": None, "location": None},
            "lines": [
                {"character": "罗湘", "text": "我们继续。"},
                {"character": "访谈者", "text": "好的。"},
                {"character": "罗湘", "text": "就这样。"},
            ],
        }
    ]
)


@pytest.mark.asyncio
async def test_dirty_data_no_exception() -> None:
    """脏数据输入：LLM 模拟过滤后，pipeline 正常返回，不抛异常。"""
    svc = _mock_llm(DIRTY_LLM_RESPONSE)
    scenes = await run_sp_parse(DIRTY_RAW, svc)

    assert len(scenes) == 1
    # LLM 清理了噪声，只剩 3 行真实对白
    assert len(scenes[0].lines) == 3


# ---------------------------------------------------------------------------
# 分块 loop 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunked_input_calls_infer_multiple_times() -> None:
    """超长输入被分块：infer 被调用多于 1 次，结果合并。

    使用 return_value（固定返回同一响应），对分块数变化免疫。
    构造 10 行、每行约 156 字符，chunk_size=500 会切成多块（>=2）。
    每块返回 1 个 ParsedScene，最终合并场数 == infer 调用次数。
    """
    chunk_scene = {
        "scene_code": None,
        "slugline": {"int_ext": None, "time_of_day": None, "location": None},
        "lines": [{"character": "A", "text": "台词"}],
    }
    chunk_response = _scenes_json([chunk_scene])

    # 单个 return_value：每次 infer 都返回同一响应，对实际分块数免疫
    svc = _mock_llm(chunk_response)

    long_raw = "\n".join(
        [f"角色甲：{'这是一段很长的台词，用于测试分块逻辑。' * 8}"] * 10
    )

    scenes = await run_sp_parse(long_raw, svc, chunk_size=500)

    # infer 被调用超过 1 次（验证确实走了 loop，不是一把梭）
    assert svc.infer.call_count >= 2
    # 场列表 = 各块场列表合并（每块各返回 1 场）
    assert len(scenes) == svc.infer.call_count


# ---------------------------------------------------------------------------
# JSON 容错路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_json_raises_sp_parse_error() -> None:
    """LLM 返回非 JSON 字符串，抛 SPParseError，cause 为 JSONDecodeError。"""
    svc = _mock_llm("这不是JSON")
    with pytest.raises(SPParseError) as exc_info:
        await run_sp_parse(SINGLE_SCENE_RAW, svc)

    import json as _json

    assert isinstance(exc_info.value.cause, _json.JSONDecodeError)


@pytest.mark.asyncio
async def test_markdown_fence_json_parsed() -> None:
    """LLM 返回 ```json...``` 包裹的 JSON，pipeline 能正常解析。"""
    wrapped = "```json\n" + SINGLE_SCENE_LLM_RESPONSE + "\n```"
    svc = _mock_llm(wrapped)
    scenes = await run_sp_parse(SINGLE_SCENE_RAW, svc)

    assert len(scenes) == 1


@pytest.mark.asyncio
async def test_missing_lines_key_raises_sp_parse_error() -> None:
    """LLM 返回的场里缺 lines 字段，抛 SPParseError。"""
    bad_response = json.dumps(
        {"scenes": [{"scene_code": None, "slugline": {"int_ext": None, "time_of_day": None, "location": None}}]},
        ensure_ascii=False,
    )
    svc = _mock_llm(bad_response)
    with pytest.raises(SPParseError):
        await run_sp_parse(SINGLE_SCENE_RAW, svc)


@pytest.mark.asyncio
async def test_missing_scenes_key_raises_sp_parse_error() -> None:
    """LLM 返回顶层缺 scenes 字段的 JSON，抛 SPParseError。"""
    bad_response = json.dumps({"result": []}, ensure_ascii=False)
    svc = _mock_llm(bad_response)
    with pytest.raises(SPParseError):
        await run_sp_parse(SINGLE_SCENE_RAW, svc)


@pytest.mark.asyncio
async def test_empty_llm_response_raises_sp_parse_error() -> None:
    """LLM 返回空字符串，抛 SPParseError。"""
    svc = _mock_llm("")
    with pytest.raises(SPParseError):
        await run_sp_parse(SINGLE_SCENE_RAW, svc)


# ---------------------------------------------------------------------------
# 空输入 / 零场
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_raw_text_returns_empty_list() -> None:
    """空 raw_text 返回空列表，不调用 infer。"""
    svc = _mock_llm(SINGLE_SCENE_LLM_RESPONSE)
    scenes = await run_sp_parse("", svc)

    assert scenes == []
    svc.infer.assert_not_called()


@pytest.mark.asyncio
async def test_whitespace_only_raw_text_returns_empty_list() -> None:
    """纯空白 raw_text 返回空列表，不调用 infer。"""
    svc = _mock_llm(SINGLE_SCENE_LLM_RESPONSE)
    scenes = await run_sp_parse("   \n\n  ", svc)

    assert scenes == []
    svc.infer.assert_not_called()


# ---------------------------------------------------------------------------
# LLM 返回 scenes=[] 正常处理
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_returns_zero_scenes() -> None:
    """LLM 返回 scenes=[] 时，pipeline 返回空列表，不抛异常。"""
    empty_response = json.dumps({"scenes": []}, ensure_ascii=False)
    svc = _mock_llm(empty_response)
    scenes = await run_sp_parse(SINGLE_SCENE_RAW, svc)

    assert scenes == []


# ---------------------------------------------------------------------------
# timeout 穿透
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_propagated() -> None:
    """LLMService.infer 抛 asyncio.TimeoutError，pipeline 不吞，让其穿透。"""
    svc = MagicMock(spec=LLMService)
    svc.infer = AsyncMock(side_effect=asyncio.TimeoutError())

    with pytest.raises(asyncio.TimeoutError):
        await run_sp_parse(SINGLE_SCENE_RAW, svc, timeout=0.1)


# ---------------------------------------------------------------------------
# infer 调用参数验证
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_infer_called_with_correct_task_type_and_priority() -> None:
    """run_sp_parse 调用 infer 时 task_type='script_parse' 且 priority=3。"""
    svc = _mock_llm(SINGLE_SCENE_LLM_RESPONSE)
    await run_sp_parse(SINGLE_SCENE_RAW, svc)

    svc.infer.assert_called_once()
    call_kwargs = svc.infer.call_args
    kw = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
    assert kw.get("task_type") == "script_parse"
    assert kw.get("priority") == 3


# ---------------------------------------------------------------------------
# 数据类型验证
# ---------------------------------------------------------------------------


def test_parsed_scene_is_frozen_dataclass() -> None:
    """ParsedScene 是 frozen dataclass，字段赋值应抛 FrozenInstanceError。"""
    from dataclasses import FrozenInstanceError

    scene = ParsedScene(
        scene_code=None,
        slugline=Slugline(int_ext=None, time_of_day=None, location=None),
        lines=[],
    )
    with pytest.raises(FrozenInstanceError):
        scene.scene_code = "1"  # type: ignore[misc]


def test_slugline_is_frozen_dataclass() -> None:
    """Slugline 是 frozen dataclass。"""
    from dataclasses import FrozenInstanceError

    sl = Slugline(int_ext="内", time_of_day="日", location="咖啡馆")
    with pytest.raises(FrozenInstanceError):
        sl.int_ext = "外"  # type: ignore[misc]


def test_parsed_line_character_none_is_stage_direction() -> None:
    """ParsedLine.character=None 合法，表示舞台指示行。"""
    line = ParsedLine(character=None, text="罗湘走向门口")
    assert line.character is None
    assert line.text == "罗湘走向门口"
