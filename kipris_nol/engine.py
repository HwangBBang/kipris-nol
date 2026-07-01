"""분류 엔진: entries → ledger rows. 파일/stdout/키로드 결합 없음(CLI·GUI 공용)."""
from __future__ import annotations

import time
from collections import Counter
from datetime import datetime
from typing import Callable

from . import accounting, config, core


def classify_entries(
    entries: list[dict],
    access_key: str,
    *,
    source: str = "c",
    delay: float = config.INTER_CALL_DELAY_SEC,
    progress_cb: Callable[[int, int, str, dict], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[dict]:
    dups = {a for a, c in Counter(e["application_number"] for e in entries).items() if c > 1}
    rows: list[dict] = []
    total = len(entries)

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
