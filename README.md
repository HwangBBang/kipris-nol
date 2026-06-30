# kipris-nol

*IP 출원 데이터(KIPRIS Plus)를 회계 분류로 변환하는 배치 도구.*

> 상태: proof-of-concept · Python 3.14 · 데이터원: KIPRIS Plus Open API · 외부 의존성 0

---

## 1. 개요

`kipris-nol`은 특허청 KIPRIS Plus Open API로 조회한 **법적상태(legal state)**를 입력받아, 각 출원을 **회계 자산/비용으로 분류**하는 도구다. 행정처분 현황을 단순 조회·덤프하던 초기 POC에서 **회계 분류 도구**로 전환되었다.

핵심은 다음 매핑이다.

- **등록** → 무형자산(상표권) *(v1은 상표권만; 특허권은 향후 항목)*
- **탈락(거절·각하·무효·취하·포기·소멸)** → 비용(지급수수료)
- **대기(심사중·공고·불복중·이의신청중)** → 건설중인자산(무형)

출원번호 배열과 취득원가(`cost`)를 입력하면, 출원별로 자산상태·회계계정·취득원가를 산출한 **자산대장(ledger)**과 실무자 **검수용 CSV(review)**를 출력한다.

설계 원칙은 **오분류 0**이다. 확신이 없는 건은 임의 분류하지 않고 `검토필요` 또는 `unsupported`로 격리한다.

자산화 인식 시점은 **설정등록 완료(등록)**이며, 인식일(`recognition_date`)은 등록 확정 시에만 기록된다.

---

## 2. 회계 분류 규칙

분류 진입점은 `accounting.classify(right_code, legal_state) -> (bucket, account, right_label)`이다. 규칙 테이블은 `config.py`(`RIGHT_CODE_INFO`, `BUCKET_RULES`, `APPLICATION_STATUS_MAP`)에 있고, 알고리즘은 `accounting.py`에 있다.

### 2.1 법적상태 → 자산상태 → 회계계정

| 표준 법적상태 | 자산상태(버킷) | 회계계정 |
|---|---|---|
| 등록 | 등록 | 상표권 *(권리구분별 `asset_account`)* |
| 거절 / 각하 / 무효 / 취하 / 포기 / 소멸 | 탈락 | 지급수수료 |
| 심사중 / 공고 / 불복중 / 이의신청중 | 대기 | 건설중인자산(무형) |
| *위 표에 없는 상태* | 검토필요 | *(빈칸)* |
| *지원하지 않는 권리구분* | unsupported | *(빈칸)* |

> 등록 버킷의 회계계정은 `BUCKET_RULES`의 placeholder `'자산'`을 `RIGHT_CODE_INFO[right_code]['asset_account']`로 치환한 값이다. 현재 40/70 모두 **상표권**.

### 2.2 지원 권리구분 (출원번호 앞 2자리)

| 코드 | 권리구분(label) | 자산계정(asset_account) |
|---|---|---|
| 40 | 상표 | 상표권 |
| 70 | 상표 | 상표권 |

70(지정상품추가등록출원, 상표 패밀리)은 정보검색에서 상표로 확인되어 **지원 대상**이다. 그 외 prefix(예: 특허 10 / 실용신안 20)는 현재 매핑이 없어 **unsupported**이며 API 호출도 하지 않는다. (특허권/`10`·`20`은 코드 주석에만 존재하는 향후 항목이다.)

### 2.3 분류 알고리즘

1. `info = RIGHT_CODE_INFO.get(right_code)` — 없으면 `("unsupported", "", "")`.
2. `rule = BUCKET_RULES.get(legal_state)` — 없으면 `("검토필요", "", label)`.
3. 매핑되면 `(bucket, account)` 반환. `account == '자산'`이면 `info['asset_account']`로 치환.

### 2.4 법적상태 도출 모드 (`--source`)

- **C-모드(기본)** — `trademarkInfoSearchService`의 `ApplicationStatus`를 `APPLICATION_STATUS_MAP`으로 표준 법적상태에 매핑. 정보검색 확정 상태 기반.
- **B-모드(보수)** — `RelatedDocsonfileTMService` 행정처리 이력에서 마일스톤을 추론. 취하/포기만 안전하게 확정하고, **등록은 절대 확정하지 않으며**(→ 검토필요) 거절류·불복 불명도 검토필요로 둔다.

