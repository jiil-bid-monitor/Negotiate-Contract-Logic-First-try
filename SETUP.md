# 깃허브에 올려서 실행하기 — 처음부터 끝까지

깃허브를 처음 쓴다는 가정으로 씁니다. **웹 화면만으로 끝나는 방법(A)** 과 **명령어로 하는 방법(B)** 둘 다 적었습니다. 둘 중 하나만 하면 됩니다.

미리 준비할 것 두 가지입니다.

- **나라장터 인증키** — 공공데이터포털(data.go.kr)에서 [조달청_나라장터 입찰공고정보서비스](https://www.data.go.kr/data/15129394/openapi.do) 활용신청 후 발급. 마이페이지 → 개발계정 상세보기에서 **"일반 인증키(Decoding)"** 값을 씁니다. Encoding 키를 넣으면 인코딩이 두 번 적용돼서 인증 오류가 납니다.
- **Supabase service_role 키** — Supabase 대시보드 → 프로젝트 선택 → 좌측 `Settings` → `API` → `Project API keys` → **`service_role`** 의 `Reveal` 클릭 후 복사.

> `anon` 키가 아니라 **`service_role`** 키여야 합니다. 테이블에 RLS(행 수준 보안)가 켜져 있어서 `anon` 키로는 쓰기가 막힙니다. 이 키는 모든 권한을 가지므로 **코드나 공개된 곳에 절대 넣지 마세요.** 깃허브 Secrets에만 둡니다.

---

## 0단계 — 저장소 만들기

1. 깃허브 로그인 → 우측 상단 **`+`** → **`New repository`**
2. **Repository name**: `narajangter-nego` (아무 이름이나 됩니다)
3. **Visibility**: **`Private` 를 반드시 선택하세요.**
   수집 원문에 발주기관 담당자 이름·전화·이메일이 들어옵니다.
4. `Add a README file` 은 **체크하지 마세요.** 우리가 올릴 파일에 이미 있습니다.
5. **`Create repository`**

---

## A. 웹 화면으로 올리기 (깃 명령어 없이)

방금 만든 빈 저장소 화면에 **`uploading an existing file`** 링크가 보입니다. 그걸 누르세요.
(이미 파일이 있는 저장소라면 `Add file` → `Upload files`)

### A-1. 압축 풀기

받으신 `narajangter-nego.zip` 을 풀면 `nego` 폴더가 나옵니다. 그 **안쪽 내용**을 올려야 합니다.

```
nego/                    ← 이 폴더를 여세요
├── nego/                ← 올릴 것
├── config/              ← 올릴 것
├── tests/               ← 올릴 것
├── scripts/             ← 올릴 것
├── .github/             ← 올릴 것 (중요! 아래 A-3 참고)
├── README.md
├── SETUP.md
├── requirements.txt
└── .gitignore
```

`nego` 폴더 **자체**를 통째로 올리면 경로가 `nego/nego/...` 로 한 단계 깊어져서 워크플로가 파일을 못 찾습니다. **폴더를 열고 안의 것들을 선택**하세요.

### A-2. 드래그해서 올리기

파일 탐색기에서 위 항목들을 전부 선택(Ctrl+A) → 브라우저의 점선 영역으로 끌어다 놓기 → 아래 `Commit changes` 버튼 클릭.

### A-3. `.github` 폴더가 안 올라갈 때

**점(`.`)으로 시작하는 폴더는 운영체제가 숨김 처리**해서 탐색기에 안 보일 수 있습니다. `.github` 와 `.gitignore` 둘 다 그렇습니다.

- **Windows**: 탐색기 상단 `보기` 탭 → `숨긴 항목` 체크
- **macOS**: Finder에서 `Command + Shift + .` (마침표)

그래도 안 되면 웹에서 직접 만들어도 됩니다.

1. 저장소 화면에서 `Add file` → `Create new file`
2. 파일 이름 칸에 정확히 이렇게 입력: `.github/workflows/daily.yml`
   **슬래시(`/`)를 치는 순간 폴더가 자동으로 만들어집니다.**
3. `daily.yml` 내용을 복사해 붙여넣기
4. `Commit changes`

`.gitignore` 도 같은 방법으로 만들면 됩니다.

---

## B. 명령어로 올리기

터미널(Windows는 Git Bash 또는 PowerShell)에서 압축 푼 `nego` 폴더로 이동한 뒤:

```bash
cd nego                 # 압축 푼 폴더 안으로

git init                # 이 폴더를 깃 저장소로 만들기
git add .               # 모든 파일을 커밋 대상에 올리기 (.gitignore에 적힌 건 자동 제외)
git commit -m "협상에 의한 계약 공고 추출 시스템 초기 버전"

git branch -M main      # 기본 브랜치 이름을 main으로
git remote add origin https://github.com/<본인계정>/narajangter-nego.git
git push -u origin main
```

`<본인계정>` 자리에 깃허브 아이디를 넣으세요. 주소는 저장소 화면의 초록색 `Code` 버튼에서 복사할 수 있습니다.

푸시할 때 비밀번호를 물으면 계정 비밀번호가 아니라 **Personal Access Token**이 필요합니다. 깃허브 → 우측 상단 프로필 → `Settings` → 맨 아래 `Developer settings` → `Personal access tokens` → `Tokens (classic)` → `Generate new token` → `repo` 권한 체크 → 생성된 문자열을 비밀번호 자리에 붙여넣으세요.

---

## 1단계 — Secrets 등록 (키 넣기)

키를 코드에 적지 않고 깃허브 금고에 넣습니다.

저장소 화면 → **`Settings`** 탭 → 왼쪽 메뉴 **`Secrets and variables`** → **`Actions`** → **`New repository secret`**

아래 키를 하나씩 등록합니다. 이름은 **대소문자까지 정확히** 같아야 합니다.

| Name | Secret |
|---|---|
| `NARA_SERVICE_KEY` | 나라장터 인증키 (Decoding 값) |
| `SUPABASE_URL` | `https://ottfetbpuyytdrqotefg.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase `service_role` 키 |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 (아래 "텔레그램 봇 만들기") |
| `TELEGRAM_CHAT_ID` | 보고서를 받을 대화방 ID. 여러 곳이면 쉼표로 구분 |
| `TELEGRAM_DEV_CHAT_ID` | (선택) 개발자 알림 방 — 수집 실패(시도마다)·회복 알림만. 자세한 건 `docs/telegram.md` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURE`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` | (선택) 주간 리포트 메일 — 매주 월요일 보고를 `config/recipients.json`의 주소로 보냅니다. `SMTP_SECURE`는 SSL(보통 465)이면 `true`, STARTTLS(587)이면 비워 둡니다. 없으면 메일만 건너뜁니다 |
| `CORPUS_PASSPHRASE` | (선택) 싱크로율 자료 묶음(`corpus/sync_bundle.enc`) 비밀번호 — 없으면 메일 리포트의 싱크로율만 빠집니다. 묶음 만드는 법은 `nego/corpus.py` 맨 위 설명 |

> 한 번 저장하면 **다시 볼 수 없습니다.** 값을 바꾸려면 새로 덮어써야 합니다. 정상입니다.

텔레그램 두 개를 등록하지 않으면 수집·저장은 그대로 되고 **발송만 건너뜁니다.**

### 텔레그램 봇 만들기 (무료, 5분)

1. 텔레그램 앱에서 **`@BotFather`** 를 검색해 대화를 열고 `/newbot` 을 보냅니다.
2. 봇 이름과 아이디(끝이 `bot` 으로 끝나야 함, 예: `jiil_bid_bot`)를 차례로 보내면 **토큰**(`123456789:AA...` 형태)을 줍니다 → `TELEGRAM_BOT_TOKEN` 에 등록.
3. 보고서를 받을 사람이 **방금 만든 봇을 검색해 `/start` 를 한 번 보냅니다.** (봇은 먼저 말을 걸 수 없어서 이 단계가 꼭 필요합니다.) 여러 명이 같이 받으려면 단체방을 만들고 봇을 초대한 뒤 단체방에 아무 메시지나 한 번 보냅니다.
4. 브라우저 주소창에 `https://api.telegram.org/bot<토큰>/getUpdates` 를 열면 결과 안에 `"chat":{"id":123456789,...}` 가 보입니다. 이 숫자가 **대화방 ID** → `TELEGRAM_CHAT_ID` 에 등록. (단체방은 `-100...` 처럼 음수입니다. 마이너스까지 그대로 넣으세요.)

> 대화방 ID는 PC 텔레그램(데스크톱 앱, web.telegram.org)에서도 같은 대화로 보입니다. 핸드폰·PC 어디서든 받습니다.

테이블 이름은 코드에 기본값이 들어 있어서 등록하지 않아도 됩니다. 바꾸고 싶으면 같은 화면의 **`Variables`** 탭에서 `SUPABASE_TABLE` 을 추가하세요. (Secret이 아니라 Variable입니다 — 비밀이 아니니까요)

---

## 2단계 — 손으로 한 번 돌려보기

자동 실행을 기다리지 말고 먼저 확인합니다.

1. 저장소 화면 → **`Actions`** 탭
2. 처음이면 *"Workflows aren't being run on this forked repository"* 또는 워크플로 활성화 버튼이 뜹니다. 있으면 눌러서 켜주세요.
3. 왼쪽 목록에서 **`나라장터 공고 수집`** 클릭
4. 오른쪽 **`Run workflow`** 버튼 → `days` 에 `7` 입력 (첫 실행은 일주일치가 결과 보기 좋습니다) → 초록 **`Run workflow`**
   - 특정 유형만 보려면 `categories` 에 `입찰` 또는 `협상,규격가격동시입찰` 처럼 넣습니다. 비우면 전 유형입니다.
   - 텔레그램 발송까지 시험하려면 `telegram` 에 `true` 를 넣습니다.
5. 잠시 뒤 목록에 실행 항목이 생깁니다. 클릭 → `collect` 클릭 → 각 단계 로그가 펼쳐집니다.

### 결과 확인하는 곳

- **로그** — `수집 실행` 단계를 펼치면 `수집 N건 → 대상 유형 N건 → 업역 N건 → 후보 N건` 과 공고 목록이 그대로 찍힙니다.
- **리포트 파일** — 실행 화면 맨 아래 `Artifacts` 의 `nego-report-...` 를 내려받으면 HTML·CSV가 들어 있습니다.
- **Supabase** — 대시보드 → `Table Editor` → `협상에의한계약 추출 로직_1차`
- **텔레그램** — `telegram=true` 로 돌렸으면 등록한 대화방에 보고서 메시지와 HTML 리포트 파일이 옵니다.

---

## 3단계 — 자동 실행

> **2026-10-12부터 켜져 있습니다** (텔레그램 발송만 — Supabase 저장은 수집 기능이 완성된 뒤에 켭니다).

**2026-10-21부터 정시 실행은 외부 예약 서비스(cron-job.org)가 맡습니다** — 설정은 `docs/telegram.md` 9절.
아래 GitHub 예약(`schedule:`)은 12:00 백업으로, 그날 외부 예약 실행이 이미 있으면 수집 없이 끝납니다.

| 언제 | 무엇을 | 조회 기간 |
|---|---|---|
| 평일 09:00 (한국시간) | **입찰** 공고 → 텔레그램 | 어제 하루 (0시~23시59분 게시분). 월요일은 금~일까지 |
| 매주 월요일 09:10 | **협상 · 규격가격동시입찰 · 사전규격** 공고 → 텔레그램 | 지난 7일 (월~일) |

```yaml
- cron: "0 3 * * 1-5"   # 03:00 UTC = 12:00 KST — 평일, 입찰 (백업)
- cron: "10 3 * * 1"    # 월요일 03:10 UTC = 월요일 12:10 KST — 주간, 협상·규격가격동시입찰 (백업)
```

깃허브 서버는 UTC로 돌아서 9시간을 빼서 적습니다. 시간을 바꾸려면 이 줄을 고치되, **주간 줄을 바꾸면 같은 파일의 "실행 모드 결정" 단계에 적힌 문자열(`10 3 * * 1`)도 똑같이 바꿔야** 주간 실행으로 인식합니다.

조회 기간을 날짜 단위로 딱 잘라서 돌리기 때문에 예약 실행이 늦어져도 **같은 공고가 두 번 오거나 빠지지 않습니다.**

> 깃허브의 예약 실행은 서버가 붐비면 **몇 시간씩 늦어질 수 있습니다**(실측 09:00 예약 → 11시 전후 시작). 그래서 정시는 외부 예약으로 맞춥니다.
> 그리고 **60일 동안 저장소에 아무 활동이 없으면 예약 실행이 자동으로 멈춥니다.** 멈추면 Actions 탭에 안내가 뜨고, 아무 커밋이나 하나 하면 다시 돕니다.

---

## 잘 안 될 때

| 증상 | 원인과 해결 |
|---|---|
| `Actions` 탭에 워크플로가 안 보임 | `.github/workflows/daily.yml` 경로가 정확한지 확인. `.github` 가 최상단에 있어야 합니다 (`nego/.github` 가 아니라) |
| `NARA_SERVICE_KEY 환경변수가 필요합니다` | Secret 이름 오타. 대소문자와 언더바까지 정확히 |
| `resultCode=30 등록되지 않은 서비스키` | Encoding 키를 넣었을 가능성이 큽니다. **Decoding(평문)** 키로 바꾸세요 |
| `resultCode=31 기한만료된 서비스키` | data.go.kr에서 활용기간 연장 신청 |
| `resultCode=22 서비스 요청제한횟수 초과` | 일일 트래픽 한도. 다음 날 다시 돌거나 포털에서 한도 증액 신청 |
| `Supabase 저장 실패 (HTTP 401)` | `service_role` 키가 아니라 `anon` 키를 넣었을 가능성 |
| `Supabase 저장 실패 (HTTP 404)` | 테이블 이름이 다릅니다. 공백과 언더바까지 정확히 `협상에의한계약 추출 로직_1차` |
| 후보가 0건 | 정상일 수 있습니다. 조회 기간을 `--days 30` 으로 늘려보세요. 로그의 `협상 N건` 이 0이면 그 기간에 협상 공고 자체가 없었던 것이고, `업역 N건` 에서 0이 됐다면 키워드 문제입니다 |
| 리포트 값이 `미상` 투성이 | API 필드명이 바뀌었을 수 있습니다. 로컬에서 `python -m nego --verify` 를 돌리면 실제 응답 키가 그대로 찍힙니다. 그걸 보고 `nego/fields.py` 의 후보 목록에 한 줄 추가하면 됩니다 |

---

## 설정 바꾸기

**코드를 고칠 필요 없이** `config/` 안의 JSON만 수정하면 다음 실행부터 반영됩니다.

| 파일 | 무엇 |
|---|---|
| `config/keywords.json` | 키워드 / 제외키워드 / 최소예산 |
| `config/codes.json` | 관심 세부품명번호·업종코드 |
| `config/held_qualifications.json` | 지일 보유 등록물품·등록업종 |

깃허브 웹에서 파일을 열고 연필 아이콘(`Edit this file`)을 눌러 고친 뒤 `Commit changes` 하면 됩니다.

> `held_qualifications.json` 은 기존 주간 이메일 시스템(`narajangter-bid-monitor`)에서 복사해 온 것입니다. **경쟁입찰참가자격등록증이 갱신되면 두 저장소 다 고쳐야 합니다.**

### 바꾸기 전에 확인하는 법

키워드를 손대면 어떤 공고가 더 들어오고 빠지는지 미리 볼 수 있습니다. 로컬에서:

```bash
python -m nego --days 30            # 한 번 수집해서 data/notices.jsonl 에 저장
# config/keywords.json 수정
python -m nego --from-store         # API 재호출 없이 저장된 원문으로만 다시 필터링
```

`--from-store` 는 네트워크를 타지 않아서 몇 초면 끝납니다. API 한도도 안 씁니다.
