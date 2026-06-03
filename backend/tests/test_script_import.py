"""3.C Script Import 单元测试。

覆盖：
  ① 单场 → active scene 插新版本
  ② 多场新场建场（scene_code 非 None，未命中 → 建场）
  ③ 多场老场（scene_code 命中）→ 插新版本，不重复建场
  ④ 多场无号 → append-only（batch_id 钉住，断言合成 code）
  ⑤ scene_code 去重（同 code 两场 → 一个 scene_id）
  ⑥ 行清洗（空 text 行被 filter、空串 character → None）
  ⑦ 无 active scene 单场 → NoActiveSceneError

全部使用 FakeDAL（实现 ScriptImportDAL Protocol），不加载真实数据库 / 模型。
"""

from __future__ import annotations

import pytest

from backend.core.script_import import (
    NoActiveSceneError,
    import_scenes,
)
from backend.pipelines.sp_script import ParsedLine, ParsedScene, Slugline


# ---------------------------------------------------------------------------
# Fake DAL（实现 ScriptImportDAL Protocol）
# ---------------------------------------------------------------------------


class FakeDAL:
    """测试用假 DAL，镜像 ScriptImportDAL Protocol 契约。

    get_or_create_scene 内部 dict 存 scene_code→id，
    命中返 (id, False)，未命中建 (id, True)。
    """

    def __init__(self, active_scene_id: int | None = None) -> None:
        self._active_scene_id = active_scene_id

        # scene_code → scene_id
        self._scenes: dict[str, int] = {}
        self._next_scene_id = 100

        # script records: list of (scene_id, raw_text, version)
        self.scripts: list[tuple[int, str, int]] = []
        # auto version per scene: scene_id → current max version
        self._scene_versions: dict[int, int] = {}
        self._next_script_id = 1000

        # script_lines records: list of (script_id, line_no, character, text)
        self.lines: list[tuple[int, int, str | None, str]] = []

    # --- Protocol 方法 ---

    def get_active_scene_id(self) -> int | None:
        return self._active_scene_id

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
        if scene_code in self._scenes:
            return self._scenes[scene_code], False
        new_id = self._next_scene_id
        self._next_scene_id += 1
        self._scenes[scene_code] = new_id
        return new_id, True

    def insert_script(
        self,
        scene_id: int,
        raw_text: str,
        version: int | None = None,
    ) -> int:
        if version is None:
            version = self._scene_versions.get(scene_id, 0) + 1
        self._scene_versions[scene_id] = version
        script_id = self._next_script_id
        self._next_script_id += 1
        self.scripts.append((scene_id, raw_text, version))
        return script_id

    def insert_script_line(
        self,
        script_id: int,
        line_no: int,
        character: str | None,
        text: str,
    ) -> int:
        self.lines.append((script_id, line_no, character, text))
        return len(self.lines)  # fake line_id


# ---------------------------------------------------------------------------
# 辅助构建函数
# ---------------------------------------------------------------------------


def _scene(
    scene_code: str | None = None,
    lines: list[tuple[str | None, str]] | None = None,
    int_ext: str | None = None,
    time_of_day: str | None = None,
    location: str | None = None,
) -> ParsedScene:
    """快速构建 ParsedScene。lines 是 (character, text) 元组列表。"""
    parsed_lines = [ParsedLine(character=c, text=t) for c, t in (lines or [])]
    return ParsedScene(
        scene_code=scene_code,
        slugline=Slugline(int_ext=int_ext, time_of_day=time_of_day, location=location),
        lines=parsed_lines,
    )


# ---------------------------------------------------------------------------
# ① 单场 → active scene 插新版本
# ---------------------------------------------------------------------------


def test_single_scene_inserts_into_active_scene() -> None:
    """单场路径：插入 active scene，版本自增。"""
    dal = FakeDAL(active_scene_id=1)
    scenes = [
        _scene(scene_code=None, lines=[("张三", "你好"), ("李四", "再见")]),
    ]

    import_scenes(scenes, target="current_scene", batch_id="b1", dal=dal)

    assert len(dal.scripts) == 1
    scene_id, raw_text, version = dal.scripts[0]
    assert scene_id == 1
    assert version == 1

    # line_no 从 1 连续
    assert len(dal.lines) == 2
    assert dal.lines[0][1] == 1  # line_no
    assert dal.lines[1][1] == 2


def test_single_scene_second_import_bumps_version() -> None:
    """单场第二次导入 → version 自增到 2。"""
    dal = FakeDAL(active_scene_id=1)
    scenes = [_scene(lines=[("张三", "第一次")])]
    import_scenes(scenes, target="current_scene", batch_id="b1", dal=dal)

    scenes2 = [_scene(lines=[("张三", "第二次")])]
    import_scenes(scenes2, target="current_scene", batch_id="b1", dal=dal)

    assert len(dal.scripts) == 2
    assert dal.scripts[0][2] == 1  # version 1
    assert dal.scripts[1][2] == 2  # version 2


# ---------------------------------------------------------------------------
# ② 多场新场建场
# ---------------------------------------------------------------------------


def test_multi_scene_creates_new_scenes() -> None:
    """多场，scene_code 未命中 → 建新场。"""
    dal = FakeDAL(active_scene_id=None)
    scenes = [
        _scene(scene_code="3", lines=[("张三", "台词A")], location="咖啡馆"),
        _scene(scene_code="4", lines=[("李四", "台词B")], location="办公室"),
    ]

    import_scenes(scenes, target="multi_scene", batch_id="b1", dal=dal)

    # 两个不同 scene_id
    assert len(dal.scripts) == 2
    sid1 = dal.scripts[0][0]
    sid2 = dal.scripts[1][0]
    assert sid1 != sid2

    # 两个场都建了
    assert "3" in dal._scenes
    assert "4" in dal._scenes


