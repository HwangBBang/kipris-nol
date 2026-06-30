"""회계 분류 — 법적상태 → 버킷·계정, B-모드 상태 도출, 자산대장 행/집계.

설계 Approach C(하이브리드)의 공통 레이어 + B-모드(정보검색 미접근 시 보수 분류).
규칙: [[kipris-accounting-rules]]. **오분류 0 원칙 — 확신 없으면 '검토필요'.**
정보검색(trademarkInfoSearchService) 승인 후 derive 함수만 C용으로 교체하면 됨.
"""
from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config

# 자산대장 출력 필드(실무자 확정 필수항목 + 판정 근거). raw_items는 CSV 제외(JSON 감사용).
LEDGER_FIELDS = [
    "application_number",   # 출원번호 (입력)
    "right_code",           # 권리구분 코드(40/70/...)
    "right_label",          # 권리구분(상표/특허)
    "kipris_status",        # KIPRIS 원본 상태값(정보검색 ApplicationStatus) — 검증용 원천
    "registration_number",  # 등록번호
    "mark_name",            # 상표명/발명명칭 (B-모드에선 미확보 → C/정보검색 필요)
    "recognition_date",     # 자산화 인식일(=등록일; 등록 확정 시에만)
    "acquisition_cost",     # 취득원가(=cost, 부가세 불포함)
    "asset_status",         # 자산상태: 등록/대기/탈락/검토필요/unsupported
    "account",              # 회계계정: 상표권·특허권 / 건설중인자산(무형) / 지급수수료
    "legal_state",          # 표준 법적상태
    "basis",                # 판정 근거(감사 추적)
    "source_mode",          # 상태 출처: B(이력추론) / C(정보검색+교차검증)
    "queried_at",
    "result_code",
    "result_msg",
]


# --------------------------------------------------------------------------- #
# cost 검증 — 누락·비수치·0/음수 → None(→ 검토필요, 합계에 0으로 묻지 않음)
# --------------------------------------------------------------------------- #
def parse_cost(raw) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


# --------------------------------------------------------------------------- #
# B-모드: 행정처리 이력 이벤트 → 보수적 표준 법적상태
# 등록은 B로 확정 불가 → '검토필요'. 명백 취하/포기만 탈락(안전 방향). 거절/무효는 불복 불명 → 검토필요.
# --------------------------------------------------------------------------- #
_WITHDRAW_KW = ("취하", "포기")
_REGISTER_KW = ("설정등록", "등록결정", "등록사정", "등록료")
_REJECT_KW = ("거절결정", "각하", "무효")


def derive_legal_state_b_mode(items: list[dict]) -> tuple[str, str]:
    """행정처리 이력 → (legal_state, basis). C 전환 시 이 함수만 교체."""
    if not items:
        return "검토필요", "행정처리 이력 없음"
    titles = [(it.get("documentTitle") or "").strip() for it in items]

    for t in titles:  # 1) 명백 취하/포기 → 탈락(안전 방향, 위험 비대칭)
        if any(k in t for k in _WITHDRAW_KW):
            return ("취하" if "취하" in t else "포기"), f"이력 '{t}'"

    has_reg_no = any((it.get("registrationNumber") or "").strip() for it in items)
    reg_hit = next((t for t in titles if any(k in t for k in _REGISTER_KW)), None)
    if has_reg_no or reg_hit:  # 2) 등록 신호 → B로는 확정 불가 → 검토필요
        why = "등록번호 부여됨" if has_reg_no else f"등록 문서 '{reg_hit}'"
        return "검토필요", f"{why} — 정보검색 미접근으로 '등록' 확정 불가(B-모드)"

    rej = next((t for t in titles if any(k in t for k in _REJECT_KW)), None)
    if rej:  # 3) 거절/무효 신호 → 불복 여부 불명 → 검토필요
        return "검토필요", f"거절/무효 신호 '{rej}' — 확정 여부 불명(B-모드)"

    return "심사중", f"진행 이벤트 {len(items)}건(최근 '{titles[-1]}')"  # 4) 진행중 → 대기


