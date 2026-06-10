"""hop A 工具声明文本渲染 + 工具名集合定义正确性。"""
from backend.pipelines.voice_dispatch_helpers import (
    NOTE_TOOL_NAMES,
    QP_TOOL_NAMES,
    build_hop_a_system,
)
from backend.pipelines.qp_query import _scrape_tool_name


def test_build_hop_a_system_contains_tool_names():
    """build_hop_a_system 返回的 system content 包含 8 工具名(7 QP + note)（从 GGUF 提取的原生声明）。
    因 GGUF 提取需要模型文件，此测试用 patch 替换提取函数，只验 build_hop_a_system 组装逻辑。
    """
    import backend.pipelines.voice_dispatch_helpers as vdh

    # 用 monkeypatch 风格：临时替换 extract_tool_declarations_text
    original = vdh.extract_tool_declarations_text
    vdh.extract_tool_declarations_text = lambda: "<STUB_TOOL_DECL>"
    try:
        sys_prompt = build_hop_a_system(scene_context="Scene 1: 大堂")
        assert "STUB_TOOL_DECL" in sys_prompt
        assert "Scene 1" in sys_prompt
    finally:
        vdh.extract_tool_declarations_text = original


def test_note_qp_tool_names_disjoint():
    assert set(NOTE_TOOL_NAMES).isdisjoint(set(QP_TOOL_NAMES))
    assert len(NOTE_TOOL_NAMES) >= 1   # 至少 structure_note
    assert len(QP_TOOL_NAMES) == 7  # 7 QP 工具(含 list_scenes/get_scene_script),误删即红
    assert "list_scenes" in QP_TOOL_NAMES and "get_scene_script" in QP_TOOL_NAMES


def test_scrape_tool_name_from_hop_a_output():
    """_scrape_tool_name 能抠出 corrected-C3 实测格式的工具名（返回 str | None）。"""
    fake_output = "<|tool_call>call:count_takes{\"scene_id\": 1}"
    result = _scrape_tool_name(fake_output)
    assert result == "count_takes"
