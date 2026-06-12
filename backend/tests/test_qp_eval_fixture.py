"""qp_eval.jsonl 加载契约 + 种子 DB 不变量(均无模型,常跑)。"""
import json
from pathlib import Path

from backend.tests.qp_eval_seed import CHARACTERS_16, seed_qp_eval_db

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


def test_seed_db_invariants(tmp_dal) -> None:
    seed_qp_eval_db(tmp_dal)
    scenes = tmp_dal.list_scenes_readonly()
    jiangcheng = [s for s in scenes if (s["location"] or "") == "江城家"]
    assert len(jiangcheng) == 12
    assert sum(1 for s in jiangcheng if s["time_of_day"] == "日") == 4
    assert sum(1 for s in jiangcheng if s["time_of_day"] == "夜") == 4
    assert sum(1 for s in jiangcheng if s["time_of_day"] is None) == 4

    no_slug = [s for s in scenes if s["scene_code"] in ("1", "2")]
    assert len(no_slug) == 2 and all(s["location"] is None for s in no_slug), "场1/2不应有 location"

    sid15 = tmp_dal.resolve_scene_id("15")
    assert tmp_dal.count_takes(sid15) == 3  # 4 - 1 软删

    sid16 = tmp_dal.resolve_scene_id("16")
    assert sorted(tmp_dal.list_characters(sid16)) == sorted(CHARACTERS_16)
    info = tmp_dal.get_scene_info(sid16)
    assert info is not None, "场16 get_scene_info 返回 None"
    assert info["character_count"] == 4 and info["int_ext"] == "内"

    hits = tmp_dal.search_script_lines("合同")
    assert len(hits) == 1 and "合同" in hits[0]["text"]


# ── 语音 QP 评测夹具 schema 验证 ─────────────────────────────────────────────

VOICE_FIXTURE = Path(__file__).parent / "fixtures" / "qp_voice_eval.jsonl"


def _load_voice_cases() -> list[dict]:
    with VOICE_FIXTURE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_voice_fixture_schema() -> None:
    cases = _load_voice_cases()
    assert len(cases) >= 1, "语音夹具至少一条"
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "id 重复"
    for c in cases:
        assert isinstance(c.get("id"), str) and c["id"].strip(), f"id 缺失: {c}"
        assert isinstance(c.get("audio"), str) and c["audio"].strip(), f"audio 缺失: {c['id']}"
        assert isinstance(c.get("question"), str) and c["question"].strip(), f"question 缺失: {c['id']}"
        assert isinstance(c.get("must_contain_all"), list), f"{c['id']}: must_contain_all 须为 list"
        for group in c["must_contain_all"]:
            assert isinstance(group, list) and group, f"{c['id']}: 内层须非空 list"
            assert all(isinstance(s, str) and s for s in group), c["id"]
        assert isinstance(c.get("must_not_contain"), list), f"{c['id']}: must_not_contain 须为 list"
        assert all(isinstance(s, str) and s for s in c["must_not_contain"]), c["id"]
        # audio 文件真实存在
        audio_path = Path(__file__).parent / "fixtures" / c["audio"]
        assert audio_path.exists(), f"{c['id']}: audio 文件不存在: {audio_path}"
        # WAV 文件头校验：前 4 字节必须是 RIFF
        header = audio_path.read_bytes()[:4]
        assert header == b"RIFF", f"{c['id']}: WAV 头异常，前4字节={header!r}"
