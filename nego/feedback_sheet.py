"""피드백 구글 시트 — 텔레그램 보고에 나간 공고를 시트에 한 줄씩 쌓는다 (2026-10-20, 팀원 레포 이식).

팀원 레포(narajangter-bid-monitor `src/sheets/feedbackSheet.ts`·`feedbackRow.ts`)가 9월부터 쌓아 온 **같은 시트**에
같은 열로 이어서 쓴다. 매일·주간 보고에 같은 공고가 나와도 공고번호가 이미 있으면 건너뛴다. 시스템은 A~R열(18개)만 쓰고
S열 이후(판정·오판사유·판정자·판정일)는 담당자 몫으로 비워 둔다 — 이미 판정한 행은 절대 건드리지 않는다.
판단은 웹앱 판단 기록에서 하고, 시트는 보고된 공고 목록을 사무실 밖에서 보는 용도(사용자 결정).

필요한 환경변수: GOOGLE_SERVICE_ACCOUNT_JSON(서비스 계정 키 JSON 전문), FEEDBACK_SHEET_ID(시트 주소의 /d/ 와 /edit 사이),
FEEDBACK_SHEET_NAME(탭 이름, 기본 "피드백"). 둘 중 하나라도 없으면 아무것도 하지 않는다.
구글 로그인은 서비스 계정 JWT(RS256)를 직접 서명한다 — 이미 쓰는 `cryptography`만으로, 구글 라이브러리를 더 설치하지 않게.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"

# 팀원 레포 feedbackRow.ts FEEDBACK_HEADER와 같은 22열 — 순서를 바꾸면 이미 쌓인 시트와 어긋난다. 열을 더하려면 맨 끝에만.
HEADER = [
    "공고번호", "구분", "업무", "공고명", "발주기관", "추정가격(원)", "마감", "낙찰방법", "추천등급", "매칭근거",
    "해외의심", "링크", "수집일시", "싱크로율", "원점수", "싱크로율근거", "유사 과거사업", "공동수급방식",
    "판정", "오판사유", "판정자", "판정일",
]
SYSTEM_COLUMNS = 18
LAST_COLUMN = "V"
VERDICT_COLUMN_INDEX = 18  # S열 — 판정 드롭다운
VERDICT_OPTIONS = ["O (맞음)", "X (아님)", "보류"]

# 시트가 수식으로 읽는 시작 글자 — 공고명은 외부 입력이라 "=…"이 섞이면 셀이 깨진다. 앞에 작은따옴표(시트엔 안 보임)
_FORMULA_START = re.compile(r"^[-=+@]")


class SheetsError(RuntimeError):
    pass


def sanitize(value: Any) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if _FORMULA_START.match(text) else text


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def match_reason(candidate: Any) -> str:
    """어떤 규칙이 이 공고를 걸어 올렸는지 — 품목·업종·키워드 (팀원 레포 formatMatchReason과 같은 모양, 이름으로)."""
    sr = candidate.screen_result
    parts = ([f"품목 {p}" for p in sr.matched_product_codes or []]
             + [f"업종 {i}" for i in sr.matched_industry_codes or []]
             + [f"키워드 {k}" for k in sr.matched_keywords or []])
    return " / ".join(parts) if parts else "(근거 없음)"


def to_row(candidate: Any, collected_at: datetime) -> list[Any]:
    """공고 1건 → 시스템 열 18개 (팀원 레포 toFeedbackRow와 같은 순서·형식)."""
    n = candidate.notice
    sync = candidate.sync or {}
    score = sync.get("score")
    earliest = candidate.schedule.earliest
    sr = candidate.screen_result
    similar = " / ".join(f"{t.get('year')} {t.get('title')} ({t.get('score', 0):.3f})"
                         for t in sync.get("top") or [] if (t.get("score") or 0) > 0.02)
    return [
        sanitize(n.notice_no),
        n.kind,
        n.work_type,
        sanitize(n.title),
        sanitize(n.notice_institution or ""),
        # 숫자로 넣어야 시트에서 정렬·필터·합계가 된다. 금액 모름은 빈 칸
        n.budget if n.budget is not None else "",
        sanitize(_fmt(earliest[1]) if earliest else ""),
        sanitize(n.award_method or ""),
        sr.confidence or "",
        sanitize(match_reason(candidate)),
        f"몽골: {sr.mongolia}" if getattr(sr, "overseas_flag", False) and getattr(sr, "mongolia", None) else "",
        n.detail_url or "",
        _fmt(collected_at),
        # 0과 빈 칸은 뜻이 다르다 — 빈 칸 = 비교할 과거 실적 자료가 없었음
        round(score * 100) if isinstance(score, (int, float)) else "",
        round(score, 4) if isinstance(score, (int, float)) else "",
        ("제목+과업내용" if sync.get("basis") == "과업 원문" else "제목") if isinstance(score, (int, float)) else "",
        sanitize(similar),
        sanitize(candidate.joint.label if candidate.joint else ""),
    ]


def select_new_rows(candidates: list[Any], existing: set[str], collected_at: datetime) -> tuple[list[list[Any]], int]:
    """시트에 이미 있는 공고번호는 빼고 새 행만. 같은 실행 안의 중복도 막는다."""
    seen = set(existing)
    rows, skipped = [], 0
    for c in candidates:
        no = c.notice.notice_no
        if no in seen:
            skipped += 1
            continue
        seen.add(no)
        rows.append(to_row(c, collected_at))
    return rows, skipped


# ── 구글 로그인·시트 API ────────────────────────────────────────────

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _access_token(service_account_json: str, session: requests.Session) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    try:
        sa = json.loads(service_account_json)
    except json.JSONDecodeError as err:
        raise SheetsError("GOOGLE_SERVICE_ACCOUNT_JSON을 JSON으로 읽을 수 없습니다 — 키 파일 내용을 통째로 넣었는지 확인하세요") from err
    email, key = sa.get("client_email"), sa.get("private_key")
    if not email or not key:
        raise SheetsError("서비스 계정 JSON에 client_email 또는 private_key가 없습니다")
    key = key.replace("\\n", "\n")  # 한 줄로 넣으면 줄바꿈이 글자 '\n'으로 들어온다
    token_uri = sa.get("token_uri") or DEFAULT_TOKEN_URI
    now = int(time.time())
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({"iss": email, "scope": SCOPE, "aud": token_uri, "iat": now, "exp": now + 3600}).encode())
    private_key = serialization.load_pem_private_key(key.encode(), password=None)
    signature = private_key.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    res = session.post(token_uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                        "assertion": f"{header}.{claims}.{_b64(signature)}"}, timeout=30)
    if res.status_code != 200:
        raise SheetsError(f"구글 액세스 토큰 발급 실패 (HTTP {res.status_code}): {res.text[:300]}")
    return res.json()["access_token"]


class _Sheets:
    def __init__(self, token: str, sheet_id: str, sheet_name: str, session: requests.Session):
        self.token, self.sheet_id, self.name, self.session = token, sheet_id, sheet_name, session

    def call(self, path: str, method: str = "GET", body: Any = None) -> Any:
        res = self.session.request(method, f"{SHEETS_API}/{self.sheet_id}{path}", json=body, timeout=60,
                                   headers={"Authorization": f"Bearer {self.token}"})
        if not res.ok:
            hint = " — 시트를 서비스 계정 이메일에 '편집자'로 공유했는지 확인하세요" if res.status_code == 403 else ""
            raise SheetsError(f"구글 시트 API 실패 (HTTP {res.status_code}){hint}: {res.text[:300]}")
        return res.json() if res.text else {}

    def range(self, a1: str) -> str:
        return quote(f"{self.name}!{a1}", safe="")

    def ensure_tab(self) -> int:
        meta = self.call("?fields=sheets.properties")
        for s in meta.get("sheets", []):
            if s.get("properties", {}).get("title") == self.name:
                return s["properties"]["sheetId"]
        created = self.call(":batchUpdate", "POST", {"requests": [{"addSheet": {"properties": {"title": self.name}}}]})
        log.info("피드백 시트 탭 생성: %s", self.name)
        return created["replies"][0]["addSheet"]["properties"]["sheetId"]

    def ensure_header(self, tab_id: int) -> None:
        current = self.call(f"/values/{self.range(f'A1:{LAST_COLUMN}1')}")
        if current.get("values") and current["values"][0]:
            return
        self.call(f"/values/{self.range(f'A1:{LAST_COLUMN}1')}?valueInputOption=RAW", "PUT", {"values": [HEADER]})
        try:  # 서식은 실패해도 적재는 계속
            self.call(":batchUpdate", "POST", {"requests": [
                {"updateSheetProperties": {"properties": {"sheetId": tab_id, "gridProperties": {"frozenRowCount": 1}},
                                           "fields": "gridProperties.frozenRowCount"}},
                {"repeatCell": {"range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1},
                                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                                "fields": "userEnteredFormat.textFormat.bold"}},
                {"setDataValidation": {"range": {"sheetId": tab_id, "startRowIndex": 1, "startColumnIndex": VERDICT_COLUMN_INDEX,
                                                 "endColumnIndex": VERDICT_COLUMN_INDEX + 1},
                                       "rule": {"condition": {"type": "ONE_OF_LIST",
                                                              "values": [{"userEnteredValue": v} for v in VERDICT_OPTIONS]},
                                                "showCustomUi": True, "strict": False}}},
            ]})
        except SheetsError as err:
            log.warning("피드백 시트 서식 설정 실패 (적재는 계속): %s", err)

    def existing_notice_nos(self) -> set[str]:
        res = self.call(f"/values/{self.range('A2:A')}")
        return {str(r[0]).strip().lstrip("'") for r in res.get("values", []) if r and str(r[0]).strip()}

    def append(self, rows: list[list[Any]]) -> None:
        self.call(f"/values/{self.range('A1')}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
                  "POST", {"values": rows})


def append_candidates(candidates: list[Any], collected_at: datetime, session: requests.Session | None = None) -> dict[str, int] | None:
    """보고에 나간 공고를 시트에 쌓는다. 설정이 없으면 None(아무것도 안 함). 실패하면 SheetsError."""
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    sheet_id = os.environ.get("FEEDBACK_SHEET_ID", "").strip()
    if not sa_json or not sheet_id:
        return None
    session = session or requests.Session()
    sheets = _Sheets(_access_token(sa_json, session), sheet_id,
                     os.environ.get("FEEDBACK_SHEET_NAME", "").strip() or "피드백", session)
    tab_id = sheets.ensure_tab()
    sheets.ensure_header(tab_id)
    rows, skipped = select_new_rows(candidates, sheets.existing_notice_nos(), collected_at)
    # 행 길이가 어긋나면 담당자 입력란(판정 등)까지 덮어쓸 수 있어 중단
    if any(len(r) != SYSTEM_COLUMNS for r in rows):
        raise SheetsError(f"행 길이가 시스템 열 개수({SYSTEM_COLUMNS})와 다릅니다 — 담당자 입력란을 덮어쓸 수 있어 중단")
    if rows:
        sheets.append(rows)
    return {"appended": len(rows), "skipped": skipped}
