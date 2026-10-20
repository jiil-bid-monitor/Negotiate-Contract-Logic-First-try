# 판단 기록(참가·보류·불참) 코드 정리

2026-10-19 기준, 브랜치 `claude/collector-integration`. 담당자 화면을 팀원 화면(ui.html)으로 바꾸면서 저장 방식도 팀원 화면과 같게 바뀌었다.
2026-10-20: 팀원 레포 10/19 작업을 옮겨 **판단 기록 백업(구글 드라이브)**과 **텔레그램 보고에 나간 공고 → '미판단'·'놓침'**을 더했다 (8절).

## 1. 한눈에

```
[카드의 참가여부 버튼·사유 버튼·사유 입력·되돌리기·담당·대화]
        │  POST /api/participation  {noticeNo, title, by, status|tags|reason|undo|owner|comment…, meta}
        ▼
webapp/server.py  →  webapp/mstore.py  TeamStore.update()
        │  공고번호 키로 한 건씩 덮어씀 (+ 바로 앞 상태 prev 하나)
        ▼
data/participation.json   (서버 PC — git에 안 올라감)  ──저장할 때마다──▶  BACKUP_DIR (구글 드라이브 동기화 폴더)
        ▲                                                         participation.json·promoted.json·판단기록.csv·날짜별/
        │  5분마다 merge_report (webapp/reportsync.py)
        │  ◀── bot-state 브랜치 reports/*.json ◀── 텔레그램 보고(Actions, --report-log)
        │  GET /api/participation  (화면이 10초마다 다시 받음)
        ▼
[카드: "비슷한 공고 지난 판단"]   [판단 기록 탭: 미판단·놓침·참가·보류·불참, 검색·CSV·집계]
```

- **저장 단위**: 공고번호(차수 없이). 정정공고로 차수가 올라도 판단·대화가 그대로 이어진다.
- **이력**: 공고마다 **현재 판단 하나 + 바로 앞 상태(prev) 하나**만 남는다. 예전 sqlite처럼 모든 판단 이력을 남기지는 않는다.
- **판단으로 치는 것**: 참가·보류·불참. 검토중은 "아직 판단 전"이라 판단 기록 탭·집계에 안 들어간다.

## 2. 파일별 역할

| 파일 | 하는 일 |
|---|---|
| `webapp/mstore.py` | 저장. `TeamStore.update()`(참가여부·사유·담당·대화), `TeamStore.promote()`(제외 공고 올리기), 예전 sqlite 기록을 처음 한 번 옮기기(`_migrate`) |
| `webapp/server.py` | `GET /api/participation` → `{participation, statuses}`, `POST /api/participation` → `team.update(data)`, `POST /api/promote` |
| `webapp/static/index.html` | 화면. 카드 참가여부 칸(`partBlock`·`decisionRow`), 비슷한 지난 판단(`similarDecisions`·`pastHint`), 판단 기록 탭(`renderLog`·`logAggHtml`·`downloadLogCsv`), 클릭 처리(`data-pstatus`·`data-rtag`·`data-pundo`…) |
| `webapp/backup.py` | 판단 기록을 `BACKUP_DIR`에 복사(날짜별 사본 30일), 이 PC에 파일이 없으면 백업에서 되살리기 |
| `nego/report_log.py` | 보고 기록 만들기·읽기·판단 기록에 합치기(`merge`), '미판단'·'놓침' 판정(`log_status`), `판단기록.csv`(`decisions_csv`) |
| `webapp/reportsync.py` | bot-state의 보고 기록을 5분마다 받아 `TeamStore.merge_report` |
| `tests/test_team_ui.py` | 저장 규칙 테스트 |
| `tests/test_report_log.py` | 백업·보고 기록·미판단/놓침·CSV 테스트 |

## 3. 저장되는 모양 (`data/participation.json`)

