"""NP Pipeline 单元测试。"""
from backend.pipelines.np_note import (
    NPInput,
    NPParseError,
    _parse_llm_output,
    _build_user_message,
    _build_system_prompt,
)


def test_parse_valid_json():
    output = _parse_llm_output('{"take_id": 1, "category": "issue", "content": "灯光有问题"}')
    assert output.take_id == 1
    assert output.category == "issue"
    assert output.content == "灯光有问题"


def test_parse_default_category():
    output = _parse_llm_output('{"take_id": 2, "content": "test"}')
    assert output.category == "note"
    assert output.content == "test"


def test_parse_markdown_fenced():
    output = _parse_llm_output('```json\n{"take_id": 3, "category": "keeper", "content": ""}\n```')
    assert output.take_id == 3
    assert output.category == "keeper"
    assert output.content == ""


def test_parse_empty_raises():
    try:
        _parse_llm_output("")
        assert False, "should raise"
    except NPParseError:
        pass


def test_parse_invalid_json_raises():
    try:
        _parse_llm_output("not json")
        assert False, "should raise"
    except NPParseError:
        pass


def test_parse_invalid_category_raises():
    try:
        _parse_llm_output('{"take_id": 1, "category": "invalid"}')
        assert False, "should raise"
    except NPParseError:
        pass


def test_parse_take_id_not_int_raises():
    try:
        _parse_llm_output('{"take_id": "abc", "category": "note"}')
        assert False, "should raise"
    except NPParseError:
        pass


def test_build_user_message_full_scene_shot_take():
    """4.H：当前活跃 take + 历史 take 都渲染成完整 场/镜/次，不再暴露 DB 内部 id 当主键。"""
    input_data = NPInput(
        raw_text="飞机声",
        parsed_category="note",
        current_scene_id=1,
        current_take_id=5,
        take_context=[
            {"take_id": 3, "scene_code": "Scene_1", "shot": "Shot1", "take_number": 1, "summary": "正常"},
            {"take_id": 4, "scene_code": "Scene_1", "shot": "Shot1", "take_number": 2, "summary": "有噪音"},
        ],
        ts=1000.0,
        current_scene_code="Scene_1",
        current_shot="Shot1",
        current_take_number=3,
    )
    msg = _build_user_message(input_data)
    assert "飞机声" in msg
    # 当前活跃 take 渲染成 场/镜/次（第3条），不是裸 DB id
    assert "Scene_1/Shot1/第3条" in msg
    assert "活跃 take ID: 5" not in msg
    # 历史 take 带 shot（per-shot 语义），仍保留 take_id 供模型回引
    assert "Scene_1/Shot1/第1条" in msg
    assert "take_id=3" in msg
    assert "正常" in msg


def test_build_user_message_no_active_take():
    """4.H：无活跃录制时显式注明，历史 take 仍带完整 场/镜/次。"""
    input_data = NPInput(
        raw_text="上一条有什么问题",
        parsed_category="note",
        current_scene_id=1,
        current_take_id=None,
        take_context=[
            {"take_id": 3, "scene_code": "Scene_1", "shot": "Shot1", "take_number": 1, "summary": ""},
        ],
        ts=1000.0,
        current_scene_code="Scene_1",
        current_shot=None,
        current_take_number=None,
    )
    msg = _build_user_message(input_data)
    assert "无活跃录制" in msg
    assert "上一条有什么问题" in msg
    assert "Scene_1/Shot1/第1条" in msg


def test_system_prompt_not_empty():
    prompt = _build_system_prompt()
    assert len(prompt) > 50
    assert "take_id" in prompt


def test_system_prompt_per_shot_semantics():
    """4.H：编号规则对齐 per-shot——「第N条」指当前场当前镜的第 N 条，跨镜/跨场需显式。"""
    prompt = _build_system_prompt()
    assert "当前镜" in prompt
    assert "跨镜" in prompt or "跨场" in prompt
