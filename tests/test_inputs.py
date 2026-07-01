import unittest
from kipris_nol import inputs


class TestParsePasted(unittest.TestCase):
    def test_tab_two_columns(self):
        e = inputs.parse_pasted("40-2025-0233236\t210000\n70-2024-0001232\t46000")
        self.assertEqual(e, [
            {"application_number": "40-2025-0233236", "cost": "210000"},
            {"application_number": "70-2024-0001232", "cost": "46000"},
        ])

    def test_thousands_separator_and_won(self):
        e = inputs.parse_pasted("40-2025-0233236\t₩210,000")
        self.assertEqual(e[0]["cost"], "210000")

    def test_comma_fallback(self):
        e = inputs.parse_pasted("40-2025-0233236,210000")
        self.assertEqual(e[0], {"application_number": "40-2025-0233236", "cost": "210000"})

    def test_comma_paste_thousands_recovery(self):  # MUST-1: "210,000" 이 210 으로 깨지면 안 됨
        e = inputs.parse_pasted("40-2025-0233236,210,000")
        self.assertEqual(e[0], {"application_number": "40-2025-0233236", "cost": "210000"})

    def test_header_and_blank_lines_skipped(self):
        e = inputs.parse_pasted("출원번호\t취득원가\n\n40-2025-0233236\t1000\n")
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0]["application_number"], "40-2025-0233236")

    def test_appno_only_no_cost(self):
        e = inputs.parse_pasted("40-2025-0233236")
        self.assertEqual(e[0], {"application_number": "40-2025-0233236", "cost": None})


class TestParseCsv(unittest.TestCase):
    def test_utf8_sig_with_header(self):
        data = "﻿출원번호,취득원가\n40-2025-0233236,210000\n".encode("utf-8-sig")
        e = inputs.parse_csv(data)
        self.assertEqual(e, [{"application_number": "40-2025-0233236", "cost": "210000"}])

    def test_cp949_fallback(self):
        data = "출원번호,취득원가\n40-2025-0233236,1000\n".encode("cp949")
        e = inputs.parse_csv(data)
        self.assertEqual(e[0]["application_number"], "40-2025-0233236")

    def test_quoted_thousands_separator(self):
        data = '40-2025-0233236,"210,000"\n'.encode("utf-8")
        e = inputs.parse_csv(data)
        self.assertEqual(e[0]["cost"], "210000")

    def test_reordered_header(self):  # MUST-1: 헤더명 감지로 열 재정렬 방어
        data = "취득원가,출원번호\n210000,40-2025-0233236\n".encode("utf-8")
        e = inputs.parse_csv(data)
        self.assertEqual(e, [{"application_number": "40-2025-0233236", "cost": "210000"}])

    def test_extra_column_by_header(self):  # MUST-1: 추가열이 있어도 헤더명으로 cost 열 특정
        data = "출원번호,상표명,취득원가\n40-2025-0233236,NOL,210000\n".encode("utf-8")
        e = inputs.parse_csv(data)
        self.assertEqual(e[0], {"application_number": "40-2025-0233236", "cost": "210000"})


if __name__ == "__main__":
    unittest.main()
