"""CLI 오케스트레이션: testSet → 건별 조회 → 결과표(CSV/JSON).

사용: python -m kipris_nol [--input testSet.json] [--out-dir out] [--format both|csv|json]
                          [--limit N] [--delay 0.4]
accessKey 는 .env 에서만 로드하며 화면/파일/로그에 노출하지 않는다.
30/31(키 미등록/만료)은 전건 중단(fail-fast).
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from collections import Counter

from . import accounting, config, core


def _row(appno, right_code, svc, *, queried_at, parsed=None, status, msg=""):
    base = {
        "application_number": appno,
        "right_code": right_code,
        "queried_at": queried_at,
        "service_id": svc["service_id"] if svc else "",
        "operation": svc["operation"] if svc else "",
        "result_code": parsed["result_code"] if parsed else "",
        "result_msg": (parsed["result_msg"] if parsed else "") or msg,
        "status": status,
        "item_count": 0,
        "disposition_date": "",
        "disposition_title": "",
        "disposition_status": "",
        "disposition_step": "",
        "registration_number": "",
        "raw_items": [],
    }
    if parsed is not None:
        extracted = core.extract(parsed)
        base["item_count"] = extracted["item_count"]
        base["raw_items"] = extracted["raw_items"]
        summary = core.summarize(parsed["items"])
        if summary:
            base.update(summary)
    return base


def run(
    input_path: Path,
    out_dir: Path,
    fmt: str,
    limit: int | None,
    delay: float,
) -> int:
    access_key = config.load_access_key()
    numbers = core.load_input(input_path)
    if limit is not None:
        if limit < 0:
            raise ValueError(f"--limit must be >= 0 (got {limit})")
        numbers = numbers[:limit]

    rows: list[dict] = []
    total = len(numbers)
    consecutive_param_errors = 0
    print(f"[kipris-nol] {total}건 조회 시작 (input={input_path})")

    for idx, appno in enumerate(numbers, 1):
        right_code, svc = core.classify(appno)
        queried_at = datetime.now().astimezone().isoformat(timespec="seconds")

        if svc is None:
            print(f"  [{idx}/{total}] {appno}  권리구분 '{right_code}' 미지원 → unsupported")
            rows.append(_row(appno, right_code, None, queried_at=queried_at,
                             status="unsupported", msg=f"unsupported right_code '{right_code}'"))
            continue

        # 건별 격리: 네트워크/타임아웃/파싱/디코딩 오류는 해당 건만 실패 기록(spec §6).
        # 30/31(FatalAuthError)만 아래에서 전건 중단.
        try:
            xml = core.call(appno, svc, access_key)
            parsed = core.parse(xml)
            status = core.decide_status(parsed["result_code"], len(parsed["items"]))
        except Exception as exc:  # noqa: BLE001 — 키 노출 방지 위해 스크럽 후 기록
            msg = core._scrub(exc, access_key)
            print(f"  [{idx}/{total}] {appno}  처리 실패 → error: {msg}")
            rows.append(_row(appno, right_code, svc, queried_at=queried_at,
                             status="error", msg=msg))
            if idx < total:
                time.sleep(delay)
            continue

        if status == "fatal":  # 30/31 — 전건 동일 실패 → 즉시 중단
            raise core.FatalAuthError(parsed["result_code"], parsed["result_msg"])

        row = _row(appno, right_code, svc, queried_at=queried_at, parsed=parsed, status=status)
        disp = row["disposition_title"] or "-"
        print(f"  [{idx}/{total}] {appno}  {status:11s} items={row['item_count']:<2} 현재처분={disp}")
        rows.append(row)

        # spec §6: 파라미터 오류(10)가 연속 반복되면 버그 신호 → 중단(수집분은 저장).
        if parsed["result_code"] == "10":
            consecutive_param_errors += 1
            if consecutive_param_errors >= config.PARAM_ERROR_ABORT_THRESHOLD:
                print(f"\n[kipris-nol] resultCode 10(파라미터 오류) {consecutive_param_errors}회 연속 "
                      "→ 버그 신호로 판단해 조회를 중단합니다(spec §6). 이미 조회된 건은 저장합니다.")
                break
        else:
            consecutive_param_errors = 0

        if idx < total:
            time.sleep(delay)

    _write_outputs(rows, out_dir, fmt)
    _print_summary(rows)
    return 0


def _write_outputs(rows, out_dir: Path, fmt: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if fmt in ("json", "both"):
        p = out_dir / f"result-{stamp}.json"
        core.write_json(rows, p)
        print(f"[kipris-nol] JSON  → {p}")
    if fmt in ("csv", "both"):
        p = out_dir / f"result-{stamp}.csv"
        core.write_csv(rows, p)
        print(f"[kipris-nol] CSV   → {p}")


def _print_summary(rows) -> None:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"[kipris-nol] 완료: 총 {len(rows)}건 ({parts})")


def run_accounting(input_path: Path, out_dir: Path, fmt: str, limit: int | None,
                   delay: float, source: str = "c") -> int:
    """회계 분류: 출원번호 → 법적상태 → 등록/대기/탈락/검토필요 + 계정 + cost.

    source='c': 상표 정보검색 ApplicationStatus(확정 법적상태). 'b': 행정처리 이력 추론(보수).
    """
    access_key = config.load_access_key()
    entries = core.load_entries(input_path)
    if limit is not None:
        if limit < 0:
            raise ValueError(f"--limit must be >= 0 (got {limit})")
        entries = entries[:limit]

    dups = {a for a, c in Counter(e["application_number"] for e in entries).items() if c > 1}
    rows: list[dict] = []
    total = len(entries)
    src_label = "C/정보검색" if source == "c" else "B/이력추론"
    print(f"[kipris-nol] (accounting / {src_label}) {total}건 분류 시작")

    for idx, entry in enumerate(entries, 1):
        appno = entry["application_number"]
        cost_raw = entry["cost"]
        right_code, hist_svc = core.classify(appno)
        queried_at = datetime.now().astimezone().isoformat(timespec="seconds")
        info = config.RIGHT_CODE_INFO.get(right_code)

        if info is None:  # 범위 밖 권리구분 → unsupported, 호출 안 함
            b, a = config.UNSUPPORTED_BUCKET
            rows.append(accounting.build_row(
                appno=appno, right_code=right_code, cost_raw=cost_raw, legal_state="-",
                basis=f"권리구분 '{right_code}' v1 범위 밖", right_label="",
                bucket=b, account=a, source_mode=source.upper(), queried_at=queried_at))
            print(f"  [{idx}/{total}] {appno}  unsupported (권리구분 {right_code})")
            continue

        if appno in dups:  # 동일 출원번호 중복 → 검토필요(임의 합산/덮어쓰기 금지)
            b, a = config.REVIEW_BUCKET
            rows.append(accounting.build_row(
                appno=appno, right_code=right_code, cost_raw=cost_raw, legal_state="-",
                basis="동일 출원번호 중복 → 검토필요", right_label=info["label"],
                bucket=b, account=a, source_mode=source.upper(), queried_at=queried_at))
            print(f"  [{idx}/{total}] {appno}  검토필요 (중복)")
            continue

        if info is not None and source == "c" and "adapter" not in info:
            raise KeyError(f"RIGHT_CODE_INFO['{right_code}'] missing 'adapter' (config 마이그레이션 누락)")

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
        print(f"  [{idx}/{total}] {appno}  {row['asset_status']:9s} "
              f"계정={row['account'] or '-':16s} ({row['legal_state']})")
        if idx < total:
            time.sleep(delay)

    _write_ledger(rows, out_dir, fmt)
    _print_acct_summary(rows)
    return 0


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


def _write_ledger(rows, out_dir: Path, fmt: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if fmt in ("json", "both"):
        p = out_dir / f"ledger-{stamp}.json"
        accounting.write_ledger_json(rows, p)
        print(f"[kipris-nol] 자산대장 JSON → {p}")
    if fmt in ("csv", "both"):
        p = out_dir / f"ledger-{stamp}.csv"
        accounting.write_ledger_csv(rows, p)
        print(f"[kipris-nol] 자산대장 CSV  → {p}")
        pr = out_dir / f"review-{stamp}.csv"
        accounting.write_review_csv(rows, pr)
        print(f"[kipris-nol] 검수용 CSV   → {pr}")


def _print_acct_summary(rows) -> None:
    sums = accounting.summarize(rows)
    order = ["등록", "대기", "탈락", "검토필요", "unsupported"]
    print(f"[kipris-nol] 분류 완료: 총 {len(rows)}건")
    for b in order + [k for k in sums if k not in order]:
        if b in sums:
            print(f"    {b:11s} {sums[b]['count']:>2}건  cost합계 {sums[b]['cost_sum']:>15,.0f}")
    asset = sums.get("등록", {}).get("cost_sum", 0.0)
    expense = sums.get("탈락", {}).get("cost_sum", 0.0)
    print(f"    → 자산화 합계 {asset:,.0f} / 비용 합계 {expense:,.0f} "
          f"(검토필요·대기·unsupported는 확정 후 재집계)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kipris-nol", description="출원번호 → 회계 분류 (KIPRIS Plus)")
    parser.add_argument("--mode", choices=["accounting", "dump"], default="accounting",
                        help="accounting=등록/대기/탈락 분류(기본), dump=행정처리 이력 원시 덤프")
    parser.add_argument("--input", type=Path, default=config.REPO_ROOT / "testSet.json")
    parser.add_argument("--out-dir", type=Path, default=config.REPO_ROOT / "out")
    parser.add_argument("--format", choices=["both", "csv", "json"], default="both")
    parser.add_argument("--limit", type=int, default=None, help="앞 N건만 조회(스모크 테스트용)")
    parser.add_argument("--delay", type=float, default=config.INTER_CALL_DELAY_SEC,
                        help="호출 간 지연(초)")
    parser.add_argument("--source", choices=["c", "b"], default="c",
                        help="c=정보검색 확정상태(기본), b=행정처리 이력 추론(보수)")
    args = parser.parse_args(argv)

    try:
        if args.mode == "accounting":
            return run_accounting(args.input, args.out_dir, args.format,
                                  args.limit, args.delay, args.source)
        return run(args.input, args.out_dir, args.format, args.limit, args.delay)
    except core.FatalAuthError as exc:
        print(
            f"\n[kipris-nol] 치명 오류 — 전건 중단: {exc}\n"
            "  accessKey 가 미등록(30)이거나 사용기한 만료(31)입니다. "
            ".env 의 키와 서비스 신청 상태를 확인하세요.",
            file=sys.stderr,
        )
        return 2
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n[kipris-nol] 설정 오류: {exc}", file=sys.stderr)
        return 1