```json
{
  "R26BK01764801": {
    "noticeNo": "R26BK01764801",
    "title": "서당골천 경관개선 조형물 제작·설치",
    "status": "불참",                       // 검토중 | 참가 | 보류 | 불참 | null
    "tags": ["자격·면허 부족", "지역 제한"],   // 주된 사유 버튼 (최대 12개, 각 30자)
    "reason": "조경 면허 필수라 단독 수행 불가", // 자세한 사유 (최대 500자)
    "decidedBy": "홍길동", "decidedAt": "2026-10-19T01:23:45.678Z",
    "owner": "홍길동", "autoOwner": true,    // 담당 — 판단한 사람이 자동으로 담당이 됨
    "meta": {                                // 판단할 때의 공고 정보 (CSV·집계용)
      "sourceType": "본공고", "businessType": "물품", "institution": "거제시",
      "budgetAmount": 139000000, "confidence": "강력추천", "bidMethod": "입찰",
      "keywords": ["조형물"], "products": [], "score": 52,
      "deadline": "2026-10-24T18:00", "detailUrl": "https://…"
    },
    "prev": { "status": "보류", "reason": "", "tags": [], "owner": "", … },  // 되돌리기용, 하나만
    "comments": [{ "by": "홍길동", "text": "공동수급 상대 찾는 중", "at": "…", "editedAt": "…" }],
    "reports": [{ "date": "2026-10-20", "kind": "daily" }],  // 이 공고가 나간 텔레그램 보고 (8절) — 있으면 판단 전이어도 탭에 '미판단'
    "updatedAt": "2026-10-19T01:23:45.678Z"
  }
}
```

`data/promoted.json`(제외 공고 → 목록에 올리기)은 `{공고번호: {by, at, title, keyword, reason}}`.

## 4. 저장 규칙 (`TeamStore.update`, `webapp/mstore.py:118`)

요청 하나에 아래 항목이 섞여 와도 된다. 이름(`by`)은 필수.

| 보낸 값 | 하는 일 |
|---|---|
| `status` (다를 때만) | 지금 상태를 `prev`에 저장 → 상태를 바꾸고 **사유·사유 버튼을 비움**, `decidedBy/At` = 지금 사람·시각, **담당 = 판단한 사람**(`autoOwner`). `status: ""` = 해제(담당도 지움) |
| `meta` | 상태가 바뀔 때만, 4,000자 이하면 저장 |
| `tags` / `reason` (status 없이) | 사유 버튼·사유만 고침(`prev` 안 바꿈) |
| `undo: true` | `prev`로 되돌리고 `prev`를 비움 — 한 번만 되돌릴 수 있음 |
| `owner` | 담당을 바꾸고(다르면 `prev` 저장) `autoOwner = false` |
| `comment` / `editComment` / `deleteComment` | 대화 추가 / 고치기·지우기(**내가 쓴 것만**) |

## 5. 화면 동작 (`webapp/static/index.html`)

- **참가여부 버튼**: 누르면 `{status, meta: decisionMeta(공고번호)}` 전송. 같은 버튼을 다시 누르면 해제.
  참가·보류·불참이면 사유 입력칸에 커서를 둔다.
- **사유 버튼(여러 개, 누를 때마다 바로 저장)**
  - 보류·불참: 과업 성격 다름 / 운영·유지보수 위주 / 자격·면허 부족 / 실적 요건 미달 / 지역 제한 / 금액 적음 / 마감 촉박 / 공동수급 상대 없음 / 인력·일정 부족
  - 참가: 본업과 맞음 / 유사 실적 있음 / 자격 충족 / 금액·규모 적정 / 지역 유리 / 공동수급 상대 있음 / 아는 발주기관 / 일정 여유 / 새 분야·지역 진출
- **자세한 사유**: 입력칸 + "사유 저장"(선택). 저장 뒤엔 "사유 고치기".
  - (10/20 사용자 요청) **칩만 골라도 저장 끝** — 칩이 있는 판단은 자세히 적기 칸을 경고색 대신 보통 테두리로, 버튼은 "자세히 저장",
    칩 머리말에 "누르면 바로 저장"·고른 칩이 있으면 "✓ 저장됨". 칸을 비운 채 버튼을 누르면 칩이 있으면 그대로(이미 저장됨), 없으면 "칩을 누르면 저장" 안내.
