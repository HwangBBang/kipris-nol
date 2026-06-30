"""CLI 오케스트레이션 테스트 — 네트워크는 mock, accessKey 로더도 mock(실 .env 비의존).

리뷰 확정 결함 회귀 커버:
- 건별 격리: 비-XML 응답(ParseError)은 해당 건만 error, 배치는 계속.
- accessKey 누출 금지: 에러 행/출력 파일에 (인코딩) 키 미노출.
- --limit 0 = 0건, 음수 = 거부.
- resultCode 10 연속 → 버그 신호로 중단(수집분 저장).
- resultCode 30 = 전건 중단(FatalAuthError).
"""
import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kipris_nol import cli, config, core  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
GOOD_40 = (FIX / "sample_40_multi.xml").read_text(encoding="utf-8")
CODE10 = ("<response><header><resultCode>10</resultCode><resultMsg>param error</resultMsg>"
          "</header><body><items></items></body></response>")
CODE30 = ("<response><header><resultCode>30</resultCode><resultMsg>not registered</resultMsg>"
          "</header><body><items></items></body></response>")


def _testset(nums):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump([{"applicationNumber": n, "cost": 0} for n in nums], f)
    f.close()
    return Path(f.name)


def _load_rows(out_dir):
    files = sorted(Path(out_dir).glob("result-*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8"))


class TestRun(unittest.TestCase):
    def _run(self, nums, *, call, limit=None, key="k"):
        out = tempfile.mkdtemp()
        with mock.patch.object(config, "load_access_key", return_value=key), \
             mock.patch.object(core, "call", side_effect=call) if callable(call) and not isinstance(call, str) \
                else mock.patch.object(core, "call", return_value=call):
            rc = cli.run(_testset(nums), Path(out), "json", limit, 0.0)
        return rc, out

    def test_malformed_xml_is_isolated_not_fatal(self):
        def fake(appno, svc, key, **kw):
            return "<response><header>" if appno == "40-1-2" else GOOD_40

        rc, out = self._run(["40-1-1", "40-1-2", "40-1-3"], call=fake)
        self.assertEqual(rc, 0)
        rows = _load_rows(out)
        self.assertEqual([r["status"] for r in rows], ["ok", "error", "ok"])

    def test_accesskey_never_in_error_output(self):
        key = "sek/ret=pls+hide"
        enc = urllib.parse.quote(key, safe="")

        def boom(appno, svc, k, **kw):
            raise ValueError(f"unknown url type: 'http://h/s?accessKey={enc}'")

        rc, out = self._run(["40-1-1"], call=boom, key=key)
        rows = _load_rows(out)
        self.assertEqual(rows[0]["status"], "error")
        self.assertNotIn(key, rows[0]["result_msg"])
        self.assertNotIn(enc, rows[0]["result_msg"])
        self.assertIn("<KEY>", rows[0]["result_msg"])
        blob = sorted(Path(out).glob("result-*.json"))[-1].read_text(encoding="utf-8")
        self.assertNotIn(key, blob)
        self.assertNotIn(enc, blob)

    def test_limit_zero_processes_nothing(self):
        with mock.patch.object(config, "load_access_key", return_value="k"), \
             mock.patch.object(core, "call", return_value=GOOD_40) as m:
            out = tempfile.mkdtemp()
            cli.run(_testset(["40-1-1", "40-1-2"]), Path(out), "json", 0, 0.0)
            self.assertEqual(m.call_count, 0)
        self.assertEqual(_load_rows(out), [])

    def test_negative_limit_rejected(self):
        with mock.patch.object(config, "load_access_key", return_value="k"):
            with self.assertRaises(ValueError):
                cli.run(_testset(["40-1-1"]), Path(tempfile.mkdtemp()), "json", -1, 0.0)

    def test_repeated_code10_aborts_at_threshold(self):
        with mock.patch.object(config, "load_access_key", return_value="k"), \
             mock.patch.object(core, "call", return_value=CODE10) as m:
            out = tempfile.mkdtemp()
            cli.run(_testset([f"40-1-{i}" for i in range(10)]), Path(out), "json", None, 0.0)
            self.assertEqual(m.call_count, config.PARAM_ERROR_ABORT_THRESHOLD)
        rows = _load_rows(out)
        self.assertEqual(len(rows), config.PARAM_ERROR_ABORT_THRESHOLD)
        self.assertTrue(all(r["result_code"] == "10" for r in rows))

    def test_code30_aborts_whole_batch(self):
        with mock.patch.object(config, "load_access_key", return_value="k"), \
             mock.patch.object(core, "call", return_value=CODE30):
            with self.assertRaises(core.FatalAuthError):
                cli.run(_testset(["40-1-1", "40-1-2"]), Path(tempfile.mkdtemp()), "json", None, 0.0)


ACC_FIX = Path(__file__).resolve().parent / "fixtures"
INFO_REG = (ACC_FIX / "info_reg.xml").read_text(encoding="utf-8")
INFO_PEND = (ACC_FIX / "info_pending70.xml").read_text(encoding="utf-8")
INFO_REJ = (ACC_FIX / "info_reject.xml").read_text(encoding="utf-8")


def _acct_testset(rows):  # rows = [(appno, cost), ...]
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump([{"applicationNumber": a, "cost": c} for a, c in rows], f)
    f.close()
    return Path(f.name)


def _load_ledger(out_dir):
    files = sorted(Path(out_dir).glob("ledger-*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8"))


class TestRunAccountingC(unittest.TestCase):
    """상표 C-모드 회귀 안전망 — 리팩터 전후 동일해야 함."""
    def _run(self, rows, xml_by_appno):
        out = tempfile.mkdtemp()
        def fake_call(appno, svc, key, **kw):
            return xml_by_appno[appno]
        with mock.patch.object(config, "load_access_key", return_value="k"), \
             mock.patch.object(core, "call", side_effect=fake_call):
            cli.run_accounting(_acct_testset(rows), Path(out), "both", None, 0.0, "c")
        return _load_ledger(out)

    def test_trademark_c_rows_snapshot(self):
        rows = self._run(
            [("40-2024-0133564", 118000), ("70-2024-0001232", 50000), ("40-2025-0233236", 9000)],
            {"40-2024-0133564": INFO_REG, "70-2024-0001232": INFO_PEND, "40-2025-0233236": INFO_REJ},
        )
        by = {r["application_number"]: r for r in rows}
        reg = by["40-2024-0133564"]
        self.assertEqual((reg["asset_status"], reg["account"], reg["legal_state"]), ("등록", "상표권", "등록"))
        self.assertEqual(reg["recognition_date"], "20260522")
        self.assertEqual(reg["kipris_status"], "등록")
        self.assertEqual(by["70-2024-0001232"]["asset_status"], "대기")
        self.assertEqual(by["40-2025-0233236"]["asset_status"], "탈락")


if __name__ == "__main__":
    unittest.main()
