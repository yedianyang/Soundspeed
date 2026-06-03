"""SP Pipeline：剧本解析器（ticket 3.B）。

公共 API：
  Slugline      slugline 三要素 dataclass（frozen）
  ParsedLine    单行对白/舞台指示 dataclass（frozen）
  ParsedScene   单场解析结果 dataclass（frozen）
  SPParseError  LLM 输出解析失败异常
  run_sp_parse  纯异步函数，执行剧本解析

设计依据：
  docs/specs/2026-06-03-script-import-sp-pipeline.md §4

分块策略（体量约束，§4 最后一段）
---------------------------------------
script_parse.max_tokens=8192（= n_ctx，靠 EOS 收尾），整本剧本仍不能一把梭，必须分块。

采用「空行优先切分 → 每块一次 LLM 调用 → 结果顺序 append」策略：

1. 先按空行（连续空行）把 raw_text 切成段落块（paragraph）。
   空行是剧本中最常见的场间分隔，优先在段落边界切可避免把 slugline
   和其紧跟的对白行切进两个不同块（孤儿场的主因）。
2. 按 chunk_size 字符数累积段落块成 chunk；段落内不切（保场内完整）。
   若单个段落块本身超过 chunk_size，退化到按行机械切兜底（防超 token）。
3. 每块独立调用 LLM（task_type="script_parse"），LLM 在该块内做：
   - slugline 识别与三要素抽取（int_ext / time_of_day / location）
   - scene_code 识别（有则抽取，无则 null）
   - 行结构化（character / text，舞台指示行 character=null）
   - 脏数据过滤（空行、页码、噪声）
4. 各块输出的 ParsedScene 列表顺序 append，形成最终结果。

此策略的权衡（已标 flag，留 Lead 拍板）：
- 跨块孤儿场：空行切降低概率但不消除——场间无空行的剧本仍可能在
  块边界切中场内。多场长剧本是**常态触发**（不只是单场超长的边缘情况）。
  chunk 边界落在 slugline 与其对白行之间时，对白那块会被 LLM 识别为
  scene_code=null 的孤儿场，append-only 入库建出额外场。v1 不合并，
  待冒烟（sp_smoke.py）后评估实际孤儿场率再决定是否加后处理。
- scene_code 透传：strip 前后空白后原样透传 LLM 输出（可能是「场3」
  「3」「Scene 3」等），跨「手动建场」与「解析器建场」的归一化规则
  待 Lead + 2.x 商定后对齐（§3.1）。
- 空 raw_text 直接返回 []，不调 LLM（422 是 3.D 端点的职责）。

不写正则/规则解析器（用户明确定，§4）。所有语义由 LLM 结构化。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.llm.config import TASK_CONFIG

if TYPE_CHECKING:
    from backend.llm.service import LLMService


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class SPParseError(Exception):
    """LLM 输出解析失败。

    cause 串联原始异常（json.JSONDecodeError / KeyError 等），
    调用方可通过 e.cause 取原始异常细节。
    """

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Slugline:
    """场景头信息（slugline）三要素。

    任意字段为 None 表示 LLM 未能识别该要素（常态合法值，不是错误）。
    """

    int_ext: str | None
    time_of_day: str | None
    location: str | None


@dataclass(frozen=True)
class ParsedLine:
    """单行台词或舞台指示。

    character=None 表示舞台指示行（对齐 schema script_lines.character 可空）。
    """

    character: str | None
    text: str


@dataclass(frozen=True)
class ParsedScene:
    """单场解析结果。

    scene_code：解析器抽到的显式场次号，抽不到为 None（常态合法值）。
    slugline：内外景/时间/地点；任意字段可 None。
    lines：有序行列表；line_no 由入库侧（3.C）分配，解析器不 emit。
    """

    scene_code: str | None
    slugline: Slugline
    lines: list[ParsedLine]


# ---------------------------------------------------------------------------
# 内部常量
# ---------------------------------------------------------------------------

# 默认单块字符上限（触发分块的阈值）
_DEFAULT_CHUNK_SIZE = 1500


# ---------------------------------------------------------------------------
# Prompt 构建
# ---------------------------------------------------------------------------


def _build_system_prompt() -> str:
    """返回 TASK_CONFIG["script_parse"]["system"]。"""
    return TASK_CONFIG["script_parse"]["system"]


def _build_user_message(chunk_text: str) -> str:
    """组装单块的 user message。"""
    return (
        "请解析以下剧本片段，输出 JSON。直接输出 JSON，不要 markdown 代码块。\n\n"
        f"{chunk_text}"
    )


# ---------------------------------------------------------------------------
# 分块
# ---------------------------------------------------------------------------


def _lines_to_paragraphs(lines: list[str]) -> list[str]:
    """把行列表按空行切成段落块，返回非空段落列表（不含空行本身）。

    连续多个空行视为单个段落分隔。
    """
    paragraphs: list[str] = []
    current: list[str] = []

    for line in lines:
        if line.strip() == "":
            if current:
                paragraphs.append("\n".join(current))
                current = []
        else:
            current.append(line)

    if current:
        paragraphs.append("\n".join(current))

    return paragraphs


def _split_paragraph_by_lines(paragraph: str, chunk_size: int) -> list[str]:
    """单个超大段落按行机械切兜底，不切断单行。"""
    lines = paragraph.splitlines()
    chunks: list[str] = []
    current_lines: list[str] = []
    current_size = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if current_lines and current_size + line_len > chunk_size:
            chunks.append("\n".join(current_lines))
            current_lines = [line]
            current_size = line_len
        else:
            current_lines.append(line)
            current_size += line_len

    if current_lines:
        chunks.append("\n".join(current_lines))

    return chunks


def _split_into_chunks(raw_text: str, chunk_size: int) -> list[str]:
    """空行优先切分文本，返回各块列表。

    策略：
    1. 先按空行把文本切成段落块（剧本场间通常有空行，空行切降低跨块孤儿场概率）。
    2. 按 chunk_size 字符数累积段落块成 chunk；段落内不切（保场内完整）。
    3. 若单个段落块本身超过 chunk_size，退化到按行机械切兜底（防超 token）。
    """
    lines = raw_text.splitlines()
    paragraphs = _lines_to_paragraphs(lines)

    chunks: list[str] = []
    current_parts: list[str] = []
    current_size = 0

    for para in paragraphs:
        para_size = len(para) + 1  # +1 for separator newline

        if para_size > chunk_size:
            # 段落本身超限：先 flush 当前积累，再按行切兜底
            if current_parts:
                chunks.append("\n\n".join(current_parts))
                current_parts = []
                current_size = 0
            chunks.extend(_split_paragraph_by_lines(para, chunk_size))
        elif current_parts and current_size + para_size > chunk_size:
            # 加入后超限：先 flush，新段落开新块
            chunks.append("\n\n".join(current_parts))
            current_parts = [para]
            current_size = para_size
        else:
            current_parts.append(para)
            current_size += para_size

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks


# ---------------------------------------------------------------------------
# 输出解析
# ---------------------------------------------------------------------------


def _strip_markdown_fence(text: str) -> str:
    """剥除 ```json ... ``` 或 ``` ... ``` 包裹，只剥一层。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline == -1:
            return stripped
        content_start = first_newline + 1
        last_fence = stripped.rfind("```")
        if last_fence > content_start:
            return stripped[content_start:last_fence].strip()
    return stripped


