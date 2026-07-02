"""accessKey 로컬 저장. Windows: %APPDATA%/KIPRIS-NOL/config.json. .exe·CI엔 키 없음."""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_DIRNAME = "KIPRIS-NOL"


def config_dir() -> Path:
    base = os.environ.get("APPDATA")  # Windows
    if base:
        return Path(base) / APP_DIRNAME
    return Path.home() / ".config" / "kipris-nol"  # dev/mac 폴백


def _config_file() -> Path:
    return config_dir() / "config.json"


def load_key() -> str | None:
    p = _config_file()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict):  # SHOULD-3: JSON-valid non-dict → 손상 취급(fail-closed)
        return None
    key = (data.get("access_key") or "").strip()
    return key or None


def save_key(key: str) -> None:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = _config_file()
    f.write_text(json.dumps({"access_key": key.strip()}, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