- **되돌리기**: 바로 앞 판단·담당으로(카드·판단 기록 탭 모두).
- **비슷한 공고 지난 판단**(`similarDecisions`): 공고명 낱말(두 글자 이상, "사업·용역·제작·설치" 같은 흔한 말 제외)이
  **2개 이상 겹치고 자카드 0.35 이상**, 또는 **수요기관이 같고 1개 이상 겹치면** 비슷한 것으로 보고 가장 많이 겹친 2건을 카드에 표시. **표시만 — 수집·판정은 안 바꿈.**
- **다른 사람 판단 반영**: 10초마다 `GET /api/participation`. 입력 중인 칸·펼친 대화창은 건드리지 않는다(`refreshCards`).
- **판단 기록 탭**(`renderLog`·`logEntriesOf`): 판단(참가·보류·불참)과 **보고됐지만 판단 전인 공고**('미판단', 판단 없이 마감 지나면 '놓침' — 8절)를 최근 순,
  검색(공고명·번호·판단자·담당·사유)·상태 고르기(미판단·놓침 포함), 보고일 표시, "목록에서 보기"(판단 전이면 "판단하러 가기").
  - **CSV 내려받기**(`downloadLogCsv`, 엑셀 한글용 BOM): 판단일시·판단·주된 사유·사유·판단자·담당·공고번호·공고명·구분·업무·수요기관·
    사업금액·추천등급·낙찰방법·관심 키워드·등록 품목·싱크로율·마감·링크·**보고일·보고(매일/주간)**. 구글 드라이브의 `판단기록.csv`와 같은 열.
  - **집계**(`logAggHtml`, **실제 판단(참가·보류·불참) 30건 이상**부터 — 미판단·놓침은 세지 않음): 보류·불참 주된 사유별 건수 + **관심 키워드별 참가·보류·불참·참가율**
    (3건 이상이고 불참 70% 이상이면 "키워드 조정 검토" 표시). **자동으로 규칙을 바꾸지 않음** — CSV를 받아 키워드 변경안을 제안 → 사용자 승인 후 반영.

## 6. 예전 기록 옮기기와 남아 있는 예전 코드

- **처음 한 번 옮기기**(`TeamStore._migrate`): `data/participation.json`이 없고 예전 `data/webapp.sqlite3`가 있으면 서버 시작 때
  `notice_state`(참가여부·담당·올림) · `decisions`(사유 칩·메모·판단자·snapshot → meta) · `comments`(대화)를 공고번호 키로 옮긴다.
  같은 공고의 여러 차수·여러 판단은 **마지막 것만** 남는다. sqlite 파일은 지우지 않음.
- **안 쓰는 예전 코드(새 화면은 부르지 않음)**: `webapp/store.py`의 `set_state`·`add_decision`·`decisions`·`add_comment`·`comments`와
  `DECISION_REASONS`·`DECISION_SUMMARY_MIN`, `webapp/server.py`의 `/api/state`·`/api/decision`·`/api/decisions`·`/api/decisions.csv`·`/api/comments`·`decisions_csv`.
  `Store`의 수집 결과 저장(`save_run`·`run`·`latest_run`)은 계속 쓴다. 지워도 되는지는 사용자 결정 대기.

## 7. 알아 둘 점

- `data/participation.json`의 원본은 **서버 PC**에 있다. `BACKUP_DIR`을 켜 두면 구글 드라이브에 사본과 `판단기록.csv`가 있어
  PC가 고장 나도 남고, 드라이브에서 표로 볼 수 있다(8절). 백업을 켜지 않았으면 서버 PC에만 있다.
- 판단을 바꾸면 사유가 지워진다(새 판단엔 새 사유). 앞 판단은 `prev` 하나만 남아, 판단이 어떻게 바뀌어 왔는지는 기록되지 않는다.
- 이름 칸이 비어 있으면 저장하지 않는다("이름을 먼저 입력하세요").

## 8. 판단 기록 백업 · 보고된 공고 '미판단' (2026-10-20, 팀원 레포 10/19 이식)

