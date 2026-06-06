"""场记单 CSV 导出（feat/export）。

纯函数 + 行装配，FastAPI 路由（backend/api/routes/takes.py: GET /takes/export）调用。

设计要点：
  - FileName 列：port frontend-design 的 DEFAULT_FILENAME_FORMAT 默认约定（01_S1_T001）。
    后端只生成默认板式名；用户可配格式是尚未合并到 main 的纯前端 localStorage 偏好，
    不在此重复实现，避免两份 formatFileName 漂移。等其合并后由前端把格式参数传进来即可。
  - Lines 列：本 take 的 ch1 transcript 段文本（对白）拼接，ch2 备注不计入。
  - CSV：走标准库 csv 模块（正确转义逗号/引号/换行），utf-8-sig 落 BOM（Excel 中文不乱码），
    首行是非表格的「导出日期」行（有意，满足「文件最顶部加导出日期」），第二行才是表头。
"""
from __future__ import annotations

import codecs
import csv
import io
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.db.dal import DAL

# 表头固定（顺序即列序）。第二行写它，第一行留给导出日期。
CSV_HEADER = ["Scene", "Shot", "Take", "FileName", "Note", "Lines", "Mark"]

# status → Mark 列显示（对齐前端 STATUS_LABEL：大写英文）。
_MARK_LABELS = {"pass": "PASS", "ng": "NG", "keep": "KEEP", "tbd": "TBD"}


@dataclass
class ExportRow:
    """CSV 一条数据行（与 CSV_HEADER 一一对应）。"""

    scene: str
    shot: str
    take: str
    file_name: str
    note: str
    lines: str
    mark: str


def _digits_of(code: str) -> str:
    """从 scene_code（'Scene_1' / '3A' / '1'）抠首段数字；抠不到则原样返回（对齐前端 digitsOf）。"""
    m = re.search(r"\d+", code)
    return m.group(0) if m else code


def _pad(value: str, width: int) -> str:
    """左补零到 width 位（width=0 不补，对齐前端 padNum/padStart）。"""
    return value.zfill(width) if width > 0 else value


def default_take_filename(scene_code: str | None, shot: str | None, take_number: int | None) -> str:
    """按默认板式约定生成文件名（01_S1_T001）。缺失段跳过，不留空分隔。

    默认格式（DEFAULT_FILENAME_FORMAT）：scene 前缀'' pad2 / shot 前缀'S' pad0 /
    take 前缀'T' pad3 / 段间分隔 '_'。take 只用 take_number（不带冲突后缀），与前端
    formatFileName 一致；后缀只进 Take 列。
    """
    segs: list[str] = []
    if scene_code:
        segs.append(_pad(_digits_of(scene_code), 2))
    if shot:
        segs.append("S" + shot)
    if take_number is not None:
        segs.append("T" + _pad(str(take_number), 3))
    return "_".join(segs)


def _take_label(take_number: int, take_suffix: str) -> str:
    """Take 列显示：编号拼冲突后缀（对齐前端 formatTakeLabel，suffix='' → '3'，'+' → '3+'）。"""
    return f"{take_number}{take_suffix or ''}"


def build_export_rows(dal: DAL) -> list[ExportRow]:
    """从 DAL 装配导出行（一趟 join takes+scenes + 一趟 ch1 段，不 N+1）。

    排序：scene_code → shot → take_number → take_suffix。软删行已被 list_takes 排除。
    """
    scene_codes = {s["scene_id"]: s["scene_code"] for s in dal.list_scenes()}
    lines_by_take = dal.list_ch1_texts_by_take()

    rows: list[ExportRow] = []
    for t in dal.list_takes(None):
        scene_code = scene_codes.get(t.scene_id, f"#{t.scene_id}")
        rows.append(
            ExportRow(
                scene=scene_code,
                shot=t.shot,
                take=_take_label(t.take_number, t.take_suffix),
                file_name=default_take_filename(scene_code, t.shot, t.take_number),
                note=t.notes or "",
                # Lines：本 take 的 ch1 段（对白）按 start_frame 升序拼接；ch2 备注不计入。
                lines="\n".join(lines_by_take.get(t.take_id, [])),
                mark=_MARK_LABELS.get(t.status, t.status.upper()),
            )
        )
    rows.sort(key=lambda r: (r.scene, r.shot, _take_sort_key(r.take)))
    return rows


def _take_sort_key(take_label: str) -> tuple[int, str]:
    """把 Take 列（'3' / '3+'）拆成 (数字, 后缀) 排序键，让 3 排在 3+ 前、10 排在 9 后。"""
    m = re.match(r"(\d+)(.*)", take_label)
    if m:
        return int(m.group(1)), m.group(2)
    return 0, take_label


def rows_to_csv(rows: list[ExportRow], export_date: str) -> bytes:
    """把导出行序列化为 CSV 字节（utf-8-sig BOM；首行导出日期，次行表头）。

    首行是单格非表格行「导出日期：YYYY-MM-DD」（有意，满足「文件最顶部加导出日期」），
    第二行才是固定表头。走 csv 模块，逗号/引号/换行自动转义；utf-8-sig 落 BOM 让 Excel
    正确识别中文。
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"导出日期：{export_date}"])
    writer.writerow(CSV_HEADER)
    for r in rows:
        writer.writerow([r.scene, r.shot, r.take, r.file_name, r.note, r.lines, r.mark])
    return codecs.BOM_UTF8 + buf.getvalue().encode("utf-8")
