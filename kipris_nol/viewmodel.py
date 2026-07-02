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


VERIFY_MESSAGES = {  # engine.verify_key 결과 → 실무자 문구(관리자 전달 모델 — 설계 §6.3 확정 문구)
    "ok": "키가 확인되었습니다. 저장했습니다.",
    "ok_no_patent": "상표 서비스는 확인되었습니다. 특허 서비스는 확인되지 않아(미신청/만료 가능) "
                    "특허 출원번호는 분류되지 않을 수 있습니다. 저장했습니다 — 필요하면 관리자에게 문의하세요.",
    "unverified": "키를 저장했지만 정상 여부는 확인하지 못했습니다(KIPRIS 응답 비정상). "
                  "실행에서 오류가 계속되면 관리자에게 문의하세요.",
    "auth_30": "키가 등록되어 있지 않습니다. 붙여넣은 키에 오타나 빠진 글자가 없는지 확인하고, "
               "그래도 안 되면 키를 전달해 준 관리자에게 문의하세요. (오류 30)",
    "auth_31": "키 사용 기간이 만료되었습니다. 키를 전달해 준 관리자에게 갱신을 요청하세요. (오류 31)",
    "network": "인터넷 연결을 확인해 주세요. 키가 틀린 것이 아닐 수 있습니다. "
               "인터넷이 안 되는 곳이면 [확인 없이 저장]을 눌러 두세요.",
}

VERIFY_SAVE_OK = {"ok", "ok_no_patent", "unverified"}  # cx-review 결정 2·4: 경고 후 저장 허용 포함


def verify_message(result: str) -> str:
    return VERIFY_MESSAGES.get(result, VERIFY_MESSAGES["network"])