def _parse_chunk_output(raw_text: str) -> list[ParsedScene]:
    """解析单块 LLM 输出文本为 ParsedScene 列表。

    期望格式：
    {
      "scenes": [
        {
          "scene_code": "string | null",
          "slugline": {"int_ext": "string|null", "time_of_day": "string|null", "location": "string|null"},
          "lines": [{"character": "string|null", "text": "string"}]
        }
      ]
    }

    Raises:
        SPParseError: 空响应 / JSON 解析失败 / 顶层缺 scenes / 场缺 lines / 类型错。
    """
    if not raw_text or not raw_text.strip():
        raise SPParseError("LLM returned empty response")

    cleaned = _strip_markdown_fence(raw_text)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise SPParseError("LLM response is not valid JSON", cause=exc) from exc

    if not isinstance(data, dict):
        raise SPParseError("LLM response JSON is not a dict object")

    if "scenes" not in data:
        raise SPParseError("LLM response missing required field: 'scenes'")

    raw_scenes = data["scenes"]
    if not isinstance(raw_scenes, list):
        raise SPParseError("LLM response 'scenes' is not a list")

    parsed_scenes: list[ParsedScene] = []
    for i, scene_dict in enumerate(raw_scenes):
        if not isinstance(scene_dict, dict):
            raise SPParseError(f"scenes[{i}] is not a dict")

        if "lines" not in scene_dict:
            raise SPParseError(f"scenes[{i}] missing required field: 'lines'")

        # scene_code：缺省 None（常态合法值）
        scene_code_raw = scene_dict.get("scene_code")
        if isinstance(scene_code_raw, str):
            scene_code: str | None = scene_code_raw.strip() or None
        else:
            scene_code = None

        # slugline：缺省三字段全 None
        slugline_raw = scene_dict.get("slugline") or {}
        slugline = Slugline(
            int_ext=slugline_raw.get("int_ext") if isinstance(slugline_raw, dict) else None,
            time_of_day=slugline_raw.get("time_of_day") if isinstance(slugline_raw, dict) else None,
            location=slugline_raw.get("location") if isinstance(slugline_raw, dict) else None,
        )

        # lines
        raw_lines = scene_dict["lines"]
        if not isinstance(raw_lines, list):
            raise SPParseError(f"scenes[{i}].lines is not a list")

        parsed_lines: list[ParsedLine] = []
        for j, line_dict in enumerate(raw_lines):
            if not isinstance(line_dict, dict):
                raise SPParseError(f"scenes[{i}].lines[{j}] is not a dict")

            character_raw = line_dict.get("character")
            character: str | None = character_raw if isinstance(character_raw, str) else None

            text_raw = line_dict.get("text", "")
            text = str(text_raw) if text_raw is not None else ""

            parsed_lines.append(ParsedLine(character=character, text=text))

        parsed_scenes.append(
            ParsedScene(scene_code=scene_code, slugline=slugline, lines=parsed_lines)
        )

    return parsed_scenes


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------


