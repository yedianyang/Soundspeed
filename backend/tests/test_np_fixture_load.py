"""np_extract.jsonl fixture 加载 + schema 契约（纯 unit，无模型）。

钉住 fixture 文件存在、每行合法 JSON、每条带 utterance/expected/check，expected 只用合法字段。
"""

import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "np_extract.jsonl"

_FIELDS = {
    "scene_ordinal", "shot_ordinal", "take_ordinals",
    "deictic", "mark", "note_text", "note_category",
}


def _load() -> list[dict]:
    with FIXTURE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_fixture_exists_and_nonempty() -> None:
    cases = _load()
    assert len(cases) >= 8


def test_each_case_well_formed() -> None:
    for c in _load():
        assert isinstance(c["utterance"], str) and c["utterance"]
        exp = c["expected"]
        assert set(exp).issubset(_FIELDS), f"expected 含非法字段: {set(exp) - _FIELDS}"
        assert set(c["check"]).issubset(set(exp)), c["utterance"]
        for k, v in exp.items():
            if v == "*":
                assert k == "note_text"
