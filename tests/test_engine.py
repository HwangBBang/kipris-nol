"""engine.classify_entries 단위 테스트 (core.call 몽키패치)."""
import unittest
from unittest import mock
from pathlib import Path

from kipris_nol import config, core, engine

FIX = Path(__file__).resolve().parent / "fixtures"
INFO_REG = (FIX / "info_reg.xml").read_text(encoding="utf-8")
INFO_REJ = (FIX / "info_reject.xml").read_text(encoding="utf-8")


def _entries(rows):  # rows = [(appno, cost), ...]
    return [{"application_number": a, "cost": c} for a, c in rows]


class TestClassifyEntries(unittest.TestCase):
    def _run(self, entries, xml_by_appno, **kw):
        with mock.patch.object(core, "call", side_effect=lambda a, s, k, **_: xml_by_appno[a]):
            return engine.classify_entries(entries, "k", source="c", delay=0.0, **kw)

    def test_registered_and_rejected(self):
        rows = self._run(_entries([("40-2024-0133564", 118000), ("40-2025-0233236", 9000)]),
                         {"40-2024-0133564": INFO_REG, "40-2025-0233236": INFO_REJ})
        by = {r["application_number"]: r["asset_status"] for r in rows}
        self.assertEqual(by["40-2024-0133564"], "등록")
        self.assertEqual(by["40-2025-0233236"], "탈락")

    def test_unsupported_makes_no_call(self):
        with mock.patch.object(core, "call") as m:
            rows = engine.classify_entries(_entries([("20-2020-0012345", 5000)]), "k",
                                           source="c", delay=0.0)
        self.assertEqual(m.call_count, 0)
        self.assertEqual(rows[0]["asset_status"], "unsupported")

    def test_duplicate_appno_is_review(self):
        with mock.patch.object(core, "call", return_value=INFO_REG):
            rows = engine.classify_entries(
                _entries([("40-2024-0133564", 1), ("40-2024-0133564", 2)]), "k", delay=0.0)
        self.assertTrue(all(r["asset_status"] == "검토필요" for r in rows))

    def test_per_item_error_is_isolated_review(self):
        def boom(a, s, k, **_):
            raise RuntimeError("network down")
        with mock.patch.object(core, "call", side_effect=boom):
            rows = engine.classify_entries(_entries([("40-2024-0133564", 1)]), "k", delay=0.0)
        self.assertEqual(rows[0]["asset_status"], "검토필요")
        self.assertIn("조회 실패", rows[0]["basis"])

    def test_progress_cb_called_per_item(self):
        seen = []
        with mock.patch.object(core, "call", return_value=INFO_REG):
            engine.classify_entries(_entries([("40-2024-0133564", 1), ("40-2025-0233236", 2)]),
                                    "k", delay=0.0,
                                    progress_cb=lambda idx, total, appno, row: seen.append(idx))
        self.assertEqual(seen, [1, 2])

    def test_should_cancel_stops_early(self):
        with mock.patch.object(core, "call", return_value=INFO_REG):
            rows = engine.classify_entries(
                _entries([("40-2024-0133564", 1), ("40-2025-0233236", 2)]),
                "k", delay=0.0, should_cancel=lambda: True)
        self.assertEqual(rows, [])

    def test_c_mode_auth_error_is_review_row_no_raise(self):  # SHOULD-1: C-모드 인증오류 계약
        rc31 = "<response><header><resultCode>31</resultCode></header><body><items></items></body></response>"
        with mock.patch.object(core, "call", return_value=rc31):
            rows = engine.classify_entries(_entries([("40-2024-0133564", 1)]), "k", source="c", delay=0.0)
        self.assertEqual(rows[0]["asset_status"], "검토필요")  # 예외 없이 강등
        self.assertEqual(rows[0]["result_code"], "31")         # caller가 auth_err 집계에 사용

    def test_b_mode_fatal_auth_raises(self):  # SHOULD-1: B-모드는 30/31 → FatalAuthError 전파
        rc30 = "<response><header><resultCode>30</resultCode></header><body><items></items></body></response>"
        with mock.patch.object(core, "call", return_value=rc30):
            with self.assertRaises(core.FatalAuthError):
                engine.classify_entries(_entries([("40-2024-0133564", 1)]), "k", source="b", delay=0.0)


if __name__ == "__main__":
    unittest.main()
