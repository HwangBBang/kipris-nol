"""핵심 함수 단위 테스트 — 실응답 픽스처(40-/70-/empty) 기반."""
import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kipris_nol import config, core  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


class TestClassify(unittest.TestCase):
    def test_trademark_40(self):
        code, svc = core.classify("40-2025-0233236")
        self.assertEqual(code, "40")
        self.assertEqual(svc["service_id"], "RelatedDocsonfileTMService")

    def test_70_routes_to_trademark_keeps_label(self):
        code, svc = core.classify("70-2024-0001232")
        self.assertEqual(code, "70")  # 라벨 위조 금지
        self.assertIsNotNone(svc)
        self.assertEqual(svc["service_id"], "RelatedDocsonfileTMService")

    def test_unsupported_right_code(self):
        code, svc = core.classify("10-2025-0000001")  # 특허 — registry 미등록
        self.assertEqual(code, "10")
        self.assertIsNone(svc)

    def test_garbage(self):
        code, svc = core.classify("garbage")
        self.assertEqual(code, "")
        self.assertIsNone(svc)


class TestLoadInput(unittest.TestCase):
    def test_real_testset_has_25(self):
        nums = core.load_input(config.REPO_ROOT / "testSet.json")
        self.assertEqual(len(nums), 25)
        self.assertEqual(sum(n.startswith("70-") for n in nums), 2)
        self.assertEqual(sum(n.startswith("40-") for n in nums), 23)

    def test_ignores_cost(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump([{"applicationNumber": "40-1-2", "cost": 999}], f)
            path = f.name
        self.assertEqual(core.load_input(path), ["40-1-2"])

    def test_non_array_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write('{"applicationNumber": "40-1-2"}')
            path = f.name
        with self.assertRaises(ValueError):
            core.load_input(path)

    def test_missing_field_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump([{"cost": 1}], f)
            path = f.name
        with self.assertRaises(ValueError):
            core.load_input(path)


class TestParse(unittest.TestCase):
    def test_40_multi(self):
        parsed = core.parse((FIX / "sample_40_multi.xml").read_text(encoding="utf-8"))
        self.assertEqual(parsed["result_code"], "")  # 성공 시 빈 코드
        self.assertGreaterEqual(len(parsed["items"]), 5)
        first = parsed["items"][0]
        self.assertEqual(first["documentDate"], "20240719")
        self.assertEqual(first["step"], "출원")
        self.assertIn("상표등록출원서", first["documentTitle"])

    def test_70_multi(self):
        parsed = core.parse((FIX / "sample_70_multi.xml").read_text(encoding="utf-8"))
        self.assertEqual(parsed["result_code"], "")
        self.assertEqual(len(parsed["items"]), 6)

    def test_empty(self):
        parsed = core.parse((FIX / "sample_empty.xml").read_text(encoding="utf-8"))
        self.assertEqual(parsed["result_code"], "")
        self.assertEqual(parsed["items"], [])

    def test_empty_fields_are_stripped(self):
        parsed = core.parse((FIX / "sample_40_multi.xml").read_text(encoding="utf-8"))
        # 빈 값은 응답에서 " "(공백)으로 오므로 strip 후 "" 이어야 함
        self.assertEqual(parsed["items"][0]["registrationNumber"], "")


class TestExtractSummarize(unittest.TestCase):
    def _items(self, name):
        return core.parse((FIX / name).read_text(encoding="utf-8"))["items"]

    def test_extract_count_matches(self):
        parsed = core.parse((FIX / "sample_40_multi.xml").read_text(encoding="utf-8"))
        extracted = core.extract(parsed)
        self.assertEqual(extracted["item_count"], len(extracted["raw_items"]))
        self.assertEqual(extracted["item_count"], len(parsed["items"]))

    def test_summary_is_latest_event(self):
        items = self._items("sample_40_multi.xml")
        expected = max((i.get("documentDate", "") for i in items))
        summary = core.summarize(items)
        self.assertEqual(summary["disposition_date"], expected)
        self.assertTrue(summary["disposition_title"])

    def test_summary_70(self):
        items = self._items("sample_70_multi.xml")
        summary = core.summarize(items)
        self.assertEqual(summary["disposition_date"], "20260303")  # 캡처 시점 최신
        self.assertEqual(summary["disposition_step"], "출원")

    def test_summary_empty_is_none(self):
        self.assertIsNone(core.summarize([]))

    def test_summary_tiebreak_prefers_later(self):
        items = [
            {"documentDate": "20240101", "documentTitle": "A", "status": "", "step": "", "registrationNumber": ""},
            {"documentDate": "20240101", "documentTitle": "B(later)", "status": "", "step": "", "registrationNumber": ""},
        ]
        self.assertEqual(core.summarize(items)["disposition_title"], "B(later)")


class TestDecideStatus(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(core.decide_status("", 3), "ok")
        self.assertEqual(core.decide_status("00", 1), "ok")

    def test_empty(self):
        self.assertEqual(core.decide_status("", 0), "empty")
        self.assertEqual(core.decide_status("20", 0), "empty")

    def test_error_code(self):
        self.assertEqual(core.decide_status("10", 0), "error")

    def test_fatal(self):
        self.assertEqual(core.decide_status("30", 0), "fatal")
        self.assertEqual(core.decide_status("31", 5), "fatal")


class TestUrlBuildingKeySafety(unittest.TestCase):
    def test_hyphen_stripped_and_key_encoded(self):
        url = core.build_url("40-2025-0233236", config.TRADEMARK_HISTORY, "a/b=c+d")
        self.assertIn("applicationNumber=4020250233236", url)  # 하이픈 제거
        self.assertNotIn("a/b=c+d", url)  # 원문 그대로 들어가면 안 됨(인코딩됨)
        self.assertIn("accessKey=a%2Fb%3Dc%2Bd", url)

    def test_scrub_hides_raw_key(self):
        try:
            raise ConnectionError("boom secret-KEY-123 in url")
        except ConnectionError as exc:
            scrubbed = core._scrub(exc, "secret-KEY-123")
        self.assertNotIn("secret-KEY-123", scrubbed)
        self.assertIn("<KEY>", scrubbed)

    def test_scrub_hides_url_encoded_key(self):
        # 핵심 회귀: 예외 메시지에 URL이 실리면 키는 인코딩 형태(%2F/%3D/%2B)로 들어온다.
        raw = "a/b=c+d"
        enc = urllib.parse.quote(raw, safe="")  # a%2Fb%3Dc%2Bd
        msg = f"unknown url type: 'http://h/s?applicationNumber=40&accessKey={enc}'"
        scrubbed = core._scrub(ValueError(msg), raw)
        self.assertNotIn(raw, scrubbed)
        self.assertNotIn(enc, scrubbed)
        self.assertNotIn("a%2Fb", scrubbed)  # 부분 인코딩 잔존도 불가
        self.assertIn("<KEY>", scrubbed)

    def test_scrub_redacts_accesskey_token_even_if_key_unknown(self):
        # accessKey= 토큰은 키 문자열을 몰라도 통째로 가려져야 한다.
        scrubbed = core._scrub("GET /x?accessKey=WHATEVER123&foo=1 failed", "")
        self.assertNotIn("WHATEVER123", scrubbed)
        self.assertIn("accessKey=<KEY>", scrubbed)


if __name__ == "__main__":
    unittest.main()
