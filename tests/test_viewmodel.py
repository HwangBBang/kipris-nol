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


if __name__ == "__main__":
    unittest.main()