async def run_sp_parse(
    raw_text: str,
    llm_service: "LLMService",
    *,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    timeout: float = 60.0,
) -> list[ParsedScene]:
    """执行剧本解析：raw_text → list[ParsedScene]。

    Args:
        raw_text: 剧本原始文本（粘贴/文件提取/OCR 文本）。
        llm_service: 注入的 LLMService 实例。
        chunk_size: 单块字符上限，超过则分块（默认 1500）。
        timeout: 每次 infer 最大等待时间（默认 60s）。

    Returns:
        ParsedScene 列表，按输入顺序。空输入返回 []。

    Raises:
        SPParseError: LLM 输出非合法 JSON / 顶层缺 scenes / 场缺 lines / 类型错。
        asyncio.TimeoutError: infer 超时，不吞，让 caller 感知。
    """
    if not raw_text or not raw_text.strip():
        return []

    system_prompt = _build_system_prompt()
    chunks = _split_into_chunks(raw_text, chunk_size)

    all_scenes: list[ParsedScene] = []

    for chunk in chunks:
        if not chunk.strip():
            continue

        user_message = _build_user_message(chunk)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        raw_output: str = await llm_service.infer(
            messages,
            task_type="script_parse",
            priority=3,
            timeout=timeout,
        )

        chunk_scenes = _parse_chunk_output(raw_output)
        all_scenes.extend(chunk_scenes)

    return all_scenes
