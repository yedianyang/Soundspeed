"""note_extract TASK_CONFIG 注册契约（纯 unit）。"""

from backend.llm.config import TASK_CONFIG
from backend.llm.tools.note_extract import EXTRACT_NP_TOOL_NAME


def test_note_extract_registered() -> None:
    cfg = TASK_CONFIG["note_extract"]
    assert cfg["temperature"] == 0.2
    assert cfg["max_tokens"] == 256
    assert cfg["priority"] == 2
    # forced tool_choice 指向 extract_np
    assert cfg["tool_choice"] == {
        "type": "function",
        "function": {"name": EXTRACT_NP_TOOL_NAME},
    }
    names = [t["function"]["name"] for t in cfg["tools"]]
    assert EXTRACT_NP_TOOL_NAME in names
