"""accessKey 로더 테스트 — 실제 .env 는 읽지 않고 임시 파일로 검증."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kipris_nol import config  # noqa: E402


def _tmp_env(text: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8")
    f.write(text)
    f.close()
    return f.name


class TestLoadAccessKey(unittest.TestCase):
    def test_json_fragment_format_with_special_chars(self):
        # 실제 .env 형식: 값에 '/','=' 포함
        path = _tmp_env('"AccessKey":"x1Ohkuo/xtGlAI=Owa"')
        self.assertEqual(config.load_access_key(path), "x1Ohkuo/xtGlAI=Owa")

    def test_bare_dotenv_format(self):
        path = _tmp_env("AccessKey=abc123")
        self.assertEqual(config.load_access_key(path), "abc123")

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            config.load_access_key("/no/such/.env")

    def test_no_key(self):
        path = _tmp_env("SOMETHING_ELSE=1")
        with self.assertRaises(ValueError):
            config.load_access_key(path)

    def test_placeholder_rejected(self):
        path = _tmp_env('"AccessKey":"YOUR_KIPRIS_PLUS_ACCESS_KEY_HERE"')
        with self.assertRaises(ValueError):
            config.load_access_key(path)


if __name__ == "__main__":
    unittest.main()
