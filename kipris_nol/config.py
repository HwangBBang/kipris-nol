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


# --------------------------------------------------------------------------- #
# 회계 분류 설정 (실무자 확정 2026-06-23, [[kipris-accounting-rules]])
# premise 2: "법적상태 → 자산/비용/대기" 매핑은 코드가 아니라 설정으로 분리.
# --------------------------------------------------------------------------- #

# 권리구분(출원번호 앞 2자리) → 라벨 + 자산 계정. v1 = 국내 상표(40)만 지원.
# 70 등 비대상은 미등록 → unsupported(자동 분류 금지).
RIGHT_CODE_INFO: dict[str, dict] = {
    "40": {"label": "상표", "asset_account": "상표권"},
    # 70- = 지정상품추가등록출원(상표 패밀리). 정보검색이 상표로 확인(2026-06-23) → 상표로 포함.
    "70": {"label": "상표", "asset_account": "상표권"},
    # 향후: "10"/"20" 특허·실용 → {"label": "특허", "asset_account": "특허권"}
}

# 상표 정보검색 — 확정 행정상태(법적상태) 제공. 2026-06-23 승인·라이브 확인.
# 성공 시 resultCode 빈 값 + body/items/TradeMarkInfo. 인증오류는 resultCode 30/31.
TRADEMARK_SEARCH = {
    "service_id": "trademarkInfoSearchService",
    "operation": "applicationNumberSearchInfo",
    "param": "applicationNumber",
}

# 정보검색 ApplicationStatus → 표준 법적상태(BUCKET_RULES 키). 미수록 값 → 검토필요(강제).
# 라이브 관측(25건): 등록/출원/공고/거절. 나머지는 회계규칙([[kipris-accounting-rules]]) 기준 선반영.
APPLICATION_STATUS_MAP = {
    "등록": "등록",
    "출원": "심사중",
    "공고": "공고",
    "거절": "거절",
    "각하": "각하",
    "무효": "무효",
    "취하": "취하",
    "포기": "포기",
    "소멸": "소멸",
}

# 표준 법적상태 → (버킷, 회계계정). 자산화 인식 시점 = '등록'(설정등록 완료).
# 계정이 "자산"이면 권리구분에 따라 상표권/특허권으로 치환된다.
BUCKET_RULES: dict[str, tuple[str, str]] = {
    "등록": ("등록", "자산"),
    "거절": ("탈락", "지급수수료"),
    "각하": ("탈락", "지급수수료"),
    "무효": ("탈락", "지급수수료"),
    "취하": ("탈락", "지급수수료"),
    "포기": ("탈락", "지급수수료"),
    "소멸": ("탈락", "지급수수료"),
    "심사중": ("대기", "건설중인자산(무형)"),
    "공고": ("대기", "건설중인자산(무형)"),
    "불복중": ("대기", "건설중인자산(무형)"),
    "이의신청중": ("대기", "건설중인자산(무형)"),
}

# 매핑에 없거나 확신 불가 → 검토필요(계정 미정). 오분류 0 원칙(보수적).
REVIEW_BUCKET = ("검토필요", "")
# 권리구분 범위 밖(예: 70-) → 자동 분류 금지.
UNSUPPORTED_BUCKET = ("unsupported", "")
