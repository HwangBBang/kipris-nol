"""핵심 파이프라인 함수 (네트워크 외 전부 순수 함수 → 단위 테스트 가능).

흐름: load_input → classify → call → parse → extract/summarize → row → write.
'덤프 우선' 원칙: parse 는 응답의 모든 자식 필드를 그대로 보존하고,
summarize 는 그 위에서 '현재 행정처분'(최신 documentDate 이벤트)만 요약한다.
"""
from __future__ import annotations

import csv
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config


class FatalAuthError(RuntimeError):
    """resultCode 30/31 — 키 미등록/기한만료. 전건 중단 신호."""

    def __init__(self, code: str, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"resultCode {code}: {msg or '(no message)'}")


# --------------------------------------------------------------------------- #
# 입력
# --------------------------------------------------------------------------- #
def load_input(path: Path | str) -> list[str]:
    """testSet 배열 JSON → 출원번호 리스트. `cost` 는 무시(설계 §3)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("testSet must be a JSON array of {applicationNumber, cost}")
    numbers: list[str] = []
    for i, row in enumerate(data):
        if not isinstance(row, dict) or "applicationNumber" not in row:
            raise ValueError(f"entry {i} missing 'applicationNumber'")
        numbers.append(str(row["applicationNumber"]).strip())
    return numbers


def load_entries(path: Path | str) -> list[dict]:
    """testSet 배열 JSON → [{application_number, cost(raw)}]. cost는 취득원가 산정용으로 보존."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("testSet must be a JSON array of {applicationNumber, cost}")
    entries: list[dict] = []
    for i, row in enumerate(data):
        if not isinstance(row, dict) or "applicationNumber" not in row:
            raise ValueError(f"entry {i} missing 'applicationNumber'")
        entries.append({
            "application_number": str(row["applicationNumber"]).strip(),
            "cost": row.get("cost"),  # 검증은 accounting.parse_cost 에서(누락·비수치·0 → 검토필요)
        })
    return entries


# --------------------------------------------------------------------------- #
# 분류
# --------------------------------------------------------------------------- #
def classify(appno: str) -> tuple[str, dict | None]:
    """출원번호 앞 2자리(권리구분) → (right_code, service_spec).

    service_spec 가 None 이면 미지원(호출하지 않고 unsupported 처리).
    """
    digits = appno.replace("-", "")
    right_code = digits[:2] if len(digits) >= 2 and digits[:2].isdigit() else ""
    return right_code, config.ENDPOINT_REGISTRY.get(right_code)


# --------------------------------------------------------------------------- #
# 호출 (네트워크 — accessKey 는 절대 평문 노출/로그 금지)
# --------------------------------------------------------------------------- #
def _strip_hyphens(appno: str) -> str:
    return appno.replace("-", "")


# accessKey 누출 방지: 'accessKey=...' 토큰을 인코딩 형식과 무관하게 통째로 가린다.
_ACCESSKEY_RE = re.compile(r"(accessKey=)[^&\s'\"]*", re.IGNORECASE)


def _scrub(exc: BaseException | str, key: str) -> str:
    """예외/문자열에서 accessKey 흔적 제거(원문·URL인코딩 모두). 키는 절대 평문 노출 금지."""
    text = str(exc) if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    text = _ACCESSKEY_RE.sub(r"\1<KEY>", text)  # 쿼리 파라미터 통째 가림
    if key:  # 키가 다른 곳에 박혀도 원문·인코딩 변형 모두 제거
        for variant in (key, urllib.parse.quote(key, safe=""), urllib.parse.quote_plus(key)):
            text = text.replace(variant, "<KEY>")
    return text


