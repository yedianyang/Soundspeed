"""场记单 CSV 导出（feat/export）测试。

覆盖三层：
  1. default_take_filename —— port frontend-design DEFAULT_FILENAME_FORMAT 默认约定（01_S1_T001）。
  2. build_export_rows / rows_to_csv —— 行装配（一趟 join，不 N+1）+ CSV 序列化（BOM、转义、首行日期）。
  3. GET /api/v1/takes/export 端点 —— text/csv + attachment + 路由顺序（先于 /takes/{take_id}）。

Lines 列 = 本 take 的 ch1 transcript 段文本（对白），ch2 备注不计入（见 export.py 注释）。
"""
from __future__ import annotations

import codecs
import csv
import io

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.core.export import (
    CSV_HEADER,
    build_export_rows,
    default_take_filename,
    rows_to_csv,
)
from backend.core.orchestrator import create_orchestrator
from backend.db.dal import DAL

_TOKEN = "test-admin-token"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}


# ── default_take_filename（板式命名，port 默认格式）─────────────────────────────


def test_default_filename_matches_referenced_default_format():
    # DEFAULT_FILENAME_FORMAT：scene 前缀'' pad2 / shot 前缀'S' pad0 / take 前缀'T' pad3 / sep '_'。
    assert default_take_filename("Scene_1", "1", 1) == "01_S1_T001"


def test_default_filename_digits_extracted_and_shot_passthrough():
    # scene_code 抠数字（Scene_12→12），shot 原样带前缀（2B→S2B），take 补零到三位。
    assert default_take_filename("Scene_12", "2B", 3) == "12_S2B_T003"


def test_default_filename_skips_empty_shot():
    # shot 为 '' → 跳过该段，不留空分隔。
    assert default_take_filename("Scene_3", "", 5) == "03_T005"


def test_default_filename_excludes_take_suffix():
    # FileName 用 take_number（不带冲突后缀），与前端 formatFileName 一致；后缀只进 Take 列。
    assert default_take_filename("Scene_1", "1", 7) == "01_S1_T007"


# ── rows_to_csv（CSV 序列化）──────────────────────────────────────────────────


def _parse_csv(blob: bytes) -> list[list[str]]:
    assert blob.startswith(codecs.BOM_UTF8), "缺 UTF-8 BOM（Excel 打开中文会乱码）"
    text = blob.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def test_rows_to_csv_first_line_is_export_date():
    blob = rows_to_csv([], "2026-06-06")
    grid = _parse_csv(blob)
    # 首行是非表格的导出日期行（有意：满足「文件最顶部加导出日期」）。
    assert grid[0] == ["导出日期：2026-06-06"]


def test_rows_to_csv_second_line_is_fixed_header():
    blob = rows_to_csv([], "2026-06-06")
    grid = _parse_csv(blob)
    assert grid[1] == CSV_HEADER
    assert CSV_HEADER == ["Scene", "Shot", "Take", "FileName", "Note", "Lines", "Mark"]


def test_rows_to_csv_escapes_commas_quotes_newlines():
    from backend.core.export import ExportRow

    row = ExportRow(
        scene="Scene_1",
        shot="1",
        take="1",
        file_name="01_S1_T001",
        note='含逗号,和"引号"和\n换行',
        lines="第一句\n第二句",
        mark="PASS",
    )
    blob = rows_to_csv([row], "2026-06-06")
    grid = _parse_csv(blob)
    # 解析回来字段必须完整无损（证明走 csv 模块而非字符串拼接）。
    data = grid[2]
    assert data[4] == '含逗号,和"引号"和\n换行'
    assert data[5] == "第一句\n第二句"


# ── build_export_rows（行装配，DAL join）──────────────────────────────────────


@pytest.fixture
def tmp_dal(tmp_path):
    d = DAL(tmp_path / "export.db")
    try:
        yield d
    finally:
        d.close()


