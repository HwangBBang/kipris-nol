"""회계 분류 로직 단위 테스트 (순수 함수)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kipris_nol import accounting, config, core  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def _ev(title="", status="", reg=""):
    return {"documentTitle": title, "status": status, "registrationNumber": reg, "documentDate": "20250101"}


class TestParseCost(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(accounting.parse_cost(180000), 180000.0)
        self.assertEqual(accounting.parse_cost("234000"), 234000.0)

    def test_invalid_to_none(self):
        for bad in (None, "", "abc", 0, -5, True, False):
            self.assertIsNone(accounting.parse_cost(bad), f"{bad!r} should be None")


class TestDeriveBMode(unittest.TestCase):
    def test_withdraw_is_taklak(self):
        state, _ = accounting.derive_legal_state_b_mode([_ev("상표등록출원서"), _ev("[취하]취하서")])
        self.assertEqual(state, "취하")

    def test_giveup_is_taklak(self):
        state, _ = accounting.derive_legal_state_b_mode([_ev("권리포기서")])
        self.assertEqual(state, "포기")

    def test_registration_signal_is_review(self):
        # 등록 신호는 B-모드에서 확정 불가 → 검토필요
        state, basis = accounting.derive_legal_state_b_mode([_ev("[설정 특허·등록료][상표]납부서")])
        self.assertEqual(state, "검토필요")
        self.assertIn("등록", basis)

    def test_regno_present_is_review(self):
        state, _ = accounting.derive_legal_state_b_mode([_ev("아무문서", reg="4025417520000")])
        self.assertEqual(state, "검토필요")

    def test_reject_signal_is_review(self):
        state, _ = accounting.derive_legal_state_b_mode([_ev("거절결정서")])
        self.assertEqual(state, "검토필요")

    def test_in_progress_is_simsa(self):
        state, _ = accounting.derive_legal_state_b_mode([_ev("상표등록출원서"), _ev("의견제출통지서")])
        self.assertEqual(state, "심사중")

    def test_empty_is_review(self):
        self.assertEqual(accounting.derive_legal_state_b_mode([])[0], "검토필요")

    def test_on_real_fixtures(self):
        # 실응답 픽스처(2024 스냅샷, 등록 전 진행 상태) → 심사중 도출
        for name in ("sample_40_multi.xml", "sample_70_multi.xml"):
            items = core.parse((FIX / name).read_text(encoding="utf-8"))["items"]
            state, _ = accounting.derive_legal_state_b_mode(items)
            self.assertIn(state, {"심사중", "검토필요", "취하", "포기"})


class TestClassify(unittest.TestCase):
    def test_registered_trademark(self):
        self.assertEqual(accounting.classify("40", "등록"), ("등록", "상표권", "상표"))

    def test_rejected_to_expense(self):
        self.assertEqual(accounting.classify("40", "거절"), ("탈락", "지급수수료", "상표"))
        self.assertEqual(accounting.classify("40", "취하"), ("탈락", "지급수수료", "상표"))

    def test_pending_to_cip(self):
        self.assertEqual(accounting.classify("40", "심사중"), ("대기", "건설중인자산(무형)", "상표"))

    def test_unmapped_state_is_review(self):
        self.assertEqual(accounting.classify("40", "검토필요"), ("검토필요", "", "상표"))

    def test_70_is_trademark(self):
        # 70- = 지정상품추가등록출원(상표) — 정보검색 확인 → 상표로 분류
        self.assertEqual(accounting.classify("70", "심사중"), ("대기", "건설중인자산(무형)", "상표"))

    def test_out_of_scope_right_code(self):
        # 20(실용신안)은 자산계정 미확정 → RIGHT_CODE_INFO 미등록 → unsupported
        self.assertEqual(accounting.classify("20", "심사중"), ("unsupported", "", ""))

    def test_patent_right_code_supported(self):
        # 10(특허)은 Task 2에서 RIGHT_CODE_INFO 등록 → 분류 가능
        self.assertEqual(accounting.classify("10", "심사중"), ("대기", "건설중인자산(무형)", "특허"))


class TestBuildRow(unittest.TestCase):
    def test_valid_cost_kept(self):
        row = accounting.build_row(appno="40-1", right_code="40", cost_raw=180000,
                                   legal_state="심사중", basis="x", right_label="상표",
                                   bucket="대기", account="건설중인자산(무형)")
        self.assertEqual(row["acquisition_cost"], 180000.0)
        self.assertEqual(row["asset_status"], "대기")

    def test_invalid_cost_forces_review(self):
        row = accounting.build_row(appno="40-1", right_code="40", cost_raw=0,
                                   legal_state="심사중", basis="x", right_label="상표",
                                   bucket="대기", account="건설중인자산(무형)")
        self.assertEqual(row["asset_status"], "검토필요")
        self.assertEqual(row["account"], "")
        self.assertEqual(row["acquisition_cost"], "")
        self.assertIn("cost 무효", row["basis"])


class TestSummarize(unittest.TestCase):
    def test_sums_by_bucket(self):
        rows = [
            {"asset_status": "등록", "acquisition_cost": 100.0},
            {"asset_status": "등록", "acquisition_cost": 50.0},
            {"asset_status": "탈락", "acquisition_cost": 30.0},
            {"asset_status": "검토필요", "acquisition_cost": ""},
        ]
        s = accounting.summarize(rows)
        self.assertEqual(s["등록"], {"count": 2, "cost_sum": 150.0})
        self.assertEqual(s["탈락"]["cost_sum"], 30.0)
        self.assertEqual(s["검토필요"]["count"], 1)
        self.assertEqual(s["검토필요"]["cost_sum"], 0.0)


class TestLoadEntries(unittest.TestCase):
    def test_real_testset_carries_cost(self):
        entries = core.load_entries(config.REPO_ROOT / "testSet.json")
        self.assertEqual(len(entries), 25)
        self.assertTrue(all("cost" in e and "application_number" in e for e in entries))
        self.assertEqual(entries[2]["application_number"], "40-2025-0233236")
        self.assertEqual(entries[2]["cost"], 210000)


class TestCMode(unittest.TestCase):
    def _info(self, name):
        return accounting.parse_trademark_info((FIX / name).read_text(encoding="utf-8"))

    def test_parse_registered(self):
        p = self._info("info_reg.xml")
        self.assertEqual(p["result_code"], "")
        self.assertIsNotNone(p["info"])
        self.assertEqual(p["info"]["ApplicationStatus"], "등록")

    def test_derive_registered(self):
        state, basis, mark, reg_no, reg_date = accounting.derive_legal_state_c_mode(self._info("info_reg.xml")["info"])
        self.assertEqual(state, "등록")
        self.assertEqual(mark, "NOL")
        self.assertEqual(reg_no, "4025487260000")
        self.assertEqual(reg_date, "20260522")
        # basis byte-identity guard: 리팩터링 후 상표 basis 문자열이 기존 출력과 동일해야 함
        self.assertEqual(basis, "정보검색 ApplicationStatus='등록'")

    def test_derive_pending_70(self):
        state, _, mark, reg_no, _ = accounting.derive_legal_state_c_mode(self._info("info_pending70.xml")["info"])
        self.assertEqual(state, "심사중")  # ApplicationStatus=출원 → 심사중(대기)
        self.assertEqual(mark, "NOL")
        self.assertEqual(reg_no, "")

    def test_derive_rejected(self):
        state, _, _, _, _ = accounting.derive_legal_state_c_mode(self._info("info_reject.xml")["info"])
        self.assertEqual(state, "거절")

    def test_unknown_status_is_review(self):
        state, _, _, _, _ = accounting.derive_legal_state_c_mode({"ApplicationStatus": "이상한값", "Title": "X"})
        self.assertEqual(state, "검토필요")

    def test_registered_without_regno_is_review(self):
        # ApplicationStatus=등록이나 등록번호/등록일 누락 → 일관성 위반 → 검토필요
        state, basis, _, _, _ = accounting.derive_legal_state_c_mode({"ApplicationStatus": "등록", "Title": "X"})
        self.assertEqual(state, "검토필요")
        self.assertIn("등록번호", basis)

    def test_full_classify_chain_registered(self):
        # 정보검색 등록 → 등록/상표권
        info = self._info("info_reg.xml")["info"]
        state, _, _, _, _ = accounting.derive_legal_state_c_mode(info)
        self.assertEqual(accounting.classify("40", state), ("등록", "상표권", "상표"))

    def test_generic_parse_multi_record_is_none(self):
        xml = ("<response><header><resultCode>00</resultCode></header><body><items>"
               "<X><A>1</A></X><X><A>2</A></X></items></body></response>")
        p = accounting.parse_info(xml, ".//X")
        self.assertIsNone(p["info"])
        self.assertEqual(p["item_count"], 2)

    def test_generic_parse_zero_record_is_none(self):
        xml = ("<response><header><resultCode>00</resultCode></header><body><items>"
               "</items></body></response>")
        p = accounting.parse_info(xml, ".//X")
        self.assertIsNone(p["info"])
        self.assertEqual(p["item_count"], 0)

    def test_generic_derive_patent_fields(self):
        info = {"RegistrationStatus": "거절", "InventionName": "X", "RegistrationNumber": "", "RegistrationDate": ""}
        a = config.SEARCH_ADAPTERS["특허"]
        state, _, title, _, _ = accounting.derive_legal_state(info, a["fields"], a["status_map"], a["reg_requires"])
        self.assertEqual(state, "거절")
        self.assertEqual(title, "X")


class TestClassifyCFromXml(unittest.TestCase):
    def _row(self, xml, adapter_key, right_code, cost=1000):
        appno = "40-2024-0133564" if adapter_key == "상표" else "10-2020-0012345"
        a = config.SEARCH_ADAPTERS[adapter_key]
        return accounting.classify_c_from_xml(appno, xml, a, right_code, cost, "2026-06-30T00:00:00+09:00")

    def test_trademark_registered_full_row(self):
        xml = (FIX / "info_reg.xml").read_text(encoding="utf-8")
        row = self._row(xml, "상표", "40")
        self.assertEqual(row["asset_status"], "등록")
        self.assertEqual(row["account"], "상표권")
        self.assertEqual(row["recognition_date"], "20260522")
        self.assertEqual(row["kipris_status"], "등록")

    def test_fatal_rc_degrades_to_review(self):
        xml = "<response><header><resultCode>31</resultCode></header><body><items></items></body></response>"
        row = self._row(xml, "특허", "10")
        self.assertEqual(row["asset_status"], "검토필요")
        self.assertIn("인증오류", row["basis"])

    def test_no_record_is_review(self):
        xml = "<response><header><resultCode>00</resultCode></header><body><items></items></body></response>"
        row = self._row(xml, "특허", "10")
        self.assertEqual(row["asset_status"], "검토필요")

    def test_appno_propagated_to_row(self):
        xml = (FIX / "info_reg.xml").read_text(encoding="utf-8")
        a = config.SEARCH_ADAPTERS["상표"]
        row = accounting.classify_c_from_xml("40-2024-0133564", xml, a, "40", 118000, "2026-06-30T00:00:00+09:00")
        self.assertEqual(row["application_number"], "40-2024-0133564")


class TestReviewCsv(unittest.TestCase):
    def test_review_csv_headers_and_values(self):
        row = accounting.build_row(
            appno="40-2024-0133564", right_code="40", cost_raw=118000, legal_state="등록",
            basis="정보검색 ApplicationStatus='등록'", right_label="상표", bucket="등록",
            account="상표권", reg_no="4025487260000", mark_name="NOL", recognition_date="20260522",
            source_mode="C", kipris_status="등록")
        path = Path(tempfile.mkdtemp()) / "review.csv"
        accounting.write_review_csv([row], path)
        text = path.read_text(encoding="utf-8-sig")
        lines = text.strip().splitlines()
        self.assertIn("출원번호", lines[0])
        self.assertIn("KIPRIS상태(원본)", lines[0])
        self.assertIn("취득원가(부가세제외)", lines[0])
        self.assertIn("40-2024-0133564", lines[1])
        self.assertIn("상표권", lines[1])
        self.assertIn("118000", lines[1])
        # 전체 행/디테일은 LEDGER에, 검수 CSV는 핵심 열만(10열)
        self.assertEqual(len(lines[0].split(",")), len(accounting.REVIEW_COLUMNS))


if __name__ == "__main__":
    unittest.main()
