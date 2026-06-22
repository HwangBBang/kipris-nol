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

from . import config, core


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kipris-nol", description="출원번호 → 행정처분 조회 (KIPRIS Plus)")
    parser.add_argument("--input", type=Path, default=config.REPO_ROOT / "testSet.json")
    parser.add_argument("--out-dir", type=Path, default=config.REPO_ROOT / "out")
    parser.add_argument("--format", choices=["both", "csv", "json"], default="both")
    parser.add_argument("--limit", type=int, default=None, help="앞 N건만 조회(스모크 테스트용)")
    parser.add_argument("--delay", type=float, default=config.INTER_CALL_DELAY_SEC,
                        help="호출 간 지연(초)")
    args = parser.parse_args(argv)

    try:
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