def build_url(appno: str, svc: dict, access_key: str, *, extra_params: dict | None = None) -> str:
    """요청 URL 생성. accessKey 는 항상 마지막(스크럽/테스트 정합). extra_params 있으면 그 앞에 삽입."""
    params = {svc["param"]: _strip_hyphens(appno)}
    if extra_params:
        params.update(extra_params)
    params["accessKey"] = access_key  # 항상 마지막(스크럽/테스트 정합)
    return f"{config.BASE_URL}/{svc['service_id']}/{svc['operation']}?" + urllib.parse.urlencode(params)


def call(
    appno: str,
    svc: dict,
    access_key: str,
    *,
    timeout: int | None = None,
    retry: int | None = None,
    extra_params: dict | None = None,
) -> str:
    """서비스 GET 후 응답 텍스트 반환. 단일 재시도. 에러 메시지에서 키를 가린다."""
    timeout = config.TIMEOUT_SEC if timeout is None else timeout
    retry = config.RETRY if retry is None else retry
    url = build_url(appno, svc, access_key, extra_params=extra_params)
    last = ""
    for attempt in range(retry + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 — 키 노출 방지 위해 광범위 캡처 후 스크럽
            last = _scrub(exc, access_key)
            if attempt < retry:
                time.sleep(0.5)
    raise RuntimeError(f"request failed for {appno}: {last}")


# --------------------------------------------------------------------------- #
# 파싱 / 추출 / 요약
# --------------------------------------------------------------------------- #
def parse(xml_text: str) -> dict:
    """응답 XML → {result_code, result_msg, items[]}. 자식 필드 전부 보존(덤프 우선)."""
    root = ET.fromstring(xml_text)
    result_code = (root.findtext("header/resultCode") or "").strip()
    result_msg = (root.findtext("header/resultMsg") or "").strip()
    items: list[dict] = []
    for el in root.findall("body/items/relateddocsonfileInfo"):
        items.append({child.tag: (child.text or "").strip() for child in el})
    return {"result_code": result_code, "result_msg": result_msg, "items": items}


def extract(parsed: dict) -> dict:
    """raw_items + item_count 그대로 보존."""
    items = parsed["items"]
    return {"raw_items": items, "item_count": len(items)}


def summarize(items: list[dict]) -> dict | None:
    """현재 행정처분 = documentDate 최댓값 이벤트(동일 날짜면 더 나중 항목). 설계 §9 TBD-2."""
    if not items:
        return None
    # YYYYMMDD 고정폭 → 사전식 비교 = 날짜 비교. 동률은 원래 순서(나중=최신)로 tie-break.
    _, latest = max(enumerate(items), key=lambda p: ((p[1].get("documentDate") or ""), p[0]))
    return {
        "disposition_date": latest.get("documentDate", ""),
        "disposition_title": latest.get("documentTitle", ""),
        "disposition_status": latest.get("status", ""),
        "disposition_step": latest.get("step", ""),
        "registration_number": (latest.get("registrationNumber") or "").strip(),
    }


def decide_status(result_code: str, item_count: int) -> str:
    """resultCode + 건수 → ok / empty / error / fatal."""
    if result_code in config.FATAL_RESULT_CODES:  # 30 / 31
        return "fatal"
    if result_code not in config.NON_ERROR_RESULT_CODES:  # 예: 10 파라미터 오류
        return "error"
    return "ok" if item_count >= 1 else "empty"


# --------------------------------------------------------------------------- #
# 출력
# --------------------------------------------------------------------------- #
ROW_FIELDS = [
    "application_number",
    "right_code",
    "queried_at",
    "service_id",
    "operation",
    "result_code",
    "result_msg",
    "status",
    "item_count",
    "disposition_date",
    "disposition_title",
    "disposition_status",
    "disposition_step",
    "registration_number",
    "raw_items",  # CSV 에서는 JSON 문자열, JSON 출력에서는 실제 배열
]


def write_json(rows: list[dict], path: Path | str) -> None:
    Path(path).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(rows: list[dict], path: Path | str) -> None:
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ROW_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            flat["raw_items"] = json.dumps(row.get("raw_items", []), ensure_ascii=False)
            writer.writerow(flat)
