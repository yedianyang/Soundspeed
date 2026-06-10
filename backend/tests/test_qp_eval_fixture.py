"""qp_eval.jsonl 加载契约 + 种子 DB 不变量(均无模型,常跑)。"""
import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "qp_eval.jsonl"
VALID_CATEGORIES = {"multi_hop", "aggregate", "single_tool", "negative"}


def _load_cases() -> list[dict]:
    with FIXTURE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_fixture_schema() -> None:
    cases = _load_cases()
    assert len(cases) >= 10
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "case id 重复"
    for c in cases:
        assert c["category"] in VALID_CATEGORIES, c["id"]
        assert isinstance(c["question"], str) and c["question"].strip(), c["id"]
        assert isinstance(c["must_contain_all"], list), c["id"]
        assert c["must_contain_all"], f"{c['id']}: must_contain_all 不能为空列表"
        for group in c["must_contain_all"]:
            assert isinstance(group, list) and group, f"{c['id']}: 内层须非空 list"
            assert all(isinstance(s, str) and s for s in group), c["id"]
        assert isinstance(c["must_not_contain"], list), c["id"]
        assert all(isinstance(s, str) and s for s in c["must_not_contain"]), c["id"]


def test_fixture_category_coverage() -> None:
    cats = {c["category"] for c in _load_cases()}
    assert cats == VALID_CATEGORIES, f"四类配比缺: {VALID_CATEGORIES - cats}"