`APPLICATION_STATUS_MAP` (C-모드): 등록→등록, 출원→심사중, 공고→공고, 거절→거절, 각하→각하, 무효→무효, 취하→취하, 포기→포기, 소멸→소멸. **맵에 없는 값은 강제로 검토필요.**

---

## 3. 요구사항 / 설치

- Python 3.14 *(버전 핀 파일 없음; `from __future__ import annotations` 사용으로 실제로는 3.10+ 수준에서 동작)*
- **외부 의존성 0** — 표준 라이브러리만 사용 (`urllib`로 HTTP 호출, `xml.etree.ElementTree`로 XML 파싱, `json`/`csv`/`argparse`/`datetime`). `pyproject.toml`·`requirements.txt`·서드파티 패키지 없음.

```sh
git clone <repo>
# 가상환경 불필요 — 표준 라이브러리만 사용
```

---

## 4. .env 설정 (KIPRIS accessKey)

API 인증키는 **오직 `.env`**에서만 읽는다(`config.load_access_key()`). CLI 플래그로 키를 전달하는 경로는 없다. 기본 경로는 `<repo-root>/.env`.

```sh
cp .env.example .env   # KIPRIS Plus accessKey 입력
```

허용 형식 (둘 중 하나):

```
"AccessKey":"<발급받은_키>"      # JSON 조각 형식
AccessKey=<발급받은_키>          # bare dotenv 형식
```

`.env.example` 내용은 placeholder `"AccessKey":"YOUR_KIPRIS_PLUS_ACCESS_KEY_HERE"`이며, 이 placeholder 값은 **거부**된다.

> **보안:** accessKey는 절대 커밋·로그·출력하지 않는다. URL에 들어갈 때는 인코딩되며, 모든 에러/로그 출력에서 `core._scrub()`이 원문 및 URL-인코딩 형태를 `<KEY>`로 치환한다. `.env`(및 `.env.*`)는 `.gitignore` 대상이다 — **실키가 든 `.env`는 절대 커밋하지 말 것.**

---

## 5. 사용법

```sh
python -m kipris_nol
```

기본 동작: `testSet.json`을 읽어 C-모드 회계 분류를 수행하고, `out/`에 CSV+JSON 자산대장과 검수용 CSV를 기록한다.

### CLI 플래그 (총 7개)

| 플래그 | 타입 / choices | 기본값 | 설명 |
|---|---|---|---|
| `--mode` | `accounting` \| `dump` | `accounting` | accounting=등록/대기/탈락 분류(기본), dump=행정처리 이력 원시 덤프 |
| `--input` | Path | `<repo-root>/testSet.json` | 입력 JSON 경로 |
| `--out-dir` | Path | `<repo-root>/out` | 출력 디렉터리(자동 생성) |
| `--format` | `both` \| `csv` \| `json` | `both` | both=CSV+JSON 모두 |
| `--limit` | int | `None` | 앞 N건만 조회(스모크 테스트용). 음수 → ValueError, 0 → 0건 |
| `--delay` | float | `0.4` | 호출 간 지연(초), `time.sleep` |
| `--source` | `c` \| `b` | `c` | c=정보검색 확정상태(기본), b=행정처리 이력 추론(보수). accounting 모드 전용 |

```sh
# 정보검색 확정 상태 기반 분류 (기본)
python -m kipris_nol --input testSet.json --out-dir out --format both --source c

# 보수적 이력 추론 모드, 앞 3건만
python -m kipris_nol --source b --limit 3

# 행정처리 이력 원시 덤프
python -m kipris_nol --mode dump
```

### 종료 코드

- `0` — 성공
- `2` — 인증 실패(`FatalAuthError`: resultCode 30 키 미등록 / 31 사용기한 만료). dump 모드와 B-모드에서 30/31을 만나면 전건 중단되어 이 코드로 종료한다. **C-모드(기본)는 정보검색 30/31을 전건 중단 대신 해당 건만 `검토필요`로 강등**한다(설계 F3).
- `1` — 설정 오류(`FileNotFoundError` / `ValueError`)

---

## 6. 입력 형식 (testSet.json)

최상위는 **JSON 배열**이며, 각 항목은 다음 필드를 갖는다.

