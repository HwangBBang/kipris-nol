"""설정: accessKey 로딩 + endpoint registry (POC 경량).

- accessKey 는 .env 에서만 로드하며 로그/출력에 절대 노출하지 않는다.
- endpoint registry: 출원번호 권리구분(앞 2자리) → KIPRIS Plus 서비스 매핑.
  2026-06-22 라이브 확정: 40-(상표)·70- 모두 RelatedDocsonfileTMService 로 정상 조회됨.
"""
from __future__ import annotations

import re
from pathlib import Path

# 리포 루트(.env 위치) = 이 파일의 부모의 부모
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = REPO_ROOT / ".env"

# .env 형식: '"AccessKey":"<값>"' (JSON 조각). 표준 dotenv(AccessKey=...) 도 허용.
_KEY_RE_JSON = re.compile(r'"?AccessKey"?\s*[:=]\s*"([^"]+)"')
_KEY_RE_BARE = re.compile(r"AccessKey\s*=\s*(\S+)")
_PLACEHOLDER = "YOUR_KIPRIS_PLUS_ACCESS_KEY_HERE"


def load_access_key(env_path: Path | str | None = None) -> str:
    """`.env` 에서 accessKey 를 읽어 반환. 값/플레이스홀더 누락 시 명확히 실패."""
    path = Path(env_path) if env_path else DEFAULT_ENV_PATH
    if not path.exists():
        raise FileNotFoundError(
            f".env not found at {path} — copy .env.example and set your AccessKey"
        )
    text = path.read_text(encoding="utf-8")
    m = _KEY_RE_JSON.search(text) or _KEY_RE_BARE.search(text)
    if not m:
        raise ValueError(
            '.env present but AccessKey not found (expected \'"AccessKey":"..."\')'
        )
    key = m.group(1).strip()
    if not key or key == _PLACEHOLDER:
        raise ValueError("AccessKey is empty or still the placeholder; set a real key in .env")
    return key


# --- endpoint registry: 권리구분 코드 → 서비스 스펙 ---
# 상표 행정처리 이력: 출원번호 → 문서 이벤트 타임라인(단계/처리상태/문서명).
TRADEMARK_HISTORY = {
    "service_id": "RelatedDocsonfileTMService",
    "operation": "relatedDocsonfileInfo",
    "param": "applicationNumber",
}

# 라이브 확정(2026-06-22): 40-/70- 모두 상표 이력 서비스로 조회됨. 라벨은 실제 prefix 유지.
ENDPOINT_REGISTRY: dict[str, dict] = {
    "40": TRADEMARK_HISTORY,  # 상표
    "70": TRADEMARK_HISTORY,  # 확정표엔 없으나 상표 엔드포인트로 정상 조회(라벨 "70" 유지)
}

BASE_URL = "http://plus.kipris.or.kr/openapi/rest"
TIMEOUT_SEC = 20
RETRY = 1  # 일시 실패 시 단일 재시도
INTER_CALL_DELAY_SEC = 0.4  # 호출 간 예의상 지연

# 인증/권한 오류 → 전건 중단(fail-fast). 단일 키·단일 서비스라 전건 동일 실패.
FATAL_RESULT_CODES = {"30", "31"}
# 파라미터 오류(10)가 동일 endpoint 에서 이 횟수만큼 연속되면 버그 신호로 보고 중단(spec §6).
PARAM_ERROR_ABORT_THRESHOLD = 3
# "20"(결과없음)은 에러가 아님. 이 서비스는 보통 빈 resultCode + 0건으로 결과없음을 표현.
NON_ERROR_RESULT_CODES = {"", "00", "20"}
