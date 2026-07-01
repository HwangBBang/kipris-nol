import unittest
from unittest import mock
from pathlib import Path
import tempfile

from kipris_nol import keystore


class TestKeystore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = mock.patch.object(keystore, "config_dir", return_value=self.tmp)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_missing_is_none(self):
        self.assertIsNone(keystore.load_key())

    def test_save_then_load_roundtrip(self):
        keystore.save_key("MYKEY123")
        self.assertEqual(keystore.load_key(), "MYKEY123")

    def test_whitespace_stripped(self):
        keystore.save_key("  spaced  ")
        self.assertEqual(keystore.load_key(), "spaced")

    def test_corrupt_file_is_none(self):
        (self.tmp / "config.json").write_text("not json", encoding="utf-8")
        self.assertIsNone(keystore.load_key())

    def test_empty_key_is_none(self):
        keystore.save_key("   ")
        self.assertIsNone(keystore.load_key())

    def test_json_valid_non_dict_is_none(self):  # SHOULD-3: "[]"/"abc" 는 valid JSON이나 dict 아님
        (self.tmp / "config.json").write_text("[]", encoding="utf-8")
        self.assertIsNone(keystore.load_key())


if __name__ == "__main__":
    unittest.main()