| 필드 | 필수 | 설명 |
|---|---|---|
| `applicationNumber` | 필수 (camelCase) | 출원번호. 하이픈 허용(`70-2024-0001232`); 분류·호출 시 하이픈 제거. 앞 2자리가 권리구분 코드 |
| `cost` | 선택 | 취득원가(부가세 불포함). accounting 모드에서만 사용; dump 모드는 무시 |

```json
[
  { "applicationNumber": "70-2024-0001232", "cost": 180000 },
  { "applicationNumber": "40-2025-0233236", "cost": 210000 }
]
```

- `applicationNumber` 누락 → `ValueError("entry {i} missing 'applicationNumber'")`
- 최상위가 배열이 아님 → `ValueError('testSet must be a JSON array of {applicationNumber, cost}')`
- **cost 검증**(`parse_cost`): 누락·boolean·비수치·0·음수는 모두 `None` 처리 → 해당 행은 `검토필요`로 강제되며 합계에 0으로 묻히지 않는다.

저장소 루트에 25건(40- 23건, 70- 2건)짜리 샘플 `testSet.json`이 포함되어 있다.

---

## 7. 출력

CSV는 Excel 호환을 위해 `utf-8-sig`(BOM), JSON은 `ensure_ascii=False, indent=2`로 기록된다. 파일명은 타임스탬프(`%Y%m%d-%H%M%S`)가 붙는다.

### accounting 모드 (기본)

- `ledger-{stamp}.json` — `--format` json/both
- `ledger-{stamp}.csv` — `--format` csv/both
- `review-{stamp}.csv` — **CSV가 생성될 때만**(csv/both). json 단독에서는 생성되지 않음

### dump 모드

- `result-{stamp}.json` / `result-{stamp}.csv`

### 7.1 자산대장 컬럼 (`LEDGER_FIELDS`, 16개, 순서대로)

`application_number`, `right_code`, `right_label`, `kipris_status`, `registration_number`, `mark_name`, `recognition_date`, `acquisition_cost`, `asset_status`, `account`, `legal_state`, `basis`, `source_mode`, `queried_at`, `result_code`, `result_msg`

> CSV에는 `raw_items`가 제외된다(`extrasaction='ignore'`; B-모드 행에 한해 JSON 감사용으로만 포함될 수 있음).

| 컬럼 | 의미 |
|---|---|
| `application_number` | 출원번호(입력) |
| `right_code` | 권리구분 코드(40/70/…) |
| `right_label` | 권리구분(상표/특허) |
| `kipris_status` | **KIPRIS 원본 상태값(정보검색 ApplicationStatus) — 검증용 원천.** C-모드에서만 채워짐 |
| `registration_number` | 등록번호 |
| `mark_name` | 상표명/발명명칭 (B-모드 미확보) |
| `recognition_date` | 자산화 인식일(=등록일; 등록 확정 시에만) |
| `acquisition_cost` | 취득원가(=cost, 부가세 불포함; cost 무효 시 빈 문자열) |
| `asset_status` | 자산상태(버킷): 등록 / 대기 / 탈락 / 검토필요 / unsupported |
| `account` | 회계계정: 상표권 / 건설중인자산(무형) / 지급수수료 / `''` |
| `legal_state` | 표준 법적상태 |
| `basis` | 판정 근거(감사 추적) |
| `source_mode` | B(이력추론) / C(정보검색+교차검증) |
| `queried_at`, `result_code`, `result_msg` | 조회 시각·API 결과코드·메시지 |

### 7.2 검수용 CSV 컬럼 (`REVIEW_COLUMNS`, 10개, 순서대로)

| 컬럼(헤더) | 원본 필드 |
|---|---|
| 출원번호 | `application_number` |
| 상표명 | `mark_name` |
| 권리구분 | `right_label` |
| KIPRIS상태(원본) | `kipris_status` |
| 자산상태 | `asset_status` |
| 회계계정 | `account` |
| 취득원가(부가세제외) | `acquisition_cost` *(정수 문자열로 표기, 예 180000.0 → `180000`)* |
| 등록번호 | `registration_number` |
| 자산화인식일 | `recognition_date` |
| 판정근거 | `basis` |

### 7.3 콘솔 요약