# ---------------------------------------------------------------------------
# ③ 多场老场（scene_code 命中） → 插新版本不重复建场
# ---------------------------------------------------------------------------


def test_multi_scene_existing_code_no_duplicate_scene() -> None:
    """scene_code 命中既有场 → 不建新场，插新版本。"""
    dal = FakeDAL()
    # 先建场
    existing_id, _ = dal.get_or_create_scene("5")

    scenes = [_scene(scene_code="5", lines=[("张三", "重导台词")])]
    import_scenes(scenes, target="multi_scene", batch_id="b2", dal=dal)

    # 没有建新场
    assert len(dal._scenes) == 1
    # script 关联到已有 scene_id
    assert dal.scripts[0][0] == existing_id


# ---------------------------------------------------------------------------
# ④ 多场无号 → append-only（batch_id 钉住断言合成 code）
# ---------------------------------------------------------------------------


def test_multi_scene_no_code_append_only() -> None:
    """scene_code=None → 合成 import:<batch_id>:<n>，建新场（append-only）。"""
    dal = FakeDAL()
    scenes = [
        _scene(scene_code=None, lines=[("张三", "第一场")]),
        _scene(scene_code=None, lines=[("李四", "第二场")]),
    ]

    import_scenes(scenes, target="multi_scene", batch_id="batch-42", dal=dal)

    # 两个合成 code
    assert "import:batch-42:0" in dal._scenes
    assert "import:batch-42:1" in dal._scenes

    # 两个不同 scene_id
    assert dal.scripts[0][0] != dal.scripts[1][0]


def test_multi_scene_no_code_second_import_creates_different_codes() -> None:
    """两次导入，batch_id 不同 → 合成 code 不同 → 各建场（不跨批次合并）。"""
    dal = FakeDAL()
    scenes = [_scene(scene_code=None, lines=[("张三", "台词")])]

    import_scenes(scenes, target="multi_scene", batch_id="batch-A", dal=dal)
    import_scenes(scenes, target="multi_scene", batch_id="batch-B", dal=dal)

    assert "import:batch-A:0" in dal._scenes
    assert "import:batch-B:0" in dal._scenes
    assert len(dal.scripts) == 2


# ---------------------------------------------------------------------------
# ⑤ scene_code 去重（同 code 两场 → 一个 scene_id）
# ---------------------------------------------------------------------------


def test_same_scene_code_dedup() -> None:
    """同 scene_code 的两场 → 命中同一 scene_id，各自插新版本（版本自增）。"""
    dal = FakeDAL()
    scenes = [
        _scene(scene_code="7", lines=[("张三", "台词1")]),
        _scene(scene_code="7", lines=[("张三", "台词2")]),
    ]

    import_scenes(scenes, target="multi_scene", batch_id="b1", dal=dal)

    # 只有一个 scene_id（同一场）
    assert len(dal._scenes) == 1
    sid = list(dal._scenes.values())[0]

    assert dal.scripts[0][0] == sid
    assert dal.scripts[1][0] == sid

    # 版本分别是 1、2
    assert dal.scripts[0][2] == 1
    assert dal.scripts[1][2] == 2


# ---------------------------------------------------------------------------
# ⑥ 行清洗
# ---------------------------------------------------------------------------


def test_empty_text_lines_filtered() -> None:
    """text.strip() 为空的行被过滤，不写入 script_lines。"""
    dal = FakeDAL(active_scene_id=1)
    scenes = [
        _scene(lines=[
            ("张三", "正常台词"),
            ("李四", ""),        # 空串，应被过滤
            ("王五", "   "),     # 纯空白，应被过滤
            (None, "舞台指示"),
        ])
    ]

    import_scenes(scenes, target="current_scene", batch_id="b1", dal=dal)

    # 只有 2 行入库（"正常台词" + "舞台指示"）
    assert len(dal.lines) == 2
    # line_no 从 1 连续，无空洞
    assert dal.lines[0][1] == 1
    assert dal.lines[1][1] == 2


def test_empty_character_normalized_to_none() -> None:
    """character 为空串 → 归一为 None（舞台指示行）。"""
    dal = FakeDAL(active_scene_id=1)
    scenes = [
        _scene(lines=[
            ("", "这行 character 是空串"),
        ])
    ]

    import_scenes(scenes, target="current_scene", batch_id="b1", dal=dal)

    assert len(dal.lines) == 1
    _, _, character, text = dal.lines[0]
    assert character is None
    assert text == "这行 character 是空串"


# ---------------------------------------------------------------------------
# ⑦ 无 active scene 单场 → NoActiveSceneError
# ---------------------------------------------------------------------------


def test_no_active_scene_raises() -> None:
    """单场路径无 active scene → 抛 NoActiveSceneError。"""
    dal = FakeDAL(active_scene_id=None)
    scenes = [_scene(lines=[("张三", "台词")])]

    with pytest.raises(NoActiveSceneError):
        import_scenes(scenes, target="current_scene", batch_id="b1", dal=dal)


# ---------------------------------------------------------------------------
# raw_text 拼接格式验证
# ---------------------------------------------------------------------------


def test_raw_text_format() -> None:
    """raw_text 拼接格式：有 character → '角色：台词'，无 → 直出 text，换行分隔。"""
    dal = FakeDAL(active_scene_id=1)
    scenes = [
        _scene(lines=[
            ("张三", "你好"),
            (None, "（停顿）"),
            ("李四", "再见"),
        ])
    ]

    import_scenes(scenes, target="current_scene", batch_id="b1", dal=dal)

    _, raw_text, _ = dal.scripts[0]
    expected = "张三：你好\n（停顿）\n李四：再见"
    assert raw_text == expected
