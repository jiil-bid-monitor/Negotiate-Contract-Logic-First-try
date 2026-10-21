# 나라장터 입찰 공고 수집·판정 시스템 (지일)

나라장터 입찰공고와 사전규격을 매일 모아 **지일이 참가할 만한 공고만 골라**, 참가자격(면허·세부품명·지역)을 판정해
**텔레그램·메일로 보고**하고, **사내 웹앱**에서 팀원이 함께 검토·판단을 기록하는 시스템입니다.

처음엔 「협상에 의한 계약」 공고만 추출하는 도구로 시작했고, 지금은 수의계약을 뺀 경쟁입찰 전부(협상·규격가격동시입찰·입찰)와
사전규격까지 다룹니다. 팀원 수집기(`iamhiki/narajangter-bid-monitor`, TS)의 규칙도 2026-10-19부터 이 레포로 합쳤습니다(그 레포는 수정하지 않음).

> 결정 배경은 날짜별로 [CLAUDE.md](CLAUDE.md)에, 새로 이어받을 때 읽을 요약은 [docs/HANDOFF.md](docs/HANDOFF.md)에 있습니다.

---

## 한눈에

| 구성 | 하는 일 | 위치 |
|---|---|---|
| 수집·판정 엔진 | 나라장터 API 수집 → 범위·키워드·예산·면허·지역 필터 → 첨부 읽기 → 자격 판정 | `nego/` |
| 정기 보고 | 평일 09:00 입찰 / 월 09:10 협상·규격가격·사전규격을 텔레그램(+HTML 첨부)·메일로 | `.github/workflows/daily.yml`, `nego/telegram.py`, `nego/email_html.py` |
| 사내 웹앱 | 회사 PC 한 대에서 띄워 팀원이 사내망으로 접속 — 공고 카드·자격 팝업·싱크로율·참가여부 기록·투찰 계산기 | `webapp/` |
| 피드백 시트 | 보고된 공고(A~R열)와 웹앱 판단(S~V열)을 팀 구글 시트에 | `nego/feedback_sheet.py`, `webapp/sheetsync.py` |
| 투찰 도구 | 투찰 계산기(예비가격 15개 생성), 과거 개찰결과 수집기 | `nego/bidcalc.py`, `nego/bidprice.py` |

기능 하나하나의 규칙·함정·코드 위치는 [docs/FEATURE_CATALOG.md](docs/FEATURE_CATALOG.md), 세부 판정 규칙은 [docs/LOGIC_DETAILS.md](docs/LOGIC_DETAILS.md).

---

## 처리 흐름

```
① 수집        본공고 3종(용역·물품·공사) + 사전규격, 면허제한·참가가능지역·기초금액
                  ↓  담당자 연락처만 제거하고 원문 그대로
② 수집 범위    최신 차수 → 취소공고 제외 → 수의계약·설계공모 제외 → 유형 3개(협상 / 규격가격동시입찰 / 입찰)
                  ↓  본공고로 이미 나간 사전규격은 제외
③ 업역 필터    관심·제외 키워드(띄어쓰기 무시) / 최소예산 1억(예외: 설계 용역) / 세부품명(보유 26개)
                  ↓  해외 개최(한국관·전시회+단체관·국가 약자) 제외, 몽골 공고는 "몽골 공고 유력" 표시
④ 자격 필터    지일 보유 면허가 하나도 없는 공고 제외(공동수급도 불가) / API 지역제한 미달 제외
                  ↓
⑤ 첨부 읽기    HWP·HWPX·PDF·ZIP·DOCX·XLSX → 참가자격 절 찾기 → 코드·이름으로 자격 판정
                  ↓  실적·기술인력 "확인 필요" 칩, 첨부 마감·사업 범위·개최 장소
⑥ 마감 정리    기준 마감 = 자격등록·입찰·의견등록 중 가장 이른 것 → 지난 공고 제외
                  ↓
⑦ 보고·표시    텔레그램(간결 2줄 + HTML 첨부) / 주간 메일 / 웹앱 카드 / 피드백 시트
```

**②의 순서가 중요합니다.** 최신 차수를 먼저 고르고 취소 여부를 봅니다. 반대로 하면 "000 등록 → 001 취소"인 사업에서
000이 살아남아 취소된 사업이 검토 목록에 올라옵니다.

---

## 판정 규칙 요약

