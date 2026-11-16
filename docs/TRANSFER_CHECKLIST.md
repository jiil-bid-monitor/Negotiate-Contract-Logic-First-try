# 웹앱 관할 이관 체크리스트 (2026-11-16 작성)

웹앱 관할을 다른 부서로 넘길 때 넘겨야 할 것과 넘긴 뒤 할 일입니다.
**비밀번호·키·접속 토큰 값은 이 문서에 적지 않습니다** — 실제 값은 직접 만나서 전달합니다.

## 1. 계정·권한 (소유자를 바꿔야 하는 것)

| 대상 | 넘길 것 |
|---|---|
| GitHub 조직 `jiil-bid-monitor` | 조직 Owner 권한 (지금 `kdh-007` 한 명뿐) |
| GitHub 저장소 | 공개 `Negotiate-Contract-Logic-First-try`(코드), 비공개 `jiil-quant-docs`(회사 자료 잠금 묶음), `jiil-past-contracts`(싱크로율용 과거 실적) — admin 권한 |
| GitHub Secrets | 나라장터 키, 텔레그램 봇·방 ID 3개, 판단 링크(`TELEGRAM_JUDGE_URL`), 메일(SMTP) 6개, 구글 시트 키·시트 ID, 싱크로율 묶음 비밀번호(`CORPUS_PASSPHRASE`), Anthropic·Supabase 키 |
| 공공데이터포털 | 나라장터 API 인증키를 받은 계정 (입찰공고·사전규격·낙찰정보 활용신청) |
| 텔레그램 | 봇 `@jiil_bid_bot`(BotFather 소유권), 담당자 방·개발자 방 관리자 |
| cron-job.org | 평일 9시 정시 발송 예약 계정 + GitHub 토큰(최대 1년, 만료 전 갱신) |
| 구글 | 피드백 시트, 서비스 계정 키, 판단 기록 백업 드라이브 폴더 |
| 메일 발송 계정 | 주간 보고 메일 SMTP 계정 |
| Supabase | 프로젝트 (지금은 저장을 꺼 둠 — 계속 쓸지 결정) |

## 2. 장비

| 장비 | 넘길 것 |
|---|---|
| 서버 PC | 로그인 계정(`HJE`), 위치·켜는 법, 원격 데스크톱 사용 여부 |
| NAS | 컨테이너 2개(`jiil-webapp`·`jiil-under100m`), 재시작 방법(`control/restart-webapp`), 이미지 재빌드는 NAS 관리자 필요 |
| 사내 담당 | 공유기·NAS 관리자 연락처 |

## 3. 서버 PC·NAS 설정 파일

- **서버 PC `.env`**: 나라장터 키, 접속 토큰(`APP_TOKEN`), 묶음 비밀번호(`QUANT_VAULT_PASSPHRASE`),
  회사 연락처 값(`QUANT_FAX`·`QUANT_TEL`·`QUANT_EMAIL`·`QUANT_HOMEPAGE`·`QUANT_REP_MOBILE`),
  NAS로 결과 보내기(`RESULT_PUSH_URL`), 구글 시트 키, 백업 폴더
- **NAS `.env`**: `WEBAPP_ROLE=viewer`, 같은 접속 토큰, 판단 링크, 투찰 기록 받아 오기(`BIDLOG_PULL_URL`)
- **NAS 1억 미만**: `under100m/run.sh`, `회사프로필.json`(저장소 밖, 공개 금지)

## 4. 데이터 (공개 금지 — 서버 PC·NAS에만 있음)

- 서버 PC `data/`: 판단 기록(`participation.json`·`promoted.json`), 투찰 내역(`bid_log.json`), 밤 작업 기록
- 서버 PC `output/`: 과거 개찰 결과(`bid_results*.jsonl`), 투찰 이력 엑셀·성적표
- 회사 자료: `jiil-quant-docs` 폴더(정량서류·회사자료 원본), 잠금 묶음 `company.enc`
- NAS: 낙찰 후 서류 폴더, 1억 미만 결과 폴더, 회사서류_최신
- 지일 투찰 이력(21라인 엑셀) 원본

## 5. 문서 (저장소 `docs/`)

| 문서 | 내용 |
|---|---|
| `HANDOFF.md` | 지금 상태·다음 할 일 (먼저 읽을 것) |
| `../CLAUDE.md` | 날짜별 결정·규칙 전체 기록 |
| `FEATURE_CATALOG.md`, `LOGIC_DETAILS.md` | 기능 목록·판정 규칙 |
| `user_guide.md` / `.pptx` | 담당자용 사용 안내 |
| `telegram.md` | 발송 일정·봇·외부 예약 설정 |
| `quantdocs.md` | 정량평가서 기능·회사 자료 넣는 법 |
| `../webapp/README.md` | 실행법·접속 토큰 바꾸기 |
| `backup_setup.md`, `server_pc_*.md` | 백업·시트·NAS 설정 |
| `decision_log.md`, `filter_cases.md` | 판단 기록 규칙·필터 사례 |
| `worklog/` | 날짜별 작업 기록 |

## 6. 운영 일정 (자동으로 도는 것)

- 평일 9시: 입찰 보고(텔레그램)
- 월요일 9시 10분: 협상·규격·사전규격 주간 보고(메일)
- 12시: GitHub 예약 실행(정시 발송이 누락됐을 때만 대신 보냄)
- 서버 PC 매일 22시: 개찰 결과 수집·투찰 이력 엑셀 / 월요일: 성적표
- NAS 평일 8~19시 매시: 투찰 기록 받아 오기
- NAS 평일 11~17시: 1억 미만 하루 한 번
- NAS 22시: 낙찰 확인 / 회사 서류 만료 알림

## 7. 일상 운영 방법

- 코드 반영(실행 중인 창은 먼저 `Ctrl+C`, 끝나면 브라우저 F5):
  ```bash
  cd "C:\Users\HJE\나라장터 수집\Negotiate-Contract-Logic-First-try" && git checkout main && git pull && bash webapp/start.sh
  ```
  `requirements.txt`가 바뀌었으면 `git pull` 뒤 `py -m pip install -r requirements.txt`
- 회사 자료 갱신: 폴더에 넣기 → 묶음 다시 잠그기(`py -m quantdocs vault seal --src …` / 비밀번호만 바꿀 땐 `vault rekey`)
- 접속 토큰 바꾸기: `webapp/README.md` '접속 토큰 바꾸기'
- 보유 면허·세부품명 갱신: `config/held_qualifications.json` (최정미 차장님 자료 기준)

## 8. 진행 중·결정 대기

- 부장님께 여쭤볼 것: NAS 외부 접속(QuickConnect·포트포워딩), 공유기 3389 포트, NAS `jiil-webapp` 공유 폴더 쓰기 권한
- 서버 PC: 제천 통합서식 다시 만들어 ':' 위치 확인
- 투찰 추천 규칙: 실전 기록이 쌓인 뒤 고도화(그 전엔 손대지 않기로 결정)

## 9. 넘긴 뒤 꼭 할 것

1. 새 담당자에게 조직 Owner를 넘기고, 넘겨받은 계정에 2단계 인증을 켭니다.
2. 기존 담당자 접근을 정리합니다(GitHub·텔레그램·cron-job.org·구글).
3. **접속 토큰과 묶음 비밀번호를 새로 바꿉니다** — 기존 담당자가 알고 있는 값이라서.
4. 나라장터 키·텔레그램 봇 토큰도 개인 계정 소유라면 새 담당자 계정으로 다시 발급합니다.
