# 작업 인수인계 (2026-10-19 기준)

> **10/19 — 팀원 수집기(iamhiki/narajangter-bid-monitor, TypeScript)와 통합 중.** 브랜치 `claude/collector-integration`
> (main 미병합). 16개 항목 결정과 단계별 진행은 아래 "7. 수집기 통합"과 `CLAUDE.md` "2026-10-19" 절.

다른 Claude 계정(또는 새 세션)에서 이 프로젝트를 이어받을 때 **이 파일을 먼저 읽고**, 그다음 루트 `CLAUDE.md`(자세한 결정·이력 메모)를 읽는다.
팀원 수집기와 비교·통합할 땐 `docs/FEATURE_CATALOG.md`(기능 목록·이식 순서)와 `docs/LOGIC_DETAILS.md`(세부 판정 규칙)를 본다.
새 세션에 이렇게 말하면 된다:

> `docs/HANDOFF.md`와 `CLAUDE.md`를 읽고 이어서 작업해 줘.

---

## 1. 지금 상태 한눈에

- **main = 최신**(마지막 병합 PR #134, 커밋 `525e1b5`). 열린 작업 브랜치·미병합 코드 없음.
- 테스트: `python -m pytest -q` → **400 통과, 1 건너뜀**(10/16 확인). JS: `node tests/js/bidcalc_test.js` → `bidcalc ok`.
- 결과 형식 번호: `webapp/collect.py` `RESULT_FORMAT = 40` (10/19부터 화면은 서버가 알려 주는 값과 비교 — 화면 쪽 숫자 없음)
  (자격 판정·팝업 자료 형식을 바꾸면 **둘 다** 올릴 것 — 테스트가 일치 검사).
- 사용자 PC(서버)에 반영하는 법: `Ctrl+C` → `git pull` → `bash webapp/start.sh` → 브라우저 **F5**
  (판정 로직이 바뀐 경우에만 "나라장터에서 불러오기" 다시).

## 2. 프로젝트가 하는 일

지일(전시·체험관 설계·시공 회사)이 참가할 만한 **나라장터 공고를 수집 → 필터 → 보고**하는 시스템.

1. **수집** — 수의계약을 뺀 경쟁입찰 전부(협상 / 규격가격동시입찰 / 입찰) + 사전규격. `nego/pipeline.py`, `nego/scope.py`
2. **필터** — 관심·제외 키워드, 최소예산 1억(상한 없음), 품명코드, 보유면허 0개면 제외, 지역 미달 제외. `nego/screen.py`, `nego/qualify.py`, `nego/region.py`, `config/*.json`
3. **자격 표시** — 면허제한 API + 첨부(HWP/HWPX/PDF/ZIP) 참가자격 절 파싱, 충족/미달/미확인. "확인 필요" 칩(실적 요건 / 기술인력 요건). `nego/qualification_text.py`, `nego/text_requirements.py`, `webapp/qualview.py`
4. **보고** — 텔레그램 자동 발송(GitHub Actions 예약), 사내 웹앱(팀원 공유), HTML/CSV 리포트.
5. **투찰 계산기**(10/15 신규) — 웹앱 "투찰 계산기" 탭. 21라인 "복수예가산출프로그램"을 본뜸. `nego/bidcalc.py`, `webapp/static/bidcalc*.js`

평가표·배점·적격심사 점수 예측은 **범위 밖**(사용자 결정 — 제안하지 말 것).

## 3. 구성 요소와 위치

| 무엇 | 어디 | 비고 |
|---|---|---|
| 수집·판정 코어 | `nego/` | CLI `python -m nego` (`--fetch-attachment-text`, `--pre-spec`, `--telegram`, `--verify` 등) |
| 설정 | `config/keywords.json`, `codes.json`, `held_qualifications.json`, `code_names.json` | 보유 자격은 최정미 차장님 관리 자료로 갱신 |
| 사내 웹앱 | `webapp/` (`server.py`, `collect.py`, `store.py`, `static/`) | 실행 `bash webapp/start.sh`, 설명 `webapp/README.md` |
| 텔레그램 | `nego/telegram.py`, 문서 `docs/telegram.md` | 담당자 방·개발자 방 2개, 봇 `@jiil_bid_bot` |
| 예약 실행 | `.github/workflows/daily.yml` | 매일 09:00 KST 입찰(어제분), 월 09:10 KST 협상·규격가격·사전규격(지난 7일). Supabase 저장은 꺼둠 |
| 투찰가 분석 수집기 | `nego/bidprice.py`, `.github/workflows/bidprice.yml` | 수동 실행, 과거 개찰결과·예비가격 |
| 담당자 안내 | `docs/user_guide.md`, `docs/user_guide.pptx` | 기능 바뀌면 같이 고칠 것 |
| 필터 판단 사례 | `docs/filter_cases.md` | 새 사례 나오면 한 줄 추가 |
| 과거 실적(싱크로율) | 별도 비공개 레포 `kdh-007/jiil-past-contracts` | 이 레포 옆에 나란히 클론(`JIIL_REPO`로 경로 지정 가능) |

## 4. 새 계정에서 준비할 것

1. **GitHub 접근** — 새 계정이 `kdh-007/Negotiate-Contract-Logic-First-try`와 `kdh-007/jiil-past-contracts`(비공개)에
   접근할 수 있어야 함(저장소 소유자가 협업자로 추가하거나, Claude GitHub 앱을 새 계정에 연결).
   Claude Code 웹이면 세션 만들 때 이 저장소를 고르고, 필요하면 jiil 레포도 추가.
2. **비밀값은 계정이 아니라 저장소·PC에 있음** — 계정을 바꿔도 그대로 동작:
   - GitHub 저장소 Secrets: `NARA_SERVICE_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`(-1004487982383), `TELEGRAM_DEV_CHAT_ID`(-5536307994),
     (선택) `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.
   - 서버 PC: 레포 루트 `.env`(나라장터 인증키, `APP_TOKEN=jiil2026`), `data/webapp.sqlite3`(참가여부·사유·대화 — 이 PC에만 있음).
3. **이 클라우드 환경의 제약** — 나라장터 API·g2b.go.kr·api.telegram.org는 막혀 있음. 실제 호출 확인은
   GitHub Actions 수동 실행(`workflow_dispatch`)이나 사용자 PC 웹앱으로 해야 함.
4. **작업 방식** — 답변은 항상 한국어. 사용자가 명시적으로 원할 때만 PR 생성, 지정된 브랜치에서 작업 후 PR → main 병합(사용자가 병합).
   큰 변경 전후엔 `python -m pytest -q`. 첨부 파싱 규칙을 바꾸면 jiil-past-contracts 과거 문서(약 290건)로 전후 대조하는 게 관례.

## 5. 바로 이어서 할 일 (우선순위 순)

### A. 투찰 계산기 — 사용자 확인 대기 (가장 최근 작업)
- 21라인 결과 캡처를 받아 대조: 버튼별 **칸 나누기는 추정**(범위·개수는 「대한민국 조달제도 기본서」 표 근거).
- 기타설정 추정 3가지: 끝자리 단위를 예정가격·투찰금액 둘 다 적용 / "단가"는 소수점 둘째 자리 / "시설공사·감리및설계"는 총액과 같게.
  "원단위" = 10원 단위로 둠(확인 대기).
- 21라인 "반잔배치" 뜻 — 사용자에게 물어봄(답 대기).
- 면허제한(업종제한) 등록일 하루치 조회(PR #131) 실측 — 서당골천(R26BK01764801) 다시 불러와 경고가 사라졌는지.

### B. 다음 단계 후보 — 추천 사정률
예비가격상세 API로 과거 공고 15개 예비가격 전부·`bssamtBssUpNum`·`drwtNum`·`bidwinrSlctnAplBssCntnts` 수집 → 실제 분포 확인 →
계산기 하단 "과거 공고 검색·추천번호" → 지일 투찰 이력 100건(사용자 엑셀, **공개 저장소에 올리지 말 것**)으로 외주 추천값과 비교.

### C. 자격요건 파싱 개선 — 사례 제보가 오면
공고번호·화면을 먼저 받고 고침. 최근 처리 이력은 `CLAUDE.md` "2026-10-14 오전에 할 것" 3번 항목.

### 하지 말 것 / 꺼내지 말 것 (사용자 결정)
- 예전부터 답을 기다리던 항목(실측 확인 요청, 텔레그램 정시 발송, 제외 공고 첨부 읽기, "품명코드 일치" 칩, 10/16 실사용 결과 듣기 등)은
  **사용자가 먼저 꺼내기 전엔 묻거나 언급하지 않음**.
- 평가표·배점·정량평가 계산 제안 금지. 현장설명회 요건은 파싱 안 함.
- Ruflo(다중 에이전트)는 보류 — 독립 큰 기능 여러 개를 한꺼번에 만들 때만 다시 언급.

## 7. 수집기 통합 (2026-10-19, 브랜치 `claude/collector-integration`)

팀원 수집기와 하나로 합치기로 함(10/16 비교·결정, 10/19 8·10번은 팀원 방식으로 변경). 역할: **수집·판정·첨부·보고·싱크로율 = Python(이 레포),
담당자 화면 = 팀원 화면(ui.html)을 이 레포 웹앱에 옮김(B안 — 서버는 그대로 Python `webapp/server.py`)**. 팀원 레포는 수정하지 않음(사용자 지시).

| 단계 | 내용 | 상태 |
|---|---|---|
| 1 판정 규칙 | 예산(배정예산 그대로·0원=금액 모름), 공사 설계 용역 변형, 가장 이른 마감·마감 지남 제외, 설계공모(낙찰방법) 제외, 지역(API만 제외·시군 단위·첨부는 의심 표시), 해외(팀원 규칙 + 의심 표시), 등록 품목으로 수집·세부품명 구역, '보수공사' 제외 해제 | 완료 |
| 2 첨부 | 과업 문서 순서, 연결 사전규격 첨부, 국방전자조달 첨부, docx·공내역서 xlsx, 첨부 개최지 | 완료 |
| 3 보고 | 텔레그램·HTML 관심/세부품명 구역, [정정]·보낸 공고 기록(bot-state 브랜치), 주간 메일, 자격 충족 표시 수정 | 완료 |
| 4 싱크로율 | 암호화 묶음(corpus/sync_bundle.enc, 아직 안 만듦 — 담당자 PC에서 `python -m nego.corpus seal`) → 메일·HTML 표시 | 완료(묶음 파일 대기) |
| 5 화면 | 팀원 화면(ui.html)을 `webapp/static/index.html`로 옮김. 서버가 팀원 화면 API 모양(`/api/notices`·`/api/excluded`·`/api/participation`·`/api/promote`·`/api/search`·`/api/corpus`)을 내줌(`webapp/mview.py`), 참가여부·올리기는 `data/participation.json`·`data/promoted.json`(`webapp/mstore.py`, 예전 sqlite 기록은 처음 한 번 옮김). 자격·지역·확인 필요·싱크로율 표시는 이 레포 판정 자료로, 투찰 계산기 탭 추가 | 완료(픽스처·브라우저 확인, 실제 수집 실측 대기) |

실측 확인 필요(이 클라우드 환경은 나라장터·텔레그램이 막혀 있음): 연결 사전규격·국방전자조달 첨부 조회, bot-state 브랜치 기록, 메일 발송.

## 6. 최근 작업 이력 요약 (PR 번호는 `kdh-007/Negotiate-Contract-Logic-First-try`)

| 날짜 | 내용 |
|---|---|
| 10/15 | 투찰 계산기 신설·개편(PR #123~#134): 공고번호로 기초금액·하한율·A값 불러오기, 행자부/조달청 난수추출(기본서 15구간 표), 예비가격 소수점 올림 처리, 기타설정 3상자, JIIL 로고 SVG |
| 10/14 | 지역 미달 공고 제외, 관심 키워드 "공간"·"공사 설계 용역"(예산 예외), 허용업종 늘 표시, 면허 조합 표시 개편, 물품 세부품명번호 칸 버그 수정, DRM 첨부 건너뜀, 보유 세부품명 만료 17개 정리, 웹앱 UI 8가지(초록 테마 등), 텔레그램 09:00 |
| 10/13 | 보유면허 0개 공고 제외, 첨부 "또는" 판정 개선, 필터 메뉴화·정렬·구역 이동, 제외→실제 공고 올리기, 참가여부 사유 기록·판단 기록 탭 |
| 10/12 | 텔레그램 자동 발송 켬(PR #88), 실패 시 개발자 방 알림·60분 재시도, 슈퍼그룹 ID 자동 전환, d2b 국방 공고 표시, 투찰가격 분석 수집기 |
| 9/28~10/2 | 수집 범위 확대(수의계약 제외 전부), 사내 웹앱, 사전규격, ZIP 첨부, 참가자격 절 찾기 개선, 지역 판정, 확인 필요 칩 |

세부 결정·근거·실측 기록은 전부 `CLAUDE.md`에 날짜별로 있음 — 판단이 필요할 때 해당 날짜 절을 찾아볼 것.