### 수집 범위·필터
- **유형**: 낙찰방법 `협상에의한계약…` → 협상, `규격가격동시입찰` → 규격가격, 그 밖 경쟁입찰 → 입찰.
  계약체결방법(`cntrctCnclsMthdNm`)에 "수의"가 있으면 제외. 낙찰·계약방법에 "설계공모"가 있으면 제외.
- **키워드**: `config/keywords.json` — 관심 키워드, 조건부 제외(`excludeKeywords`), 무조건 제외(`hardExcludeKeywords` —
  특별전·순회전·보수공사), 제외 예외("실내건축"은 "건축"으로 안 침). 웹앱에선 관심·제외가 같이 있으면 버리지 않고 **"검토 필요"**.
- **예산**: 배정예산 기준 1억 이상, 상한 없음. 0원·1,000원 미만은 "금액 모름"으로 통과. 공고명에 "설계"+"용역"이면 예산 무관.
- **세부품명**: 물품은 `held_qualifications.json` heldProducts 중 하나만 맞아도 일치(키워드 없이 품목만 맞으면 "세부품명" 구역).

### 자격
- **면허제한 API**: 제한그룹끼리 **"또는"**, 그룹 안 행끼리 **"모두 필요"**, 행의 허용업종은 대신 인정.
  업종은 **코드 비교 + 이름 완전일치**(부분일치 금지 — `실내건축공사업`이 `건축공사업`을 채우던 오류).
- **첨부 참가자격**: "또는/혹은"으로 이어진 코드끼리만 하나 보유로 충족, 나머지는 모두 보유. "어느 하나"·"중 1개 이상"은 그 표시가 든
  하위 항목만. 결격 사유 나열("다음 중 어느 하나에 해당하는 경우 자격이 없습니다")은 OR로 안 봄.
  "보유하지 않은 경우 공동수급" 요건은 미보유가 아니라 **조건부**.
- **자격 미달이어도 후보에서 빼지 않음** — 공동수급으로 수주하는 경우가 있어 담당자가 판단. 단 보유 면허 0개는 제외.
  정보가 없거나 조회 실패면 걸러내지 않고 "자격 미확인"(fail-open). 기타자유업종(9999)은 늘 보유.
- **지역**: 제외는 API 참가가능지역만(시·군 단위, 지일 = 강원 정선군). 첨부 문구로 본 지역제한은 "지역 제한 의심" 표시만.

### 마감
- 기준 마감 = 입찰참가자격등록·입찰·사전규격 의견등록 중 **가장 이른 것**(셋 다 없으면 첨부 공고문 마감).
  공동수급협정 마감은 기준에서 빼고 팝업·툴팁에만. 기준 마감이 지난 공고는 첨부를 읽은 뒤 제외.

---

## 보고

| 언제 (KST) | 내용 | 방식 |
|---|---|---|
| 평일 09:00 | 어제 게시된 **입찰** (월요일은 금~일 3일치) — 처음 보는 공고·[정정]만 | 텔레그램 |
| 매주 월 09:10 | 지난 7일 **협상·규격가격동시입찰 + 사전규격** | 텔레그램 + 메일 |

- 정시 실행은 **cron-job.org가 `workflow_dispatch`를 호출**합니다(GitHub 예약은 1~3시간씩 늦어서). GitHub `schedule`은 12:00 백업이며
  그날 같은 모드가 이미 돌았으면 수집 없이 끝납니다. 설정법은 [docs/telegram.md](docs/telegram.md) 9절.
- 텔레그램은 공고마다 2줄(공고명 링크 / 금액 · D-N · 자격), 마감 임박순, 본공고는 [협상]·[규격가격]·[입찰] 표시, 사전규격은 따로 구역,
  0건이어도 "새 공고 없음". 마감 10일 이내는 🔴. 자세한 판정은 첨부 HTML(관심/세부품명 구역, 자격 팝업)에서.
- 수집이 실패하면 60분 뒤 재시도(최대 5회). 실패·회복 알림은 **개발자 방에만**(`TELEGRAM_DEV_CHAT_ID`).
- 보낸 공고는 `bot-state` 브랜치에 기록되어 웹앱 판단 기록에 '미판단'으로 쌓이고(판단 없이 마감되면 '놓침'), 피드백 구글 시트에도 한 줄씩 쌓입니다.

---

## 사내 웹앱 (`webapp/`)

회사 PC에서 `bash webapp/start.sh`로 켜고 팀원이 `http://<서버 IP>:8765/?t=<토큰>`으로 접속합니다. 자세한 실행법은 [webapp/README.md](webapp/README.md),
담당자용 안내는 [docs/user_guide.md](docs/user_guide.md).