처리 후 버킷별 건수·취득원가 합계를 `[등록, 대기, 탈락, 검토필요, unsupported]` 순으로, 자산화 합계/비용 합계와 함께 출력한다. 합계에는 **유효 cost만** 합산된다(자산화 합계=등록 버킷, 비용 합계=탈락 버킷).

---

## 8. 분류 결과 해석

### `검토필요` (자동 분류 보류)

확신이 없어 자동 분류를 보류한 상태(회계계정 빈칸). 단일 지점이 아니라 분류 경로 전반의 여러 보수 게이트에서 발생하며, 대표적으로 다음과 같다.

**공통**
1. `classify()` — `legal_state`가 `BUCKET_RULES`에 없음
2. `build_row()` — cost 무효(누락·boolean·비수치·0·음수). bucket/account를 검토필요로 덮고 basis에 `cost 무효(...) → 검토필요` 추가

**오케스트레이션(`run_accounting`)**
3. 동일 `applicationNumber`가 입력에 중복 → 임의 합산/덮어쓰기 금지 위해 검토필요
4. 건별 조회 중 예외 발생(네트워크/타임아웃/파싱 등) → 검토필요(키를 스크럽한 사유를 basis에 기록)

**C-모드(`_classify_c`)**
5. 정보검색 resultCode 30/31(인증오류) → 전건 중단이 아니라 **해당 건만** 검토필요로 강등
6. 정보검색 결과 없음(`TradeMarkInfo` 없음) → 검토필요
7. `ApplicationStatus`가 `APPLICATION_STATUS_MAP`에 없음(미수록)
8. `ApplicationStatus=등록`이지만 등록번호/등록일 누락(일관성 위반)

**B-모드(`_classify_b` / `derive_legal_state_b_mode`)**
9. 조회 결과 empty/error → 검토필요
10. 등록 신호(등록번호 또는 등록 관련 문서) 감지 → B-모드는 등록을 확정할 수 없음
11. 거절/무효 신호 감지(불복 여부 불명) → 검토필요
12. 행정처리 이력 없음 → 검토필요

### `unsupported` (지원 범위 밖)

권리구분 코드가 `RIGHT_CODE_INFO`에 없을 때(40/70 외) 발생한다. accounting 모드에서는 `run_accounting`이 `RIGHT_CODE_INFO` 미등록을 감지해 곧바로 `UNSUPPORTED_BUCKET`으로 격리하며(`accounting.classify()`도 동일하게 unsupported 반환), dump 모드에서는 `core.classify()`가 endpoint 미등록 → status `unsupported`로 표시한다. `right_label`은 빈 문자열이고, 어느 경우든 **API 호출을 하지 않는다.**

두 값 모두 실무자가 검수용 CSV에서 직접 확인·후속 처리하도록 격리된다.

---

## 9. 한계 / 범위

**v1 = 국내 상표 분류만.**

- **지원 권리구분:** 40 / 70(상표). 특허(10) / 실용신안(20)은 미지원(`unsupported`) — 코드 주석상의 향후 항목.
- **상각 제외:** 5년 정액상각은 v1 범위 밖. recognition_date까지만 기록하고 상각 계산은 하지 않는다.
- **국외 미지원:** 마드리드/WIPO 국제출원 판정은 v1 범위 밖.
- **분류만:** 법적상태→자산상태→회계계정 매핑 및 자산대장/검수 CSV 산출까지. 전표 생성·전기는 범위 밖.
- KIPRIS Plus 무료 티어 내에서 동작.

---

## 10. 테스트

표준 라이브러리 `unittest`만 사용한다(pytest·서드파티 없음).

```sh
python -m unittest discover -s tests
```

- 테스트 파일 4개, 총 64개 테스트 메서드
  - `test_accounting.py` (28) — parse_cost, B/C 모드 도출, classify, build_row, summarize, load_entries, review CSV
  - `test_core.py` (25) — classify 라우팅, load_input, parse/extract/summarize, decide_status, URL 빌드·키 안전성
  - `test_cli.py` (6) — 배치 회복력(행별 격리, 키 스크럽, --limit, param-error abort, FatalAuthError)
  - `test_config.py` (5) — load_access_key(JSON / bare 형식, placeholder 거부)
- `tests/fixtures/` — XML 픽스처 6개(행정처리 이력 3 + 정보검색 3)
