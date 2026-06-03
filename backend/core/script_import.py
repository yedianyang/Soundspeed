"""3.C：剧本入库协调模块（ticket 3.C）。

公共 API：
  ScriptImportDAL  DAL 依赖协议（Protocol），声明 3.C 用到的 DAL surface。
  NoActiveSceneError  单场路径无 active scene 时抛出的领域异常。
  import_scenes    协调函数：list[ParsedScene] → 入库（按场替换/建场）。

设计依据：
  docs/specs/2026-06-03-script-import-sp-pipeline.md §5 + §0 分叉 1

架构约束：
  本模块**调用** DAL 方法，不定义任何 DAL 方法。
  3.x 的 dal.py 保持零改动，待 2.x 合 main 带入 get_or_create_scene。
  mypy 在 3.x 通过 Protocol 类型检查；2.x 合并后真 DAL 结构上满足该 Protocol。

batch_id 注入：
  合成 scene_code（import:<batch_id>:<n>）所需的 batch_id 由调用方传入，
  不在函数内部生成 wall-clock / uuid，保证 append-only 测试可复现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from backend.pipelines.sp_script import ParsedScene


# ---------------------------------------------------------------------------
# 领域异常
# ---------------------------------------------------------------------------


class NoActiveSceneError(Exception):
    """单场路径无 active scene 时抛出，调用方（3.D 端点）转 422。"""


# ---------------------------------------------------------------------------
# DAL 依赖协议
# ---------------------------------------------------------------------------


@runtime_checkable
class ScriptImportDAL(Protocol):
    """3.C 用到的 DAL surface，兼做 DAL 依赖文档。

    3.x 的真 DAL 目前缺 get_or_create_scene（由 2.x 补入），
    但通过此 Protocol 类型检查 3.x 阶段的代码；
    2.x 合并后真 DAL 结构上满足此 Protocol。
    """

    def get_active_scene_id(self) -> int | None:
        """返回当前活跃场次 ID，无则返回 None。"""
        ...

    def get_or_create_scene(
        self,
        scene_code: str,
        *,
        description: str | None = None,
        shoot_date: str | None = None,
        int_ext: str | None = None,
        time_of_day: str | None = None,
        location: str | None = None,
    ) -> tuple[int, bool]:
        """按 scene_code 查找或建场。

        命中 → (existing_id, False)，忽略其余参数，不更新已有行。
        未命中 → INSERT → (new_id, True)。
        并发 IntegrityError 兜底重 SELECT → (id, False)。
        """
        ...

    def insert_script(
        self,
        scene_id: int,
        raw_text: str,
        version: int | None = None,
    ) -> int:
        """插入剧本原文，version=None 时自动取该场 MAX+1，返回 script_id。"""
        ...

    def insert_script_line(
        self,
        script_id: int,
        line_no: int,
        character: str | None,
        text: str,
    ) -> int:
        """插入一行台词，返回 line_id。"""
        ...


# ---------------------------------------------------------------------------
# 核心协调函数
# ---------------------------------------------------------------------------


def import_scenes(
    scenes: "list[ParsedScene]",
    *,
    target: str,
    batch_id: str,
    dal: ScriptImportDAL,
) -> list[tuple[int, int]]:
    """将解析器输出的 ParsedScene 列表入库，按场替换语义（起新版本）。

    Args:
        scenes: 解析器（3.B）输出的场列表，按顺序处理。
        target: 导入目标声明，"current_scene" 走单场路径，其余走多场路径。
        batch_id: 调用方提供的批次标识，用于合成 scene_code（保证可复现）。
        dal: 实现 ScriptImportDAL Protocol 的 DAL 实例（真实 / fake 均可）。

    Returns:
        每场的 (scene_id, script_id) 列表，与输入 scenes 顺序一一对应。

    Raises:
        NoActiveSceneError: target="current_scene" 但无 active scene。

    设计（§5 + §0 分叉 1）：
    - 单场路径（target="current_scene"）→ get_active_scene_id()；
      无 active scene → 抛 NoActiveSceneError（3.D 转 422）。
    - 多场 + scene_code 非 None → get_or_create_scene(scene_code, ...)。
    - 多场 + scene_code None → 合成 import:<batch_id>:<场序号>（append-only），
      再 get_or_create_scene。
    - 入库前行清洗（spec §5 步骤 4）：
      filter text.strip() 为空的行；character 空串归一为 None。
      先过滤，再从 1 连续分配 line_no（无空洞）。
    - raw_text 拼接口径（对齐 debug.py:139）：
      有 character → "character：text"，无 → 直出 text，换行分隔。
    - 不回算已有 take，不更新已有 scene heading（spec §0 分叉 3）。
    """
    is_single = target == "current_scene"

    # 单场路径：提前解析 active scene，非 None 保证后续循环类型安全
    single_scene_id: int | None = None
    if is_single:
        single_scene_id = dal.get_active_scene_id()
        if single_scene_id is None:
            raise NoActiveSceneError("no active scene")

    results: list[tuple[int, int]] = []

    for i, scene in enumerate(scenes):
        # --- 步骤 1：定 scene_id ---
        if is_single:
            assert single_scene_id is not None  # 上方已经校验，给 mypy 类型窄化
            scene_id: int = single_scene_id
        else:
            if scene.scene_code is not None:
                code = scene.scene_code
            else:
                code = f"import:{batch_id}:{i}"

            scene_id, _ = dal.get_or_create_scene(
                code,
                int_ext=scene.slugline.int_ext,
                time_of_day=scene.slugline.time_of_day,
                location=scene.slugline.location,
            )

        # --- 步骤 2+4：行清洗 ---
        # 先 filter（text.strip() 为空的丢弃）；character 空串归一为 None
        valid_lines = [
            (None if (ln.character or "") == "" else ln.character, ln.text)
            for ln in scene.lines
            if ln.text.strip()
        ]

        # --- 步骤 2：组装 raw_text ---
        raw_text = "\n".join(
            f"{character}：{text}" if character else text
            for character, text in valid_lines
        )

        # --- 步骤 2：按场替换（起新版本）---
        script_id = dal.insert_script(scene_id, raw_text)

        # --- 步骤 2：写入 script_lines（line_no 从 1 连续分配）---
        for line_no, (character, text) in enumerate(valid_lines, start=1):
            dal.insert_script_line(script_id, line_no, character, text)

        results.append((scene_id, script_id))

    return results