- **나라장터에서 불러오기**: 기간(최근 N일 또는 직접 지정, 최대 92일)을 골라 위 흐름 그대로 수집. 실제 공고(관심·세부품명·사전규격·검토 필요) / 제외된 공고 탭.
- **카드**: 마감(커서 → 전체 일정), 자격 팝업(필수 보유 / N개 중 1개 이상 / 조합 중 하나, ✓·✗·–), 지역·공동수급, 확인 필요 칩, 싱크로율.
- **싱크로율**: 과거 실적(비공개 `jiil-past-contracts`, 암호화 묶음 `python -m nego.corpus`)과 과업 유사도. 지금은 표시만 — 판단 기록이 쌓이면 검증.
- **참가여부·사유 기록**: `data/participation.json`, 판단 기록 탭·CSV, 피드백 시트 S~V열 자동 반영, `BACKUP_DIR`(구글 드라이브) 백업.
- **제외 공고 되살리기**: "실제 공고로 올리기", '세부품명 미달' 공고, 공고문 세부품명으로 찾은 용역 공고.
- **투찰 계산기**: 공고번호로 기초금액·하한율·A값을 불러와 행자부·조달청 방식으로 예비가격 15개 생성, 예정가격·투찰금액 계산.

서버 코드가 바뀌면 `Ctrl+C` → `git pull` → `bash webapp/start.sh` → 브라우저 F5 → (판정이 바뀌었으면) 다시 불러오기.

---

## 설치 · 실행

```bash
pip install -r requirements.txt

# API 키 없이 동작 확인 (픽스처로 전 구간 실행)
python scripts/demo.py

# 테스트
python -m unittest discover -s tests -t .

# 실제 수집 (인증키 필요)
export NARA_SERVICE_KEY="발급받은 인증키(Decoding/평문)"
python -m nego --days 7 --pre-spec --fetch-attachment-text   # 사전규격 포함, 첨부까지 읽기
python -m nego --categories 입찰 --complete-days 1          # 어제 하루치 입찰만 (매일 보고와 같은 범위)
python -m nego --from-store                                 # 저장된 원문으로 재필터링 (API 호출 없음)
python -m nego --verify                                     # 응답 필드명 진단

# 사내 웹앱
bash webapp/start.sh
```

> 인증키는 **"일반 인증키(Decoding / 평문)"** 값을 넣으세요. Encoding 키를 넣으면 URL 인코딩이 중복돼 인증 오류가 납니다.
> `.env`에 인코딩된 값이 있다면 `urllib.parse.unquote`로 풀어 넣어야 합니다(CLI는 `.env`를 읽지 않음).

주요 CLI 옵션: `--telegram`(발송), `--sent-state`(보낸 공고 기록 — 새 공고·정정만), `--report-log daily|weekly`(판단 기록용 목록),
`--email`(주간 메일), `--llm-similarity`(Claude API 유사도, 선택), `--no-supabase`.

### 환경변수 · Secrets

| 이름 | 쓰는 곳 | 설명 |
|---|---|---|
| `NARA_SERVICE_KEY` | 전부 | 공공데이터포털 인증키(필수). 사전규격·낙찰정보 서비스도 같은 키로 활용신청 |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | 보고 | 담당자 방 발송(쉼표로 여러 곳). 없으면 발송만 건너뜀 |
| `TELEGRAM_DEV_CHAT_ID` | 보고 | 개발자 방 — 실패·회복 알림(선택) |
| `SMTP_HOST`·`SMTP_PORT`·`SMTP_USER`·`SMTP_PASS`·`SMTP_FROM`·`SMTP_SECURE` | 주간 메일 | 받는 사람은 `config/recipients.json` |
| `GOOGLE_SERVICE_ACCOUNT_JSON`(또는 `_FILE`), `FEEDBACK_SHEET_ID` | 피드백 시트 | 없으면 건너뜀. 설정법 `docs/server_pc_sheet_setup.md` |
| `CORPUS_PASSPHRASE` | 싱크로율 | 과거 실적 암호화 묶음 풀기 |
| `HOST`·`PORT`·`APP_TOKEN`·`BACKUP_DIR`·`JIIL_REPO` | 웹앱 | 서버 PC 설정(`webapp/start.sh`가 기본값을 채움) |
| `ANTHROPIC_API_KEY` | 선택 | LLM 유사도·AI 판단 |
| `LOOKBACK_DAYS`·`API_NUM_OF_ROWS`·`API_MAX_RETRIES`·`API_TIMEOUT_SEC`·`API_REQUEST_INTERVAL_SEC` | 수집 | 조회 기간·페이지·재시도·대기 |
| `SUPABASE_URL`·`SUPABASE_SERVICE_ROLE_KEY`·`SUPABASE_TABLE` | 저장(꺼둠) | 지금은 자동 실행도 저장하지 않음 |

