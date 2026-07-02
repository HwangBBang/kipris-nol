import unittest
from pathlib import Path
from kipris_nol import viewmodel


def _row(**kw):
    base = {"application_number": "", "mark_name": "", "right_label": "",
            "asset_status": "", "account": "", "acquisition_cost": None, "basis": ""}
    base.update(kw)
    return base


class TestViewModel(unittest.TestCase):
    def test_default_output_dir_under_documents(self):
        d = viewmodel.default_output_dir()
        self.assertEqual(d.name, "KIPRIS-NOL")
        self.assertEqual(d.parent.name, "Documents")

    def test_result_rows_projection_and_cost_format(self):
        rows = [_row(application_number="40-1", mark_name="NOL", asset_status="등록",
                     account="상표권", acquisition_cost=210000.0, basis="정보검색 '등록'")]
        out = viewmodel.result_rows(rows)
        self.assertEqual(out[0][0], "40-1")
        self.assertEqual(out[0][1], "NOL")
        self.assertEqual(out[0][2], "등록")
        self.assertEqual(out[0][4], "210,000")

    def test_result_rows_falls_back_to_right_label(self):
        out = viewmodel.result_rows([_row(mark_name="", right_label="특허")])
        self.assertEqual(out[0][1], "특허")

    def test_summary_banner_has_asset_and_expense(self):
        rows = [_row(asset_status="등록", acquisition_cost=100.0, account="상표권"),
                _row(asset_status="탈락", acquisition_cost=50.0, account="지급수수료")]
        s = viewmodel.summary_banner(rows)
        self.assertIn("등록 1건", s)
        self.assertIn("자산화 100", s)
        self.assertIn("비용 50", s)

    def test_verify_message_ok(self):
        self.assertIn("확인되었습니다", viewmodel.verify_message("ok"))

    def test_verify_message_auth30_tells_admin(self):
        msg = viewmodel.verify_message("auth_30")
        self.assertIn("오류 30", msg)
        self.assertIn("관리자에게 문의", msg)

    def test_verify_message_auth31_tells_admin_renewal(self):
        msg = viewmodel.verify_message("auth_31")
        self.assertIn("오류 31", msg)
        self.assertIn("갱신", msg)

    def test_verify_message_network_and_unknown_fallback(self):
        self.assertIn("인터넷", viewmodel.verify_message("network"))
        self.assertEqual(viewmodel.verify_message("???"), viewmodel.verify_message("network"))

    def test_verify_message_ok_no_patent_warns_but_saved(self):  # cx-review 결정 2
        msg = viewmodel.verify_message("ok_no_patent")
        self.assertIn("특허", msg)
        self.assertIn("저장", msg)

    def test_verify_save_ok_set(self):  # cx-review 결정 4: 경고 후 저장 허용 결과 집합
        self.assertEqual(viewmodel.VERIFY_SAVE_OK, {"ok", "ok_no_patent", "unverified"})


if __name__ == "__main__":
    unittest.main()
