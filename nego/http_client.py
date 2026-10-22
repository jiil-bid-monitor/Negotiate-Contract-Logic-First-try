"""공공데이터포털(data.go.kr) API 호출 클라이언트.

기존 TypeScript 시스템(`src/api/httpClient.ts`)에서 실측으로 검증된 처리 방식을
그대로 옮겼다. 다시 만들지 말 것 — 아래 세 가지는 전부 실제로 겪은 문제에 대한 대응이다.

1. 오류 응답이 표준 형식(response.header)이 아닌 경우가 있다
   (`{"nkoneps.com.response.ResponseError": {...}}`). 고정 경로만 보면 실제 오류코드를
   놓치므로 응답 트리를 재귀 탐색해서 resultCode를 찾는다.
2. items 필드가 배열 / 단일 객체 / {item: [...]} / {item: {...}} / 빈 문자열 등
   여러 형태로 내려온다. 전부 list[dict]로 정규화한다.
3. type=json을 요청해도 인증 오류 등에서는 XML이 돌아온다. 둘 다 시도한다.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

RawItem = dict[str, Any]

# 정상 응답 코드. data.go.kr은 "00" 또는 "0"을 성공으로 쓴다.
SUCCESS_RESULT_CODES = {"00", "0"}

# 재시도해도 의미가 있는 오류. 나머지(인증키 오류 등)는 재시도해도 그대로다.
RETRYABLE_RESULT_CODES = {
    "01",  # 어플리케이션 에러
    "02",  # 데이터베이스 에러
    "03",  # 데이터없음 에러 (일시적으로 뜨는 경우가 있음)
    "04",  # HTTP 에러
    "05",  # 서비스 연결실패
    "22",  # 서비스 요청제한횟수 초과
    "99",  # 기타 에러
}

RESULT_CODE_DESCRIPTIONS = {
    "00": "정상",
    "01": "어플리케이션 에러",
    "02": "데이터베이스 에러",
    "03": "데이터 없음",
    "04": "HTTP 에러",
    "05": "서비스 연결 실패",
    # 표준 공통코드는 아니지만 면허제한정보/참가가능지역 조회에서 실측됨.
    # 조회기간(inqryBgnDt~inqryEndDt)이 이 오퍼레이션이 허용하는 범위보다 길면 뜬다.
    "07": "입력범위값 초과 에러 (조회기간이 너무 깁니다)",
    "10": "잘못된 요청 파라미터",
    "11": "필수 요청 파라미터 누락",
    "12": "해당 오픈API서비스가 없거나 폐기됨",
    "20": "서비스 접근 거부",
    "22": "서비스 요청제한횟수 초과 (일일 트래픽 한도)",
    "30": "등록되지 않은 서비스키",
    "31": "기한만료된 서비스키",
    "32": "등록되지 않은 도메인",
    "99": "기타 에러",
}


_SERVICE_KEY_RE = re.compile(r"(serviceKey=)[^&\s'\")]+", re.IGNORECASE)


def mask_service_key(text: str) -> str:
    """URL 쿼리의 serviceKey 값을 ***로 바꾼다."""
    return _SERVICE_KEY_RE.sub(r"\1***", text)


class ApiError(Exception):
    """네트워크/HTTP/파싱 단계 실패. 기본적으로 재시도 대상."""

    def __init__(self, label: str, message: str):
        super().__init__(f"[{label}] {message}")
        self.label = label


class ApiResultError(ApiError):
    """응답은 받았으나 resultCode가 정상이 아닌 경우."""

    def __init__(self, label: str, result_code: str, message: str):
        super().__init__(label, f"resultCode={result_code} {message}")
        self.result_code = result_code

    @property
    def retryable(self) -> bool:
        return self.result_code in RETRYABLE_RESULT_CODES


@dataclass
class Envelope:
    result_code: str
    result_msg: str
    items: list[RawItem] = field(default_factory=list)
    total_count: int = 0
    page_no: int = 1
    num_of_rows: int = 0


def _normalize_items(node: Any) -> list[RawItem]:
    """items 필드가 어떤 형태로 오든 list[dict]로 통일한다."""
    if node is None or node == "":
        return []
    if isinstance(node, list):
        return [v for v in node if isinstance(v, dict)]
    if isinstance(node, dict):
        if "item" in node:
            return _normalize_items(node["item"])
        return [node]
    return []


def _find_header(node: Any, depth: int = 0) -> tuple[str, str] | None:
    """응답 트리 어디에 있든 resultCode/resultMsg를 가진 객체를 재귀로 찾는다."""
    if depth > 5 or not isinstance(node, dict):
        return None
    if "resultCode" in node:
        return (
            str(node.get("resultCode", "")).strip(),
            str(node.get("resultMsg", "알 수 없는 응답 형식")).strip(),
        )
    for value in node.values():
        found = _find_header(value, depth + 1)
        if found:
            return found
    return None


def _xml_to_dict(elem: ET.Element) -> Any:
    """XML 응답을 dict로 변환 (같은 태그가 반복되면 리스트로)."""
    children = list(elem)
    if not children:
        return (elem.text or "").strip()
    out: dict[str, Any] = {}
    for child in children:
        value = _xml_to_dict(child)
        if child.tag in out:
            existing = out[child.tag]
            out[child.tag] = existing + [value] if isinstance(existing, list) else [existing, value]
        else:
            out[child.tag] = value
    return out


def _extract_envelope(parsed: Any) -> Envelope:
    root = parsed if isinstance(parsed, dict) else {}
    response = root.get("response", root)
    body = response.get("body", {}) if isinstance(response, dict) else {}
    if not isinstance(body, dict):
        body = {}

    header = _find_header(root)
    result_code = (header[0] if header else "") or "99"
    result_msg = header[1] if header else "알 수 없는 응답 형식"

    items = _normalize_items(body.get("items"))

    def _int(value: Any, default: int) -> int:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default

    return Envelope(
        result_code=result_code,
        result_msg=result_msg,
        items=items,
        total_count=_int(body.get("totalCount"), len(items)),
        page_no=_int(body.get("pageNo"), 1),
        num_of_rows=_int(body.get("numOfRows"), len(items)),
    )


def parse_response_body(text: str, label: str) -> Envelope:
    """응답 본문을 JSON 또는 XML로 해석한다."""
    trimmed = text.strip()
    if not trimmed:
        raise ApiError(label, "빈 응답을 받았습니다")

    if trimmed.startswith(("{", "[")):
        import json

        try:
            return _extract_envelope(json.loads(trimmed))
        except ValueError as err:
            raise ApiError(label, f"JSON 파싱 실패: {err}") from err

    try:
        root = ET.fromstring(trimmed)
    except ET.ParseError as err:
        raise ApiError(
            label, f"응답을 JSON/XML 어느 쪽으로도 해석할 수 없습니다 (앞부분: {trimmed[:200]})"
        ) from err

    parsed = _xml_to_dict(root)
    return _extract_envelope({root.tag: parsed} if isinstance(parsed, dict) else {})


# 실측: 30일은 통과, 180일은(오늘은 ~100일도) resultCode=07로 거부된다 —
# 정확한 상한은 모른다. 확인된 30일을 안전한 청크 크기로 쓴다.
MAX_QUERY_DAYS = 30


@dataclass
class ApiConfig:
    service_key: str
    timeout_sec: float = 30.0
    # 총 시도 횟수는 max_retries+1. apis.data.go.kr이 간헐적으로 connect timeout을
    # 내는 게 실측됐고, 이때 재시도로 회복되는 경우가 많아 여유 있게 잡는다.
    # 5회를 다 소진하면 run() 단이 전체 실행을 실패로 처리하고, 그 위(Actions
    # 워크플로)에서 새 Run으로 재시도한다 — 여기서 과하게 늘리면 그 재시도가
    # 늦게 발동한다.
    max_retries: int = 4
    retry_delay_sec: float = 1.0
    # 재시도 간격을 직접 정한다(초, 예: (10, 10, 30, 70)) — 있으면 retry_delay_sec·2배 대신 이걸 쓰고
    # 재시도 횟수도 이 길이(config.py가 맞춤). GitHub Actions만 씀(2026-10-22, daily.yml API_RETRY_DELAYS).
    retry_delays: tuple[float, ...] = ()
    num_of_rows: int = 999
    max_pages: int = 100
    request_interval_sec: float = 0.0


class DataGoKrClient:
    def __init__(self, config: ApiConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()

    def _call_once(self, base_url: str, operation: str, params: dict[str, str], label: str) -> Envelope:
        query = {"serviceKey": self.config.service_key, "type": "json", **params}
        url = f"{base_url.rstrip('/')}/{operation}?{urlencode(query)}"

        try:
            res = self.session.get(url, timeout=self.config.timeout_sec)
        except requests.RequestException as err:
            # 연결 오류 문구에 요청 주소(서비스키 포함)가 그대로 들어간다 — 로그·개발자 알림에 키가 새지 않게 가린다
            raise ApiError(label, f"요청 실패: {mask_service_key(str(err))}") from err

        if res.status_code >= 400:
            raise ApiError(label, f"HTTP {res.status_code} (응답 앞부분: {res.text[:200]})")

        envelope = parse_response_body(res.text, label)

        if envelope.result_code not in SUCCESS_RESULT_CODES:
            description = RESULT_CODE_DESCRIPTIONS.get(envelope.result_code, "알 수 없는 오류코드")
            detail = f" (원본 메시지: {envelope.result_msg})" if envelope.result_msg != description else ""
            raise ApiResultError(label, envelope.result_code, f"{description}{detail}")

        return envelope

    def _retry_delay(self, attempt: int) -> float:
        delays = self.config.retry_delays
        if delays:
            return delays[min(attempt, len(delays) - 1)]
        return self.config.retry_delay_sec * (2**attempt)

    def _call_with_retry(self, base_url: str, operation: str, params: dict[str, str], label: str) -> Envelope:
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                return self._call_once(base_url, operation, params, label)
            except ApiResultError as err:
                last_error = err
                retryable = err.retryable
            except ApiError as err:
                last_error = err
                retryable = True

            is_last = attempt == self.config.max_retries
            log.warning(
                "API 호출 실패 (%d/%d) %s: %s",
                attempt + 1,
                self.config.max_retries + 1,
                label,
                last_error,
            )
            if not retryable or is_last:
                break
            time.sleep(self._retry_delay(attempt))

        assert last_error is not None
        raise last_error

    def fetch_all_pages(
        self, base_url: str, operation: str, params: dict[str, str], label: str
    ) -> list[RawItem]:
        """페이지네이션을 끝까지 순회한다. totalCount와 max_pages로 무한루프를 막는다."""
        collected: list[RawItem] = []
        page_no = 1

        while page_no <= self.config.max_pages:
            envelope = self._call_with_retry(
                base_url,
                operation,
                {**params, "pageNo": str(page_no), "numOfRows": str(self.config.num_of_rows)},
                label,
            )
            collected.extend(envelope.items)

            got_all = len(collected) >= envelope.total_count
            partial_page = len(envelope.items) < self.config.num_of_rows
            if got_all or partial_page or not envelope.items:
                break

            page_no += 1
            if self.config.request_interval_sec > 0:
                time.sleep(self.config.request_interval_sec)

        if page_no > self.config.max_pages:
            log.warning(
                "최대 페이지 수(%d)에 도달해 조회를 중단했습니다. 일부 데이터가 누락될 수 있습니다. [%s] 수집=%d",
                self.config.max_pages,
                label,
                len(collected),
            )

        return collected

    def fetch_all_pages_chunked(
        self,
        base_url: str,
        operation: str,
        params: dict[str, str],
        begin: str,
        end: str,
        label: str,
        max_days: int = MAX_QUERY_DAYS,
    ) -> list[RawItem]:
        """조회기간이 길면 `chunk_date_range`로 나눠 여러 번 호출해 합친다.

        본공고 3종·면허제한정보·참가가능지역 전부 조회기간이 너무 길면
        resultCode=07(입력범위값 초과)로 거부한다(실측: 30일은 통과, ~100일은
        거부 — 정확한 상한은 모른다). LOOKBACK_DAYS를 그 이상으로 잡아도
        되도록, 확인된 안전 범위(`MAX_QUERY_DAYS`) 단위로 쪼개 순서대로
        호출한다. 청크 하나가 실패하면 그 구간만 조용히 빠지지 않고 전체를
        실패로 올린다 — 일부만 수집해놓고 전체를 수집한 것처럼 보이면 안
        되기 때문이다.
        """
        collected: list[RawItem] = []
        for chunk_begin, chunk_end in chunk_date_range(begin, end, max_days):
            collected.extend(
                self.fetch_all_pages(
                    base_url, operation, {**params, "inqryBgnDt": chunk_begin, "inqryEndDt": chunk_end}, label
                )
            )
        return collected


def chunk_date_range(begin: str, end: str, days: int) -> Iterator[tuple[str, str]]:
    """조회 기간을 days 단위로 쪼갠다. 형식은 API가 요구하는 'YYYYMMDDHHMM'."""
    from datetime import datetime, timedelta

    fmt = "%Y%m%d%H%M"
    start_dt = datetime.strptime(begin, fmt)
    end_dt = datetime.strptime(end, fmt)

    cursor = start_dt
    while cursor <= end_dt:
        chunk_end = min(cursor + timedelta(days=days) - timedelta(minutes=1), end_dt)
        yield cursor.strftime(fmt), chunk_end.strftime(fmt)
        cursor = chunk_end + timedelta(minutes=1)