### 설정 파일 (`config/`)

| 파일 | 내용 |
|---|---|
| `keywords.json` | 관심·제외·무조건 제외 키워드, 제외 예외, 예산 예외 |
| `held_qualifications.json` | 지일 보유 면허·세부품명(등록유효기간 기준), 소재지, 사업자번호 — 등록증이 갱신되면 고칠 것 |
| `code_names.json` | 코드만 적힌 자격을 이름으로 보여주는 사전 |
| `overseas.json` | 해외 개최 판정용 국가 약자·지명 |
| `recipients.json` | 주간 메일 받는 사람 |

---

## 나라장터 API에서 겪은 것 (다시 만들지 마세요)

- **비표준 오류 응답** — `{"nkoneps.com.response.ResponseError": …}` 형태가 있어 응답 트리를 재귀로 뒤져 `resultCode`를 찾습니다.
- **items 형태 5종** — 배열 / 단일 객체 / `{item: [...]}` / `{item: {...}}` / 빈 문자열을 한 모양으로.
- **JSON/XML 양쪽 파싱** — `type=json`을 보내도 인증 오류는 XML로 옵니다.
- **면허제한정보는 기간 일괄 조회** — `bidNtceNo`를 넘겨도 서버가 거르지 않아, 기간 전체를 한 번 받아 공고번호로 묶습니다.
  정정공고 차수마다 같은 행이 다시 오므로 공고별 마지막 차수만 씁니다.
- **물품 세부품명번호 칸** — 본공고는 `dtilPrdctClsfcNo`·`purchsObjPrdctList`, 사전규격은 `prdctDtlList`. `prdctClsfcNo`만 보면 매칭이 한 번도 안 됩니다.
- **오류 문구의 서비스키** — 요청 URL이 예외 메시지에 그대로 들어가므로 로그·알림에서 가립니다(`mask_service_key`).
- **부분 실패 허용** — 업무구분 하나가 실패해도 나머지로 진행하고 경고를 답니다. 전부 실패하면 "0건"이 아니라 오류로 끝냅니다.

---

## 보안

- 담당자 이름·전화·메일은 저장 전에 제거합니다(`fields.PERSONAL_FIELDS`). `data/`·`output/`·`.env`는 `.gitignore`.
- 과거 실적·지일 투찰 이력 같은 회사 자료는 이 레포에 올리지 않습니다(비공개 `jiil-past-contracts`, 암호화 묶음).
- **이 레포는 비공개여야 합니다.**

---

## 문서

| 문서 | 내용 |
|---|---|
| [docs/HANDOFF.md](docs/HANDOFF.md) | 인수인계 — 현재 상태·준비할 것·다음 할 일 |
| [docs/FEATURE_CATALOG.md](docs/FEATURE_CATALOG.md) · [docs/LOGIC_DETAILS.md](docs/LOGIC_DETAILS.md) | 기능 목록 / 세부 판정 규칙 |
| [docs/decision_log.md](docs/decision_log.md) | 결정 기록(보고 기록·백업·시트 등) |
| [docs/telegram.md](docs/telegram.md) | 텔레그램·외부 예약·Secrets 설정 |
| [docs/server_pc_sheet_setup.md](docs/server_pc_sheet_setup.md) · [docs/backup_setup.md](docs/backup_setup.md) | 서버 PC 시트·백업 설정 |
| [docs/filter_cases.md](docs/filter_cases.md) | 필터 판단 사례(키워드 조정 때 참고) |
| [docs/user_guide.md](docs/user_guide.md) | 담당자용 웹앱 사용 안내 |
| [SETUP.md](SETUP.md) | 처음 설정(봇 만들기 등) |

---

## 남은 과제

- 실측 확인: 피드백 시트 실제 쓰기(서버 PC), 외부 예약 정시 호출·12:00 백업 skip, d2b 연계 공고
- 제외된 공고도 첨부 읽기(범위는 그때 결정 — 일정이 빈 제외 공고부터 권장)
- 싱크로율 검증(판단 기록 30건쯤 쌓이면), 추천 사정률(과거 예비가격 분포)
- `docs/user_guide.*`를 새 화면으로 고치기