def _seed_take(dal: DAL, scene_code: str, shot: str, *, status="tbd", notes=None,
               ch1=(), ch2=()):
    scene_id, _ = dal.get_or_create_scene(scene_code)
    take_id, take_number = dal.start_take(scene_id, shot, start_ts=1000.0)
    dal.end_take(take_id, end_ts=1001.0)
    if status != "tbd":
        dal.set_take_status(take_id, status)
    if notes is not None:
        dal.update_take_meta(take_id, notes=notes)
    for i, text in enumerate(ch1):
        dal.insert_segment(take_id, 1, "A", text, start_frame=i * 1000, end_frame=i * 1000 + 500)
    for i, text in enumerate(ch2):
        dal.insert_segment(take_id, 2, None, text, start_frame=i * 1000, end_frame=i * 1000 + 500)
    return take_id, take_number


def test_build_export_rows_maps_all_columns(tmp_dal: DAL):
    _seed_take(
        tmp_dal, "Scene_1", "1",
        status="pass",
        notes="一条备注",
        ch1=("第一句台词", "第二句台词"),
        ch2=("语音备注忽略",),
    )
    rows = build_export_rows(tmp_dal)
    assert len(rows) == 1
    r = rows[0]
    assert r.scene == "Scene_1"
    assert r.shot == "1"
    assert r.take == "1"
    assert r.file_name == "01_S1_T001"
    assert r.note == "一条备注"
    # Lines：只取 ch1，按 start_frame 升序拼接，ch2 不计入。
    assert r.lines == "第一句台词\n第二句台词"
    assert r.mark == "PASS"


def test_build_export_rows_lines_excludes_ch2(tmp_dal: DAL):
    _seed_take(tmp_dal, "Scene_2", "", ch1=("only ch1",), ch2=("memo",))
    rows = build_export_rows(tmp_dal)
    assert rows[0].lines == "only ch1"


def test_build_export_rows_orders_by_scene_shot_take(tmp_dal: DAL):
    _seed_take(tmp_dal, "Scene_2", "1")
    _seed_take(tmp_dal, "Scene_1", "2")
    _seed_take(tmp_dal, "Scene_1", "1")
    rows = build_export_rows(tmp_dal)
    keys = [(r.scene, r.shot) for r in rows]
    assert keys == [("Scene_1", "1"), ("Scene_1", "2"), ("Scene_2", "1")]


def test_build_export_rows_excludes_soft_deleted(tmp_dal: DAL):
    take_id, _ = _seed_take(tmp_dal, "Scene_1", "1")
    _seed_take(tmp_dal, "Scene_1", "2")
    tmp_dal.delete_take(take_id)
    rows = build_export_rows(tmp_dal)
    assert [r.shot for r in rows] == ["2"]


def test_build_export_rows_empty_when_no_takes(tmp_dal: DAL):
    assert build_export_rows(tmp_dal) == []


# ── 端点（GET /api/v1/takes/export）────────────────────────────────────────────


def _client(tmp_dal: DAL, monkeypatch) -> TestClient:
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    app = create_app(create_orchestrator(tmp_dal))
    return TestClient(app)


def test_export_endpoint_returns_csv_attachment(tmp_dal: DAL, monkeypatch):
    _seed_take(tmp_dal, "Scene_1", "1", status="keep", ch1=("台词",))
    c = _client(tmp_dal, monkeypatch)
    res = c.get("/api/v1/takes/export", headers=_HEADERS)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers.get("content-disposition", "")
    body = res.content
    assert body.startswith(codecs.BOM_UTF8)
    grid = _parse_csv(body)
    assert grid[1] == CSV_HEADER
    # 数据行存在，Mark 映射为大写。
    assert grid[2][6] == "KEEP"


def test_export_route_registered_before_take_id(tmp_dal: DAL, monkeypatch):
    # 若 /takes/export 排在 /takes/{take_id} 之后，FastAPI 会把 "export" 当 take_id 解析 → 422。
    # 断言 200 即证明导出路由先注册、未被 {take_id} 吞掉。
    c = _client(tmp_dal, monkeypatch)
    res = c.get("/api/v1/takes/export", headers=_HEADERS)
    assert res.status_code == 200


def test_export_endpoint_requires_auth(tmp_dal: DAL, monkeypatch):
    c = _client(tmp_dal, monkeypatch)
    res = c.get("/api/v1/takes/export")
    assert res.status_code in (401, 403)
