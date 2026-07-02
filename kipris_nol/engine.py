"""분류 엔진: entries → ledger rows. 파일/stdout/키로드 결합 없음(CLI·GUI 공용)."""
from __future__ import annotations

import time
from collections import Counter
from datetime import datetime
from typing import Callable

from . import accounting, config, core


class AuthAbortError(RuntimeError):
    """연속 인증오류(30/31) 임계 도달 — 조기 중단. rows = 중단 시점까지 수집된 행."""

    def __init__(self, count: int, rows: list[dict]):
        super().__init__(f"연속 인증오류 {count}건으로 중단")
        self.count = count
        self.rows = rows


def classify_entries(
    entries: list[dict],
    access_key: str,
    *,
    source: str = "c",
    delay: float = config.INTER_CALL_DELAY_SEC,
    progress_cb: Callable[[int, int, str, dict], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    auth_abort_threshold: int | None = None,
) -> list[dict]:
    dups = {a for a, c in Counter(e["application_number"] for e in entries).items() if c > 1}
    rows: list[dict] = []
    total = len(entries)
    auth_streak = 0

    for idx, entry in enumerate(entries, 1):
        if should_cancel and should_cancel():
            break
        appno = entry["application_number"]
        cost_raw = entry["cost"]
        right_code, hist_svc = core.classify(appno)
        queried_at = datetime.now().astimezone().isoformat(timespec="seconds")
        info = config.RIGHT_CODE_INFO.get(right_code)
        called = False

        if info is None:  # 범위 밖 권리구분 → unsupported, 호출 안 함
            b, a = config.UNSUPPORTED_BUCKET
            row = accounting.build_row(
                appno=appno, right_code=right_code, cost_raw=cost_raw, legal_state="-",
                basis=f"권리구분 '{right_code}' v1 범위 밖", right_label="",
                bucket=b, account=a, source_mode=source.upper(), queried_at=queried_at)
        elif appno in dups:  # 동일 출원번호 중복 → 검토필요
            b, a = config.REVIEW_BUCKET
            row = accounting.build_row(
                appno=appno, right_code=right_code, cost_raw=cost_raw, legal_state="-",
                basis="동일 출원번호 중복 → 검토필요", right_label=info["label"],
                bucket=b, account=a, source_mode=source.upper(), queried_at=queried_at)
        else:
            if source == "c" and "adapter" not in info:
                raise KeyError(f"RIGHT_CODE_INFO['{right_code}'] missing 'adapter' (config 마이그레이션 누락)")
            called = True
            try:
                if source == "c":
                    row = _classify_c(appno, right_code, cost_raw, access_key, queried_at)
                else:
                    row = _classify_b(appno, right_code, cost_raw, hist_svc, access_key, queried_at)
            except core.FatalAuthError:
                raise
            except Exception as exc:  # noqa: BLE001 — 건별 격리, 키 스크럽
                b, a = config.REVIEW_BUCKET
                row = accounting.build_row(
                    appno=appno, right_code=right_code, cost_raw=cost_raw, legal_state="-",
                    basis=f"조회 실패 → 검토필요: {core._scrub(exc, access_key)}",
                    right_label=info["label"], bucket=b, account=a,
                    source_mode=source.upper(), queried_at=queried_at)

        rows.append(row)
        if progress_cb:
            progress_cb(idx, total, appno, row)
        if auth_abort_threshold and called:  # 무호출 행(unsupported/중복)은 streak에 중립 — 증가도 리셋도 안 함
            if row.get("result_code") in config.FATAL_RESULT_CODES:
                auth_streak += 1
                if auth_streak >= auth_abort_threshold:
                    raise AuthAbortError(auth_streak, rows)
            else:
                auth_streak = 0
        if called and idx < total and not (should_cancel and should_cancel()):
            time.sleep(delay)

    return rows


def _classify_c(appno, right_code, cost_raw, access_key, queried_at) -> dict:
    """C-모드: 권리구분 어댑터로 정보검색 → 확정 분류."""
    adapter = config.SEARCH_ADAPTERS[config.RIGHT_CODE_INFO[right_code]["adapter"]]
    xml = core.call(appno, adapter["service"], access_key, extra_params=adapter["extra"])
    return accounting.classify_c_from_xml(appno, xml, adapter, right_code, cost_raw, queried_at)


def _classify_b(appno, right_code, cost_raw, svc, access_key, queried_at) -> dict:
    """B-모드: 행정처리 이력 마일스톤 추론(보수). 등록은 확정 불가 → 검토필요."""
    label = config.RIGHT_CODE_INFO[right_code]["label"]
    if svc is None:  # 행정처리 이력 미지원 권리구분(특허 등) → 검토필요
        b, a = config.REVIEW_BUCKET
        return accounting.build_row(
            appno=appno, right_code=right_code, cost_raw=cost_raw, legal_state="-",
            basis="B-모드는 상표 전용(행정처리 이력 미지원 권리구분) → 검토필요", right_label=label,
            bucket=b, account=a, source_mode="B", queried_at=queried_at)
    parsed = core.parse(core.call(appno, svc, access_key))
    status = core.decide_status(parsed["result_code"], len(parsed["items"]))
    if status == "fatal":
        raise core.FatalAuthError(parsed["result_code"], parsed["result_msg"])
    if status in ("empty", "error"):
        b, a = config.REVIEW_BUCKET
        return accounting.build_row(
            appno=appno, right_code=right_code, cost_raw=cost_raw, legal_state="-",
            basis=f"조회 결과 {status}(rc={parsed['result_code']!r}) → 검토필요", right_label=label,
            bucket=b, account=a, source_mode="B", queried_at=queried_at,
            result_code=parsed["result_code"], result_msg=parsed["result_msg"])
    legal_state, basis = accounting.derive_legal_state_b_mode(parsed["items"])
    bucket, account, _ = accounting.classify(right_code, legal_state)
    row = accounting.build_row(
        appno=appno, right_code=right_code, cost_raw=cost_raw, legal_state=legal_state, basis=basis,
        right_label=label, bucket=bucket, account=account,
        reg_no=accounting.latest_reg_no(parsed["items"]), source_mode="B", queried_at=queried_at,
        result_code=parsed["result_code"], result_msg=parsed["result_msg"])
    row["raw_items"] = parsed["items"]
    return row


def verify_key(access_key: str) -> str:
    """저장 전 키 확인(설계 §6.3, cx-review 결정 2·4: 상표+특허 2회 프로브). 무료 쿼터 최대 2건 소모.

    반환: "ok"(둘 다 확인) | "ok_no_patent"(상표만 확인 — 특허 미신청/만료/프로브 실패)
          | "auth_30" | "auth_31"(키 자체 문제 — 특허 프로브 생략)
          | "unverified"(30/31 아닌 비정상 응답 — 키 판정 불가) | "network"(연결/파싱 실패)
    저장 허용 여부는 viewmodel.VERIFY_SAVE_OK가 정의한다.
    """
    try:
        xml = core.call(config.VERIFY_APPLICATION_NUMBER, config.TRADEMARK_SEARCH, access_key)
        rc = core.parse(xml)["result_code"]
    except Exception:  # noqa: BLE001 — 네트워크/파싱 실패: 키 판정 불가(오프라인 저장 경로로 안내)
        return "network"
    if rc in config.FATAL_RESULT_CODES:
        return f"auth_{rc}"
    if rc not in ("", "00"):  # 성공도 인증오류도 아닌 응답(한도 초과·파라미터 오류 등) → 단정 금지
        return "unverified"
    adapter = config.SEARCH_ADAPTERS[config.RIGHT_CODE_INFO["10"]["adapter"]]
    try:
        xml2 = core.call(config.VERIFY_PATENT_APPLICATION_NUMBER, adapter["service"], access_key,
                         extra_params=adapter["extra"])
        rc2 = core.parse(xml2)["result_code"]
    except Exception:  # noqa: BLE001 — 상표는 이미 확인됨: 특허 상태만 미확인으로 표시
        return "ok_no_patent"
    return "ok_no_patent" if rc2 in config.FATAL_RESULT_CODES else "ok"