# --------------------------------------------------------------------------- #
# C-모드: 제네릭 정보검색 파서 + 법적상태 도출
# --------------------------------------------------------------------------- #
def parse_info(xml_text: str, item_xpath: str) -> dict:
    """정보검색 응답 → {result_code, result_msg, info, item_count}.
    info = item_xpath 단일 매칭 레코드 dict. 0건 또는 다건이면 None(검토필요 신호)."""
    root = ET.fromstring(xml_text)
    rc = (root.findtext(".//resultCode") or "").strip()
    rm = (root.findtext(".//resultMsg") or "").strip()
    recs = root.findall(item_xpath)
    info = None
    if len(recs) == 1:
        info = {child.tag: (child.text or "").strip() for child in recs[0]}
    return {"result_code": rc, "result_msg": rm, "info": info, "item_count": len(recs)}


def derive_legal_state(info: dict, fields: dict, status_map: dict,
                       reg_requires: tuple) -> tuple[str, str, str, str, str]:
    """응답 레코드 → (legal_state, basis, title, reg_no, reg_date).
    status_map 미수록 → 검토필요. state==등록인데 reg_requires 필드 공란 → 일관성위반 검토필요."""
    field_label = fields["status"]
    status = (info.get(field_label) or "").strip()
    title = (info.get(fields["title"]) or "").strip()
    reg_no = (info.get(fields["reg_no"]) or "").strip()
    reg_date = (info.get(fields["reg_date"]) or "").strip()
    state = status_map.get(status)
    if state is None:
        return "검토필요", f"미수록 {field_label} '{status}'", title, reg_no, reg_date
    if state == "등록":
        vals = {"reg_no": reg_no, "reg_date": reg_date}
        if any(not vals[k] for k in reg_requires):
            return "검토필요", f"{field_label}=등록이나 등록번호/등록일 누락(일관성 위반)", title, reg_no, reg_date
    return state, f"정보검색 {field_label}='{status}'", title, reg_no, reg_date


def parse_trademark_info(xml_text: str) -> dict:
    """상표 정보검색 thin wrapper(기존 시그니처 보존)."""
    return parse_info(xml_text, ".//TradeMarkInfo")


def derive_legal_state_c_mode(info: dict) -> tuple[str, str, str, str, str]:
    """상표 thin wrapper(기존 시그니처 보존)."""
    a = config.SEARCH_ADAPTERS["상표"]
    return derive_legal_state(info, a["fields"], a["status_map"], a["reg_requires"])


def classify_c_from_xml(appno: str, xml_text: str, adapter: dict, right_code: str,
                         cost_raw, queried_at: str) -> dict:
    """정보검색 응답 XML → 자산대장 행. 어댑터 주입형(상표/특허 공통)."""
    label = config.RIGHT_CODE_INFO[right_code]["label"]
    parsed = parse_info(xml_text, adapter["item_xpath"])
    rc = parsed["result_code"]
    if rc in config.FATAL_RESULT_CODES:  # 인증오류 → 건별 강등(설계 F3)
        b, a = config.REVIEW_BUCKET
        return build_row(appno=appno, right_code=right_code, cost_raw=cost_raw, legal_state="-",
                         basis=f"정보검색 인증오류 rc={rc} → 검토필요(서비스 접근 확인)", right_label=label,
                         bucket=b, account=a, source_mode="C", queried_at=queried_at,
                         result_code=rc, result_msg=parsed["result_msg"])
    if parsed["info"] is None:
        b, a = config.REVIEW_BUCKET
        why = (f"다건({parsed['item_count']}건) 확정불가" if parsed["item_count"] > 1
               else f"결과 없음(rc={rc!r})")
        return build_row(appno=appno, right_code=right_code, cost_raw=cost_raw, legal_state="-",
                         basis=f"정보검색 {why} → 검토필요", right_label=label,
                         bucket=b, account=a, source_mode="C", queried_at=queried_at, result_code=rc)
    legal_state, basis, title, reg_no, reg_date = derive_legal_state(
        parsed["info"], adapter["fields"], adapter["status_map"], adapter["reg_requires"])
    bucket, account, _ = classify(right_code, legal_state)
    return build_row(appno=appno, right_code=right_code, cost_raw=cost_raw, legal_state=legal_state,
                     basis=basis, right_label=label, bucket=bucket, account=account,
                     reg_no=reg_no, mark_name=title,
                     recognition_date=(reg_date if bucket == "등록" else ""), source_mode="C",
                     queried_at=queried_at, result_code=rc,
                     kipris_status=(parsed["info"].get(adapter["fields"]["status"]) or "").strip())


