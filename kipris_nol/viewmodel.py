"""GUI 표시용 순수 함수(tkinter 비의존 → 완전 테스트)."""
from __future__ import annotations

from pathlib import Path

from . import accounting

APP_DIRNAME = "KIPRIS-NOL"
DISPLAY_COLUMNS = ["출원번호", "명칭", "자산상태", "회계계정", "취득원가", "판정근거"]


def default_output_dir() -> Path:
    return Path.home() / "Documents" / APP_DIRNAME


def _fmt_cost(v) -> str:
    if v is None or v == "":
        return ""
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return str(v)


def result_rows(rows: list[dict]) -> list[list[str]]:
    out: list[list[str]] = []
    for r in rows:
        out.append([
            r.get("application_number", ""),
            r.get("mark_name") or r.get("right_label") or "",
            r.get("asset_status", ""),
            r.get("account") or "",
            _fmt_cost(r.get("acquisition_cost")),
            r.get("basis", ""),
        ])
    return out


def summary_banner(rows: list[dict]) -> str:
    sums = accounting.summarize(rows)
    order = ["등록", "대기", "탈락", "검토필요", "unsupported"]
    parts = [f"{b} {sums[b]['count']}건" for b in order if b in sums]
    asset = sums.get("등록", {}).get("cost_sum", 0.0)
    expense = sums.get("탈락", {}).get("cost_sum", 0.0)
    head = " / ".join(parts) if parts else "결과 없음"
    return f"{head}  ·  자산화 {asset:,.0f} / 비용 {expense:,.0f}"
