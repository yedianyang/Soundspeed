"""NP 提取产品路径 smoke（真 E4B，确认 service→note_extract→NPExtraction 接线通）。

默认 skip（GEMMA_MODEL_PATH 未设）。提取准确率由 test_np_extract_eval.py 门控，这里只验接线。
"""

import os

import pytest

from backend.pipelines.np_extract import NPExtraction, run_extract_np

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        not os.environ.get("GEMMA_MODEL_PATH"),
        reason="GEMMA_MODEL_PATH 未设置，跳过真模型 e2e",
    ),
]


@pytest.mark.asyncio
async def test_run_extract_np_real_service() -> None:
    from backend.llm.service import _reset_service, get_service

    _reset_service()
    svc = get_service()
    try:
        out = await run_extract_np(
            "第四进第一次 NG", svc, timeout=120.0,
            context_line="当前：第1场 / 第1进 / 当前活跃第3条；上一条=第2条。",
        )
        assert isinstance(out, NPExtraction)
        assert out.shot_ordinal == 4
        assert out.mark == "ng"
    finally:
        _reset_service()
