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


class TestAuthAbort(unittest.TestCase):
    """연속 인증오류(30/31) 조기 중단 — 옵트인(설계 §6.4). 기본값은 기존 동작 그대로."""

    RC30 = "<response><header><resultCode>30</resultCode></header><body><items></items></body></response>"
    OK_REG = INFO_REG  # 모듈 상단에서 이미 로드된 fixtures/info_reg.xml 재사용

    def _appnos(self, n):
        return _entries([(f"40-2024-{i:07d}", 1) for i in range(n)])

    def test_consecutive_reaching_threshold_raises_with_rows(self):
        with mock.patch.object(core, "call", return_value=self.RC30):
            with self.assertRaises(engine.AuthAbortError) as ctx:
                engine.classify_entries(self._appnos(5), "k", delay=0.0, auth_abort_threshold=3)
        self.assertEqual(ctx.exception.count, 3)
        self.assertEqual(len(ctx.exception.rows), 3)  # 4·5번째는 호출 안 함
        self.assertTrue(all(r["result_code"] == "30" for r in ctx.exception.rows))

    def test_streak_resets_on_success(self):
        seq = [self.RC30, self.OK_REG, self.RC30, self.OK_REG, self.RC30]
        with mock.patch.object(core, "call", side_effect=seq):
            rows = engine.classify_entries(self._appnos(5), "k", delay=0.0, auth_abort_threshold=2)
        self.assertEqual(len(rows), 5)  # 연속 2건이 없어 중단 안 함

    def test_below_threshold_returns_all(self):
        seq = [self.RC30, self.RC30, self.OK_REG]
        with mock.patch.object(core, "call", side_effect=seq):
            rows = engine.classify_entries(self._appnos(3), "k", delay=0.0, auth_abort_threshold=3)
        self.assertEqual(len(rows), 3)

    def test_uncalled_rows_do_not_reset_streak(self):  # unsupported/중복은 호출이 없어 streak에 중립
        entries = _entries([("40-2024-0000001", 1), ("20-2020-0000002", 1),
                            ("40-2024-0000003", 1), ("40-2024-0000004", 1)])
        with mock.patch.object(core, "call", return_value=self.RC30):
            with self.assertRaises(engine.AuthAbortError) as ctx:
                engine.classify_entries(entries, "k", delay=0.0, auth_abort_threshold=3)
        self.assertEqual(ctx.exception.count, 3)          # 실호출 3건 전부 인증오류
        self.assertEqual(len(ctx.exception.rows), 4)       # unsupported 행 포함 수집분 전체

    def test_default_none_never_aborts(self):  # 기존 계약 회귀 가드
        with mock.patch.object(core, "call", return_value=self.RC30):
            rows = engine.classify_entries(self._appnos(5), "k", delay=0.0)
        self.assertEqual(len(rows), 5)

    def test_config_threshold_is_3(self):
        self.assertEqual(config.AUTH_ABORT_THRESHOLD, 3)


class TestVerifyKey(unittest.TestCase):
    """engine.verify_key — 저장 전 키 확인(설계 §6.3, cx-review 결정 2: 상표+특허 2회 프로브)."""

    OK_EMPTY = "<response><header><resultCode></resultCode></header><body><items></items></body></response>"
    RC10 = "<response><header><resultCode>10</resultCode></header><body><items></items></body></response>"
    RC30 = "<response><header><resultCode>30</resultCode></header><body><items></items></body></response>"
    RC31 = "<response><header><resultCode>31</resultCode></header><body><items></items></body></response>"

    def test_ok_when_both_services_pass(self):
        with mock.patch.object(core, "call", side_effect=[self.OK_EMPTY, self.OK_EMPTY]) as m:
            self.assertEqual(engine.verify_key("k"), "ok")
        self.assertEqual(m.call_count, 2)  # 상표 → 특허 순 2회

    def test_trademark_auth_30_and_31_skip_patent_probe(self):
        with mock.patch.object(core, "call", side_effect=[self.RC30]) as m:
            self.assertEqual(engine.verify_key("k"), "auth_30")
        self.assertEqual(m.call_count, 1)  # 키 자체가 죽었으면 특허 프로브 생략(쿼터 절약)
        with mock.patch.object(core, "call", side_effect=[self.RC31]):
            self.assertEqual(engine.verify_key("k"), "auth_31")

    def test_trademark_ambiguous_rc_is_unverified(self):  # cx-review 결정 4
        with mock.patch.object(core, "call", side_effect=[self.RC10]) as m:
            self.assertEqual(engine.verify_key("k"), "unverified")
        self.assertEqual(m.call_count, 1)

    def test_patent_missing_is_ok_no_patent(self):  # 상표 ok + 특허 rc30(미신청)
        with mock.patch.object(core, "call", side_effect=[self.OK_EMPTY, self.RC30]):
            self.assertEqual(engine.verify_key("k"), "ok_no_patent")

    def test_patent_probe_failure_is_ok_no_patent(self):  # 특허 프로브 예외 — 상표는 확인됨
        with mock.patch.object(core, "call", side_effect=[self.OK_EMPTY, RuntimeError("x")]):
            self.assertEqual(engine.verify_key("k"), "ok_no_patent")

    def test_network_error_is_network(self):
        with mock.patch.object(core, "call", side_effect=RuntimeError("request failed")):
            self.assertEqual(engine.verify_key("k"), "network")

    def test_malformed_xml_is_network(self):
        with mock.patch.object(core, "call", return_value="not xml"):
            self.assertEqual(engine.verify_key("k"), "network")

    def test_uses_verify_appnos_and_services(self):
        with mock.patch.object(core, "call", side_effect=[self.OK_EMPTY, self.OK_EMPTY]) as m:
            engine.verify_key("k")
        first, second = m.call_args_list
        self.assertEqual(first.args[0], config.VERIFY_APPLICATION_NUMBER)
        self.assertIs(first.args[1], config.TRADEMARK_SEARCH)
        adapter = config.SEARCH_ADAPTERS[config.RIGHT_CODE_INFO["10"]["adapter"]]
        self.assertEqual(second.args[0], config.VERIFY_PATENT_APPLICATION_NUMBER)
        self.assertIs(second.args[1], adapter["service"])


if __name__ == "__main__":
    unittest.main()