# --------------------------------------------------------------------------- #
# 법적상태 → 버킷·계정
# --------------------------------------------------------------------------- #
def classify(right_code: str, legal_state: str) -> tuple[str, str, str]:
    """(bucket, account, right_label). 범위 밖 권리구분 → unsupported, 미매핑 상태 → 검토필요."""
    info = config.RIGHT_CODE_INFO.get(right_code)
    if info is None:
        return (*config.UNSUPPORTED_BUCKET, "")
    rule = config.BUCKET_RULES.get(legal_state)
    if rule is None:
        return (*config.REVIEW_BUCKET, info["label"])
    bucket, account = rule
    if account == "자산":  # 등록 → 권리구분별 자산계정
        account = info["asset_account"]
    return bucket, account, info["label"]


def latest_reg_no(items: list[dict]) -> str:
    for it in reversed(items or []):
        rn = (it.get("registrationNumber") or "").strip()
        if rn:
            return rn
    return ""


# --------------------------------------------------------------------------- #
# 자산대장 행 생성 (cost 무효 시 검토필요로 격리)
# --------------------------------------------------------------------------- #
def build_row(*, appno, right_code, cost_raw, legal_state, basis, right_label,
              bucket, account, reg_no="", mark_name="", recognition_date="",
              source_mode="B", queried_at="", result_code="", result_msg="",
              kipris_status="") -> dict:
    cost = parse_cost(cost_raw)
    if cost is None:  # cost 무효 → 검토필요(합계 오염 방지)
        bucket, account = config.REVIEW_BUCKET
        basis = (basis + " | " if basis else "") + f"cost 무효({cost_raw!r}) → 검토필요"
    return {
        "application_number": appno,
        "right_code": right_code,
        "right_label": right_label,
        "kipris_status": kipris_status,
        "registration_number": reg_no,
        "mark_name": mark_name,
        "recognition_date": recognition_date,
        "acquisition_cost": cost if cost is not None else "",
        "asset_status": bucket,
        "account": account,
        "legal_state": legal_state,
        "basis": basis,
        "source_mode": source_mode,
        "queried_at": queried_at,
        "result_code": result_code,
        "result_msg": result_msg,
    }


def summarize(rows: list[dict]) -> dict:
    """버킷별 건수 + 유효 cost 합계."""
    sums: dict[str, dict] = {}
    for r in rows:
        b = r["asset_status"]
        agg = sums.setdefault(b, {"count": 0, "cost_sum": 0.0})
        agg["count"] += 1
        c = r["acquisition_cost"]
        if isinstance(c, (int, float)):
            agg["cost_sum"] += c
    return sums


# --------------------------------------------------------------------------- #
# 출력
# --------------------------------------------------------------------------- #
def write_ledger_json(rows: list[dict], path: Path | str) -> None:
    Path(path).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def write_ledger_csv(rows: list[dict], path: Path | str) -> None:
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)  # raw_items 등 추가 키는 extrasaction='ignore'로 자동 제외


# 검수용(실무자) 시트 — 핵심 열만, 한글 헤더. (field, 헤더) 순서가 곧 열 순서.
REVIEW_COLUMNS = [
    ("application_number", "출원번호"),
    ("mark_name", "명칭"),
    ("right_label", "권리구분"),
    ("kipris_status", "KIPRIS상태(원본)"),
    ("asset_status", "자산상태"),
    ("account", "회계계정"),
    ("acquisition_cost", "취득원가(부가세제외)"),
    ("registration_number", "등록번호"),
    ("recognition_date", "자산화인식일"),
    ("basis", "판정근거"),
]


def write_review_csv(rows: list[dict], path: Path | str) -> None:
    """실무자 검수용 CSV — 입력→KIPRIS원본→판정이 한눈에 보이는 핵심 열만."""
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([header for _, header in REVIEW_COLUMNS])
        for row in rows:
            cells = []
            for field, _ in REVIEW_COLUMNS:
                value = row.get(field, "")
                if field == "acquisition_cost" and isinstance(value, (int, float)):
                    value = f"{int(value)}"  # 원화 정수 표기(180000.0 → 180000)
                cells.append(value)
            writer.writerow(cells)
