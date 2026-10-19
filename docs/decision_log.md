# 판단 기록(참가·보류·불참) 코드 정리

2026-10-19 기준, 브랜치 `claude/collector-integration`. 담당자 화면을 팀원 화면(ui.html)으로 바꾸면서 저장 방식도 팀원 화면과 같게 바뀌었다.

## 1. 한눈에

```
[카드의 참가여부 버튼·사유 버튼·사유 입력·되돌리기·담당·대화]
        │  POST /api/participation  {noticeNo, title, by, status|tags|reason|undo|owner|comment…, meta}
        ▼
webapp/server.py  →  webapp/mstore.py  TeamStore.update()
        │  공고번호 키로 한 건씩 덮어씀 (+ 바로 앞 상태 prev 하나)
        ▼
data/participation.json   (서버 PC에만 있음 — git에 안 올라감)
        │  GET /api/participation  (화면이 10초마다 다시 받음)
        ▼
[카드: "비슷한 공고 지난 판단"]   [판단 기록 탭: 목록·검색·CSV·집계]
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
| `tests/test_team_ui.py` | 저장 규칙 테스트 |

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
- **되돌리기**: 바로 앞 판단·담당으로(카드·판단 기록 탭 모두).
- **비슷한 공고 지난 판단**(`similarDecisions`): 공고명 낱말(두 글자 이상, "사업·용역·제작·설치" 같은 흔한 말 제외)이
  **2개 이상 겹치고 자카드 0.35 이상**, 또는 **수요기관이 같고 1개 이상 겹치면** 비슷한 것으로 보고 가장 많이 겹친 2건을 카드에 표시. **표시만 — 수집·판정은 안 바꿈.**
- **다른 사람 판단 반영**: 10초마다 `GET /api/participation`. 입력 중인 칸·펼친 대화창은 건드리지 않는다(`refreshCards`).
- **판단 기록 탭**(`renderLog`): 판단(참가·보류·불참)만 최근 순, 검색(공고명·번호·판단자·담당·사유)·상태 고르기, "목록에서 보기".
  - **CSV 내려받기**(`downloadLogCsv`, 엑셀 한글용 BOM): 판단일시·판단·주된 사유·사유·판단자·담당·공고번호·공고명·구분·업무·수요기관·
    사업금액·추천등급·낙찰방법·관심 키워드·등록 품목·싱크로율·마감·링크.
  - **집계**(`logAggHtml`, 판단 **30건 이상**부터): 보류·불참 주된 사유별 건수 + **관심 키워드별 참가·보류·불참·참가율**
    (3건 이상이고 불참 70% 이상이면 "키워드 조정 검토" 표시). **자동으로 규칙을 바꾸지 않음** — CSV를 받아 키워드 변경안을 제안 → 사용자 승인 후 반영.

## 6. 예전 기록 옮기기와 남아 있는 예전 코드

- **처음 한 번 옮기기**(`TeamStore._migrate`): `data/participation.json`이 없고 예전 `data/webapp.sqlite3`가 있으면 서버 시작 때
  `notice_state`(참가여부·담당·올림) · `decisions`(사유 칩·메모·판단자·snapshot → meta) · `comments`(대화)를 공고번호 키로 옮긴다.
  같은 공고의 여러 차수·여러 판단은 **마지막 것만** 남는다. sqlite 파일은 지우지 않음.
- **안 쓰는 예전 코드(새 화면은 부르지 않음)**: `webapp/store.py`의 `set_state`·`add_decision`·`decisions`·`add_comment`·`comments`와
  `DECISION_REASONS`·`DECISION_SUMMARY_MIN`, `webapp/server.py`의 `/api/state`·`/api/decision`·`/api/decisions`·`/api/decisions.csv`·`/api/comments`·`decisions_csv`.
  `Store`의 수집 결과 저장(`save_run`·`run`·`latest_run`)은 계속 쓴다. 지워도 되는지는 사용자 결정 대기.

## 7. 알아 둘 점

- `data/participation.json`은 **서버 PC에만** 있다 — 집계하려면 판단 기록 탭에서 CSV를 내려받아 주면 된다.
- 판단을 바꾸면 사유가 지워진다(새 판단엔 새 사유). 앞 판단은 `prev` 하나만 남아, 판단이 어떻게 바뀌어 왔는지는 기록되지 않는다.
- 이름 칸이 비어 있으면 저장하지 않는다("이름을 먼저 입력하세요").
