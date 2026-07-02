"""입력 파서: 엑셀 붙여넣기(탭 우선)·CSV(utf-8-sig→cp949) → [{application_number, cost}]."""
from __future__ import annotations

import csv
import io
import re

_COST_STRIP = re.compile(r"[,\s₩원]")
_APPNO_HINTS = ("출원", "application", "appno")
_COST_HINTS = ("취득", "원가", "cost", "금액", "가격")


def _looks_like_appno(tok: str) -> bool:
    t = tok.replace("-", "").strip()
    return len(t) >= 2 and t[:2].isdigit()


def _clean_cost(cell: str | None) -> str | None:
    if cell is None:
        return None
    s = cell.strip()
    if not s:
        return None
    cleaned = _COST_STRIP.sub("", s)  # ₩·콤마·공백 제거
    # 정리 후 숫자로 해석되면 정리값, 아니면 원문 보존(다운스트림 parse_cost가 검토필요 처리)
    try:
        float(cleaned)
        return cleaned
    except ValueError:
        return s


def _entry(appno: str, cost) -> dict:
    return {"application_number": appno.strip(), "cost": _clean_cost(cost)}


def parse_pasted(text: str) -> list[dict]:
    entries: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "\t" in line:                        # 엑셀 붙여넣기 = 탭 구분(셀 안의 천단위 콤마 안전)
            cells = [c.strip() for c in line.split("\t")]
            appno, cost = cells[0], (cells[1] if len(cells) > 1 else None)
        else:                                   # 콤마 폴백: 출원번호엔 콤마 없음 → 첫 콤마 이후 전부가 금액
            cells = line.split(",")
            appno = cells[0].strip()
            cost = ",".join(cells[1:]).strip() if len(cells) > 1 else None
        if not appno or not _looks_like_appno(appno):  # 헤더/잡음 스킵
            continue
        entries.append(_entry(appno, cost))
    return entries


def _detect_columns(header: list[str]) -> tuple[int, int] | None:
    """헤더행에서 (출원번호열, 취득원가열) 인덱스 감지. 못 찾으면 None(위치 기반 폴백)."""
    cells = [c.strip().lower() for c in header]
    ai = next((i for i, c in enumerate(cells) if any(h in c for h in _APPNO_HINTS)), None)
    ci = next((i for i, c in enumerate(cells) if any(h in c for h in _COST_HINTS)), None)
    if ai is None or ci is None:
        return None
    return ai, ci


def parse_csv(data: bytes) -> list[dict]:
    rows = list(csv.reader(io.StringIO(_decode(data))))
    if not rows:
        return []
    cols = _detect_columns(rows[0])
    if cols is not None:                        # 헤더명 감지 → 재정렬/추가열 방어
        ai, ci = cols
        body = rows[1:]
    else:                                       # 헤더 없음/미감지 → 위치(0=출원번호, 1=취득원가)
        ai, ci = 0, 1
        body = rows
    entries: list[dict] = []
    for cells in body:
        cells = [c.strip() for c in cells]
        if ai >= len(cells) or not cells[ai] or not _looks_like_appno(cells[ai]):
            continue
        cost = cells[ci] if ci < len(cells) else None
        entries.append(_entry(cells[ai], cost))
    return entries


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")