**백업** (`webapp/backup.py`, `TeamStore._backup`)
- 서버 환경변수 `BACKUP_DIR`에 구글 드라이브 데스크톱이 동기화하는 폴더를 적으면 켜진다. 비어 있으면 꺼짐.
  - **웹앱은 `.env` 파일을 읽지 않는다**(10/20 정정 — 예전에 ".env에 적으면"이라고 잘못 적었음). 서버 PC에서 PowerShell로
    `[Environment]::SetEnvironmentVariable("BACKUP_DIR", "G:\내 드라이브\입찰모니터_백업_통합", "User")`
    → **새 창**에서 웹앱을 다시 켠다(PC를 껐다 켜도 남고, 바로가기·배치 파일 등 어떤 방법으로 켜도 적용).
    켜질 때 `판단 기록 백업: <경로>`가 찍히면 켜진 것, `⚠ 폴더가 없습니다`면 경로 오타, `꺼져 있음`이면 옛 창에서 켠 것.
  - 192.168.0.22 서버는 `입찰모니터_백업_통합` 폴더를 쓴다 — `입찰모니터_백업`은 팀원 레포 화면(TS)이 쓰던 폴더라 같이 쓰면 서로 덮어씀.
    담당자용 단계별 안내: `docs/backup_setup.md`(구글 드라이브 데스크톱 설치 → 폴더 → 환경변수 → 재시작 → 확인).
- 저장할 때마다 `participation.json`·`promoted.json`을 그 폴더에 복사하고, `날짜별/`에 하루 한 개씩 30일치를 남긴다(드라이브 버전 기록 30일과 이중).
- `판단기록.csv` — 판단 기록 탭 CSV와 같은 표(미판단·놓침 포함). 드라이브에서 누르면 표로 보인다. 보기용이라 날짜별 사본은 없음.
- 서버가 뜰 때와 2분 뒤(로그인 직후 드라이브가 늦게 붙는 경우) 한 번씩 백업. 백업이 실패해도 저장은 그대로 되고 경고만 남긴다.
- 서버가 뜰 때 이 PC에 `participation.json`·`promoted.json`이 없으면 백업에서 가져온다(새 PC·고장 후 재설치). 있으면 건드리지 않는다.
- 백업에는 담당자 이름·사유·대화가 있다 — 드라이브 폴더는 팀원에게 '뷰어'로만 공유.

**보고된 공고 → 판단 기록** (`nego/report_log.py`, `webapp/reportsync.py`)
1. 텔레그램 보고(Actions)가 `--report-log daily|weekly`로 나간 공고 목록을 `output/report-log/{보고일}-{종류}.json`에 남긴다
   (공고번호·공고명·meta — 공개된 공고 정보만. 판단·담당자 이름은 없다. 저장소가 공개라서).
2. 워크플로 "보낸 공고 기록 저장"이 bot-state 브랜치의 `reports/`에 올린다(`sent_bids.json`과 같은 브랜치, 예전 덮어쓰기 대신 받아서 더하기).
3. 웹앱 서버가 15초 뒤 한 번, 그 뒤 5분마다 `git fetch origin bot-state`(원격 기록만 갱신, 작업 폴더는 안 건드림)로 받아 `merge_report`.
   - 처음 보는 공고 → `status: null` + `reports` + 보고 때 meta → 탭에 **미판단**
   - 이미 있는 공고 → 판단·담당·대화·판단 때 meta는 그대로, `reports`에 보고일만 더함 (매일·주간에 같이 나와도 한 줄)
   - 같은 보고를 여러 번 받아도 결과가 같다
4. 판단 전인데 기준 마감(meta.deadline)이 지나면 **놓침**(시각 없는 마감은 그날 23:59까지). 서버 `log_status`와 화면 `logStatusOf`가 같은 규칙.
- PC가 꺼져 있던 동안의 보고는 켜진 뒤 한꺼번에 들어온다. 꺼져 있던 사이 마감된 공고는 들어오자마자 '놓침'.
- 첫 보고 전(bot-state에 reports/가 없을 때)에는 서버 로그에 "보고 기록을 받아오지 못했습니다"가 한 번 남는다 — 정상.
