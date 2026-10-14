"""공고 첨부파일(HWP/HWPX/PDF, 그리고 이것들을 묶은 ZIP) 다운로드 및 텍스트 추출.

API가 안 주는 정보(과업내용·평가기준)를 다루기 위한 밑작업이다. 오늘 범위는
**평문 텍스트 추출까지만** — 지역제한/면허제한/공동수급처럼 API로 이미 수집한
값을 원문과 대조해볼 수 있게 하는 것이 목적이다. 표 구조 파싱(평가기준 배점표)은
다음 단계에서 이 텍스트를 재료로 다룬다.

실패는 전부 `AttachmentText.error`에 담고 예외를 올리지 않는다 — 첨부파일 하나
못 읽는다고 공고 전체 수집이 죽으면 안 되기 때문이다 (fail-open).
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
import zlib
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET

import requests

if TYPE_CHECKING:
    from .models import Notice
    from .pipeline import Candidate

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {"hwp", "hwpx", "pdf", "zip"}

# ZIP 안전장치 — 발주기관이 올린 압축 파일이라도 압축 폭탄·거대 파일로 수집이 멈추면 안 된다.
ZIP_MAX_MEMBERS = 60  # 압축 안에서 읽을 최대 문서 수 (HWP/HWPX/PDF/zip — 도면·사진 등은 세지 않는다)
_ZIP_PRIORITY_RE = re.compile(r"공고|제안\s*요청|과업\s*지시|과업\s*내용|규격|시방|입찰\s*안내|참가\s*자격")
ZIP_MAX_MEMBER_BYTES = 80 * 1024 * 1024  # 파일 하나 최대(풀었을 때)
ZIP_MAX_TOTAL_BYTES = 300 * 1024 * 1024  # 압축 하나에서 푸는 총량
ZIP_MAX_DEPTH = 1  # zip 안의 zip은 한 겹까지만


class AttachmentError(Exception):
    """다운로드/파싱 단계 실패. 호출측(fetch_attachment_text)이 잡아서 계속 진행한다."""


class AttachmentUnsupported(AttachmentError):
    """애초에 읽을 대상이 아닌 형식(xlsx 내역서 등, 또는 그런 파일만 든 zip).

    실패가 아니라 건너뜀이다 — 실패 건수·경고 로그에 넣지 않는다(2026-09-30 사용자 요청:
    "내역서 같은 불필요한 첨부파일 파싱 실패는 굳이 카운팅하지 않아도 돼").
    """


@dataclass
class AttachmentText:
    seq: str
    file_name: str
    url: str
    ext: str
    text: str = ""
    error: str | None = None
    skipped: bool = False  # 읽을 대상이 아닌 형식 — 실패로 세지 않는다

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def char_count(self) -> int:
        return len(self.text)


def download_bytes(session: requests.Session, url: str, timeout: float = 30.0) -> bytes:
    try:
        res = session.get(url, timeout=timeout)
    except requests.RequestException as err:
        raise AttachmentError(f"다운로드 실패: {err}") from err
    if res.status_code >= 400:
        raise AttachmentError(f"다운로드 실패: HTTP {res.status_code}")
    if not res.content:
        raise AttachmentError("빈 파일을 받았습니다")
    return res.content


# ── 개인정보(발주기관 담당자) 마스킹 ────────────────────────────────
#
# API 응답의 담당자 이름·전화·이메일은 fields.PERSONAL_FIELDS가 저장 전에
# 제거한다. 첨부파일 본문에도 문의처로 적힌 같은 종류의 정보가 자유 텍스트로
# 섞여 있어 같은 원칙으로 여기서도 지운다.
#
# 전화번호는 국번(02/010~019/031~069/070)과 구분자(-.공백)가 있는 패턴만
# 매칭한다 — 세부품명번호·업종코드처럼 구분자 없이 붙은 숫자열은 건드리지
# 않기 위해서다(실측 문서로 확인함). 이름은 일반 단어와 구분이 안 돼 오탐
# 위험이 크므로(예: "확인(전화번호)"에서 "확인"을 이름으로 착각) 마스킹
# 대상에서 뺀다 — 실제로 연락 가능하게 만드는 건 번호/이메일이지 이름 단독이
# 아니므로 이 선으로도 충분하다고 본다.
_KR_PHONE_RE = re.compile(r"0(?:2|1[016789]|[3-6][1-4]|70)[-.\s]\d{3,4}[-.\s]\d{4}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def redact_personal_contacts(text: str) -> str:
    text = _KR_PHONE_RE.sub("(연락처 비공개)", text)
    text = _EMAIL_RE.sub("(이메일 비공개)", text)
    return text


# 파일명 확장자와 실제 내용이 다른 첨부파일이 실측됨(예: .hwpx로 등록됐지만
# 실제로는 구버전 OLE2 .hwp 바이너리 — "File is not a zip file"로 실패).
# hwp/hwpx/pdf 세 형식은 매직 바이트가 서로 겹치지 않아 내용으로 구분할 수
# 있다. 확장자 확인은 어차피 먼저 끝난 뒤라(SUPPORTED_EXTENSIONS), 여기서
# 다른 형식으로 재분류해도 지원 안 하는 형식을 잘못 통과시킬 위험은 없다.
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC_PREFIXES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_PDF_MAGIC = b"%PDF"


def _is_hwpx_zip(data: bytes) -> bool:
    """zip 형식 중 HWPX(한글 문서)인지 — 그냥 압축 파일(입찰서류.zip 등)과 구분한다."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            if any(_HWPX_SECTION_RE.match(n) for n in names):
                return True
            if "mimetype" in names:
                return b"hwp" in zf.read("mimetype")[:64].lower()
    except (zipfile.BadZipFile, KeyError, OSError):
        return False
    return False


def _sniff_ext(data: bytes) -> str | None:
    if data.startswith(_PDF_MAGIC):
        return "pdf"
    if data.startswith(_ZIP_MAGIC_PREFIXES):
        return "hwpx" if _is_hwpx_zip(data) else "zip"
    if data.startswith(_OLE_MAGIC):
        return "hwp"
    return None


# 문서 보안(DRM)으로 잠긴 파일 — 맨 앞이 PDF·HWP 표시가 아니라 보안 프로그램 표시로 시작한다(2026-10-14 실측: R26BK01757744
# "7. 설계도서.zip" 안 도면 PDF 2개가 b"\x9b DRM…"). 보안 프로그램 없이는 못 읽어서 실패가 아니라 건너뜀으로 센다.
_DRM_HEAD_RE = re.compile(rb"DRM|SCDSA|<## FSD|DOCUMENT SAFER", re.I)


def _is_drm(data: bytes) -> bool:
    head = data[:64]
    return not head.startswith((_PDF_MAGIC, _OLE_MAGIC) + tuple(_ZIP_MAGIC_PREFIXES)) and bool(_DRM_HEAD_RE.search(head))


def extract_text(data: bytes, ext: str) -> str:
    return redact_personal_contacts(_extract_raw(data, ext, depth=0))


def _extract_raw(data: bytes, ext: str, depth: int) -> str:
    ext = ext.lower().lstrip(".")
    if _is_drm(data):
        log.info("문서 보안(DRM)으로 잠긴 파일이라 건너뜁니다")
        raise AttachmentUnsupported("문서 보안(DRM)으로 잠긴 파일")
    sniffed = _sniff_ext(data)
    if sniffed and sniffed != ext and sniffed in SUPPORTED_EXTENSIONS:
        if ext:
            log.info("확장자(.%s)와 실제 파일 내용(.%s)이 달라 실제 내용 기준으로 처리합니다", ext, sniffed)
        else:
            log.info("파일 이름에 확장자가 없어 내용으로 형식을 판별했습니다: .%s", sniffed)
        ext = sniffed

    if ext == "pdf":
        return _extract_pdf_text(data)
    if ext == "hwpx":
        return _extract_hwpx_text(data)
    if ext == "hwp":
        return _extract_hwp_text(data)
    if ext == "zip":
        if depth >= ZIP_MAX_DEPTH + 1:
            raise AttachmentError("압축 파일 안의 압축 파일이 너무 깊습니다")
        return _extract_zip_text(data, depth)
    raise AttachmentUnsupported(f"지원하지 않는 형식입니다: .{ext or '(확장자 없음)'}")


def _zip_member_name(info: zipfile.ZipInfo) -> str:
    """한국 관공서 zip은 파일명을 CP949로 넣고 UTF-8 표시(플래그 0x800)를 안 켠 경우가 많다 —
    그러면 zipfile이 CP437로 읽어 이름이 깨진다. 되돌려서 CP949로 다시 읽는다."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        return name.encode("cp437").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _extract_zip_text(data: bytes, depth: int = 0) -> str:
    """압축 파일 안의 HWP/HWPX/PDF(와 한 겹 안쪽 zip)를 모두 읽어 파일별로 이어 붙인다.

    파일 하나를 못 읽어도 나머지는 계속 읽는다. 하나도 못 읽으면 AttachmentError.
    이어 붙인 결과가 곧 이 첨부파일의 원문이 되므로 참가자격 절 찾기·자격판정·
    마감 보충·싱크로율이 다른 첨부와 똑같이 적용된다.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as err:
        raise AttachmentError(f"ZIP 파싱 실패: {err}") from err

    parts: list[str] = []
    skipped: list[str] = []  # 읽으려다 못 읽은 파일 (실패)
    unsupported: list[str] = []  # 애초에 읽을 대상이 아닌 형식 (xlsx 등)
    total = 0
    with zf:
        # 파일 수 제한은 **읽을 문서**(HWP/HWPX/PDF/zip, 확장자 없는 파일)에만 건다. 예전엔 전체 파일 앞 60개만 봐서,
        # 도면·사진 수백 개가 든 압축(2026-10-01 실측: R26BD00270194, 파일 511개)에선 문서를 하나도 못 만났다.
        # 공고문·제안요청서·과업지시서·규격서 이름을 먼저 읽는다.
        docs = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = _zip_member_name(info)
            base = name.rsplit("/", 1)[-1]
            if not base or base.startswith(("._", "~$")) or "__MACOSX" in name:
                continue
            ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
            if ext and ext not in SUPPORTED_EXTENSIONS:
                unsupported.append(f"{base}(.{ext})")
                continue
            docs.append((info, name, base, ext))
        docs.sort(key=lambda d: not _ZIP_PRIORITY_RE.search(d[2]))  # 안정 정렬 — 같은 순위끼리는 압축 안 순서 그대로
        for info, name, base, ext in docs[:ZIP_MAX_MEMBERS]:
            if info.file_size > ZIP_MAX_MEMBER_BYTES or total + info.file_size > ZIP_MAX_TOTAL_BYTES:
                skipped.append(f"{base}(너무 큼)")
                continue
            try:
                inner = zf.read(info)
                total += len(inner)
                text = _extract_raw(inner, ext, depth + 1)
            except AttachmentUnsupported:
                unsupported.append(base)
                continue
            except AttachmentError as err:
                skipped.append(f"{base}({err})")
                continue
            except Exception as err:  # 파일 하나 때문에 압축 전체를 버리지 않는다
                skipped.append(f"{base}(예상 못한 오류: {err})")
                continue
            if text.strip():
                parts.append(f"=== [압축 안] {name} ===\n{text}")
        if len(docs) > ZIP_MAX_MEMBERS:
            skipped.append(f"그 밖 문서 {len(docs) - ZIP_MAX_MEMBERS}개(파일 수 제한)")

    if skipped:
        log.info("압축 파일에서 읽지 않은 파일: %s", ", ".join(skipped[:10]) + (" 외" if len(skipped) > 10 else ""))
    if unsupported:
        log.debug("압축 파일 안의 읽을 대상이 아닌 파일: %s", ", ".join(unsupported[:10]))
    if not parts and not skipped:
        raise AttachmentUnsupported("압축 파일 안에 읽을 대상 문서가 없습니다(내역서·도면·DRM 보안 문서 등)")
    if not parts:
        raise AttachmentError("압축 파일 안에 읽을 수 있는 문서(HWP/HWPX/PDF)가 없습니다"
                              + (f" — {', '.join(skipped[:5])}" if skipped else ""))
    return "\n\n".join(parts)


def fetch_attachment_text(
    session: requests.Session, attachment: dict[str, str], timeout: float = 30.0
) -> AttachmentText:
    """첨부파일 1건을 내려받아 텍스트를 뽑는다. 실패해도 예외를 올리지 않는다."""
    seq = attachment.get("seq", "")
    file_name = attachment.get("file_name", "")
    url = attachment.get("url", "")
    ext = (attachment.get("ext") or "").lower()

    result = AttachmentText(seq=seq, file_name=file_name, url=url, ext=ext)

    if not url:
        result.error = "다운로드 URL이 없습니다"
        return result
    # 확장자가 아예 없으면(사전규격 문서 URL은 파일명 필드가 없다) 받아서 내용으로 판별한다
    # (`extract_text`의 `_sniff_ext`). 확장자가 있는데 지원 형식이 아니면 받지 않는다.
    if ext and ext not in SUPPORTED_EXTENSIONS:
        result.error = f"지원하지 않는 형식입니다: .{ext or '(확장자 없음)'}"
        result.skipped = True
        return result

    try:
        data = download_bytes(session, url, timeout=timeout)
        result.text = extract_text(data, ext)
    except AttachmentUnsupported as err:
        result.error = str(err)
        result.skipped = True
    except AttachmentError as err:
        result.error = str(err)
    except Exception as err:  # 예상 못한 오류도 파이프라인을 죽이면 안 된다
        log.exception("첨부파일 처리 중 예상 못한 오류: %s (%s)", file_name, url)
        result.error = f"예상 못한 오류: {err}"

    return result


def collect_notice_attachment_texts(
    session: requests.Session, notice: "Notice", timeout: float = 30.0
) -> list[AttachmentText]:
    return [fetch_attachment_text(session, att, timeout=timeout) for att in notice.attachments]


_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def _safe_filename(name: str) -> str:
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", name).strip()
    return cleaned or "attachment"


def save_attachment_texts(
    candidates: list["Candidate"],
    output_dir: Path,
    timeout: float = 30.0,
    session: requests.Session | None = None,
    held_codes: set[str] | None = None,
    held_code_names: dict[str, str] | None = None,
    now: datetime | None = None,
    code_names: dict[str, str] | None = None,
    held_raw: dict | None = None,
) -> dict[str, Any]:
    """후보 공고의 첨부파일을 내려받아 텍스트를 `output_dir/attachment_text/`에 저장한다.

    지역제한/면허제한/공동수급을 원문과 대조해볼 수 있도록 평문만 남기는
    용도다. 첨부파일 하나가 실패해도 나머지 처리는 계속한다.

    텍스트 안에서 "입찰 참가자격" 절을 찾으면(`qualification_text` 참고)
    `<...>_참가자격.txt`로 항목별 요약도 같이 남긴다 — API의 면허제한정보가
    비어 있는 공고(발주기관이 구조화 등록을 안 한 경우, 실측: 부안청자박물관
    R26BK01719858)를 사람이 원문 전체를 뒤지지 않고 바로 확인하기 위함이다.
    절을 못 찾아도 실패로 세지 않는다 — 애초에 없는 문서가 대부분이다.

    `held_codes`를 주면, 찾은 참가자격 항목으로 `qualify.evaluate_attachment_text`를
    돌려 그 결과를 **API 판정 위에 겹쳐서**(`qualify.merge_results`)
    `candidate.qualification`에 반영한다 — 리포트에는 다른 공고와 동일하게
    "자격 충족" / "자격 미달(이름)"로 표시된다. API가 이미 자격정보를 준 공고도
    건너뛰지 않는다: API 면허제한정보에는 세부품명번호(직접생산확인 품목) 필드가
    없어서, API만 보면 품목 요건이 통째로 빠지기 때문이다(`merge_results` 참고).
    판정 근거를 찾지 못하면(코드가 명시된 항목이 없음) 손대지 않는다. 이 판정은
    표시용일 뿐 후보 목록 자체는 바꾸지 않는다 — 후보/제외는 이미 API 기반 1차
    판정에서 끝난 뒤이기 때문이다.

    마감일정도 같은 방식으로 보충한다 — API의 마감 관련 세 필드가 전부 비어
    `candidate.schedule.earliest`가 None인("일정 미상") 공고에 한해, 첨부파일
    원문에서 제출기한을 찾아(`schedule_text.extract_deadline`)
    `candidate.schedule.attachment_deadline`을 채운다.

    첨부문서가 미보유 자격을 코드만 적은 경우 이름을 붙이려고 공고마다 이름
    정보를 모아 넘긴다: `code_names`(코드 이름 사전) + 앞서 처리한 다른 공고에서
    "이름(코드)"로 나온 코드 + 이 공고의 대표 세부품명 + 이 공고의 면허제한정보
    "업종명/코드". 그래도 못 찾은 코드는 `stats["unnamed_codes"]`로 돌려준다.
    """
    from .qualification_text import find_qualification_section
    from .qualify import api_code_names, evaluate_attachment_text, merge_results, named_codes
    from .schedule_text import extract_deadlines

    text_dir = output_dir / "attachment_text"
    text_dir.mkdir(parents=True, exist_ok=True)
    session = session or requests.Session()
    now = now or datetime.now()

    stats = {
        "attempted": 0,
        "ok": 0,
        "failed": 0,
        "qualification_found": 0,
        "qualification_determined": 0,
        "deadline_determined": 0,
        "unnamed_codes": [],
    }
    # 이번 실행에서 다른 공고 문서가 "이름(코드)"로 적어준 코드 — 뒤에 오는 공고가
    # 같은 코드를 이름 없이 적었을 때 쓴다.
    learned_names: dict[str, str] = {}
    for candidate in candidates:
        notice = candidate.notice
        qualification = getattr(candidate, "qualification", None)
        needs_check = held_codes is not None and qualification is not None
        schedule = getattr(candidate, "schedule", None)
        # 마감 정보가 없거나, 공동수급협정 마감만 있는 공고는 첨부의 등록·제출 마감도 찾는다
        needs_deadline = schedule is not None and getattr(schedule, "needs_attachment_deadline", schedule.earliest is None)
        all_items: list[str] = []
        read_files: list[tuple[str, str]] = []  # (파일명, 원문) — 칩 원문의 출처 파일을 찾을 때 쓴다
        deadline = None

        for result in collect_notice_attachment_texts(session, notice, timeout=timeout):
            if result.skipped:  # xlsx 내역서 등 — 읽을 대상이 아니라 시도·실패로 세지 않는다
                log.debug("첨부파일 건너뜀 [%s] %s: %s", notice.notice_no, result.file_name, result.error)
                continue
            stats["attempted"] += 1
            if not result.ok:
                log.warning("첨부파일 추출 실패 [%s] %s: %s", notice.notice_no, result.file_name, result.error)
                stats["failed"] += 1
                continue
            stats["ok"] += 1
            base = _safe_filename(f"{notice.notice_no}_{notice.notice_ord}_{result.seq}_{result.file_name}")
            (text_dir / f"{base}.txt").write_text(result.text, encoding="utf-8")
            read_files.append((result.file_name, result.text))
            if hasattr(candidate, "attachment_text"):
                candidate.attachment_text += f"\n\n=== {result.file_name} ===\n{result.text}"

            section = find_qualification_section(result.text)
            if section is not None:
                stats["qualification_found"] += 1
                summary = section.heading + "\n\n" + "\n\n".join(section.items)
                (text_dir / f"{base}_참가자격.txt").write_text(summary, encoding="utf-8")
                all_items.extend(section.items)

            # 첨부의 제출기한·참가등록 마감은 늘 모아 둔다(팝업에 보임). 카드 마감으로 쓰는 건 나라장터 자격등록·입찰 마감이
            # 빈 공고뿐 — 그때는 가장 이른 것(거제 지심도: 공고문엔 등록 10/13, 지침서엔 제출 11/17)
            for found in extract_deadlines(result.text):
                if schedule is not None and found not in schedule.attachment_deadlines:
                    schedule.attachment_deadlines.append(found)
                if needs_deadline and (deadline is None or found[1] < deadline[1]):
                    deadline = found

        if needs_deadline and deadline is not None:
            schedule.attachment_deadline_kind, schedule.attachment_deadline = deadline
            stats["deadline_determined"] += 1
            # candidate.days_left는 후보 산출 시점에 schedule.days_left(now)로
            # 미리 계산돼 있다 — attachment_deadline을 지금 막 채웠으니 여기서도
            # 다시 계산해야 "잔여일수"(D-N)가 새로 채운 마감/일정과 어긋나지 않는다.
            if hasattr(candidate, "days_left"):
                candidate.days_left = schedule.days_left(now)

        # 첨부 참가자격에 "주된 영업소 소재지가 ○○도" 같은 지역 요건이 있으면 API보다 우선한다
        if all_items and held_codes is not None and hasattr(candidate, "region_check"):
            from . import region as _region

            company = _region.company_sido(held_raw or {})
            candidate.region_check = _region.combine(
                candidate.region_check, _region.from_text(all_items, company)
            )

        # 실적·기술인력·건축사사무소 요건 — 판정 없이 "확인 필요" 칩으로 (2026-09-30 사용자 요청)
        if hasattr(candidate, "text_flags"):
            from .text_requirements import attach_sources, flag_requirements

            candidate.text_flags = attach_sources(
                flag_requirements(all_items), read_files
            )

        if not needs_check or not all_items:
            continue

        lookup = dict(code_names or {})
        lookup.update(learned_names)
        product_no = getattr(notice, "product_class_no", None)
        product_name = getattr(notice, "product_class_name", None)
        if product_no and product_name:
            lookup[product_no] = product_name
        lookup.update(
            api_code_names(
                list(getattr(qualification, "missing_groups", []))
                + list(getattr(qualification, "satisfied_groups", []))
            )
        )

        result = evaluate_attachment_text(all_items, held_codes, held_code_names, lookup)
        for code, label in named_codes(all_items).items():
            learned_names.setdefault(code, label.rsplit("(", 1)[0])
        for code in result.unnamed_codes:
            if code not in stats["unnamed_codes"]:
                stats["unnamed_codes"].append(code)
        if result.checked:
            candidate.qualification = merge_results(qualification, result)
            stats["qualification_determined"] += 1
            if result.missing_groups:
                # 실측 문서마다 "업종코드"/"세부품명번호" 표기가 조금씩 달라 오탐
                # 가능성이 있다(예: "세부품명번호 10자리, ####" 필러) — 미달로
                # 판정된 건은 원문을 눈으로 대조할 수 있게 -v로만 남긴다.
                log.debug("첨부파일 재판정 원문 [%s]: %s", notice.notice_no, all_items)

    # days_left가 위에서 갱신된 후보가 있을 수 있다("일정 미상" → 첨부파일로 보충) —
    # build_candidates가 정렬해둔 순서(days_left 기준)가 그 사이 낡아지므로 다시 정렬한다.
    # 그러지 않으면 이미 마감된 공고가 build_candidates 시점의 "일정 미상"(정렬 시
    # 최후순위 취급) 자리에 그대로 남아 목록 맨 뒤에 밀려 있게 된다.
    if candidates and hasattr(candidates[0], "sort_key"):
        candidates.sort(key=lambda c: c.sort_key)
    return stats


# ── PDF ──────────────────────────────────────────────────────────

def _extract_pdf_text(data: bytes) -> str:
    """페이지별로 추출한다. 그 전에 알려진 pypdf 결함을 먼저 패치해서

    실제로 텍스트가 뽑히게 만든다 (`_patch_missing_descendant_fonts` 참고).
    그래도 남는 예외는(다른 원인일 수 있으니) 조용히 건너뛰지 않고, 그
    페이지 자리에 "추출 실패, 원본 확인 필요" 표시를 남긴다 — 지역제한 같은
    중요 정보가 하필 그 페이지에 있었을 수 있으니 사람이 놓치면 안 된다.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
    except PdfReadError as err:
        raise AttachmentError(f"PDF 파싱 실패: {err}") from err

    _patch_missing_descendant_fonts(reader)

    total = len(reader.pages)
    pages: list[str] = []
    failed = 0
    for i, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as err:  # pypdf가 던지는 예외 타입이 일정하지 않다 (KeyError 등)
            log.warning("PDF %d/%d페이지 텍스트 추출 실패: %s", i + 1, total, err)
            pages.append(f"[※ {i + 1}페이지 텍스트 추출 실패 — 이 페이지는 원본 파일에서 직접 확인해야 합니다]")
            failed += 1

    if failed and failed == total:
        raise AttachmentError(f"PDF 모든 페이지({total}개) 텍스트 추출 실패")

    return "\n\n".join(pages).strip()


def _patch_missing_descendant_fonts(reader) -> int:
    """Type0류 폰트인데 /DescendantFonts가 없으면 빈 배열을 채운다.

    실측 원인: pypdf(`_font.py Font.from_font_resource`)는 폰트가
    Type1/MMType1/TrueType/Type3가 아니면 무조건 합성(Type0/CID) 폰트로 보고
    `pdf_font_dict["/DescendantFonts"]`를 바로 인덱싱한다 — 이 키가 없는
    비정상 폰트(실측: 나라장터 첨부 PDF 일부)를 만나면 KeyError로 죽는다.

    하지만 텍스트를 실제 문자로 바꾸는 작업(인코딩/ToUnicode CMap 해석)은
    이 코드보다 **먼저** 끝나 있고 `/DescendantFonts`와 무관하다 — 이 값은
    글자 폭(레이아웃 계산)에만 쓰인다. 그래서 빈 배열을 채워 넣으면 폭
    정보만 기본값으로 빠지고 실제 텍스트는 정상 추출된다 (합성 PDF로
    직접 재현·검증함: 패치 전 KeyError, 패치 후 원문 그대로 추출).
    """
    from pypdf.generic import ArrayObject, NameObject

    patched = 0
    for page in reader.pages:
        resources = page.get("/Resources")
        if resources is None:
            continue
        fonts = resources.get_object().get("/Font")
        if fonts is None:
            continue
        for font_ref in fonts.get_object().values():
            font_dict = font_ref.get_object()
            if font_dict.get("/Subtype") in ("/Type1", "/MMType1", "/TrueType", "/Type3"):
                continue
            if "/DescendantFonts" not in font_dict:
                font_dict[NameObject("/DescendantFonts")] = ArrayObject()
                patched += 1
    return patched


# ── HWPX (zip + xml, 2020년 이후 신형식) ───────────────────────────

_HWPX_SECTION_RE = re.compile(r"^Contents/section\d+\.xml$")


def _extract_hwpx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            section_names = sorted(n for n in zf.namelist() if _HWPX_SECTION_RE.match(n))
            if not section_names:
                raise AttachmentError("HWPX 안에 Contents/section*.xml이 없습니다")
            sections = [zf.read(name) for name in section_names]
    except zipfile.BadZipFile as err:
        raise AttachmentError(f"HWPX(zip) 파싱 실패: {err}") from err

    parts = [_hwpx_section_text(section) for section in sections]
    return "\n\n".join(p for p in parts if p).strip()


def _hwpx_section_text(xml_bytes: bytes) -> str:
    """<hp:sec>의 최상위 <hp:p> 문단만 순회한다 (문단 사이는 줄바꿈으로 구분).

    표(<hp:tbl>)는 문단의 <hp:run> 안에 인라인으로 끼워져 있고, 그 표의 각 셀
    (<hp:tc>)도 내부에 자기 문단(<hp:p>)을 갖는다. `root.iter()`로 태그만 보고
    전부 훑으면 표 안 문단이 상위 문단의 텍스트에 한 번(구분자 없이 뭉쳐서),
    그리고 독립된 문단으로 또 한 번, 총 두 번 잡혀서 배점표 같은 표가
    깨지고 중복된다 — 그래서 최상위 문단만 돌고, 표를 만나면
    `_render_table`이 행/열 구조를 살려서 재귀적으로 처리한다.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as err:
        raise AttachmentError(f"HWPX 섹션 XML 파싱 실패: {err}") from err

    paragraphs = [_render_paragraph(p) for p in root if p.tag.endswith("}p")]
    return "\n".join(t for t in paragraphs if t)


def _render_paragraph(p: ET.Element) -> str:
    """문단 하나의 텍스트. 런 안에 표가 끼어 있으면 표도 이어서 렌더링한다."""
    parts = []
    for run in p:
        if not run.tag.endswith("}run"):
            continue
        for child in run:
            if child.tag.endswith("}t"):
                if child.text:
                    parts.append(child.text)
            elif child.tag.endswith("}tbl"):
                table_text = _render_table(child)
                if table_text:
                    parts.append("\n" + table_text)
    return "".join(parts)


def _render_table(tbl: ET.Element) -> str:
    """행은 줄바꿈, 같은 행의 셀은 ' | '로 구분한다 (평가기준 배점표 등)."""
    rows = []
    for tr in tbl:
        if not tr.tag.endswith("}tr"):
            continue
        cells = [_render_cell(tc) for tc in tr if tc.tag.endswith("}tc")]
        rows.append(" | ".join(cells))
    return "\n".join(rows)


def _render_cell(tc: ET.Element) -> str:
    paragraphs = []
    for sub_list in tc:
        if not sub_list.tag.endswith("}subList"):
            continue
        paragraphs.extend(_render_paragraph(p) for p in sub_list if p.tag.endswith("}p"))
    return " ".join(t for t in paragraphs if t)


# ── HWP (OLE 복합문서 + 구 바이너리 포맷) ────────────────────────────
#
# 구조: OLE 컴파운드 파일(FileHeader, BodyText/Section0, Section1, ...).
# 각 Section 스트림은 (attribute 압축 플래그가 서 있으면) raw deflate로
# 압축돼 있고, 그 안은 레코드 스트림이다. 레코드 헤더 4바이트 = tag_id(10bit)
# + level(10bit) + size(12bit, 0xFFF면 다음 4바이트가 실제 크기).
#
# 참고: 표/그림 같은 인라인 컨트롤 문자 뒤에 예약된 텍스트 슬롯을 정교하게
# 건너뛰지는 않는다 — 문단 본문은 정상 추출되지만 표/이미지가 많은 문서는
# 경계 부분에 약간의 잡음이 섞일 수 있다. 실제 공고 첨부파일로 검증 필요.
#
# 2026-09-29 수정: 위 "잡음"이 실측으로 확인됨 — 목차 줄마다 "사업 개요葘ȃ 1",
# "2. 입찰참가자격 礮ȃ 2"처럼 원문에 없는 한자가 섞였다(울산박물관 R26BK01748232).
# HWP 5.0 규격상 컨트롤 문자 중 인라인(4~9, 19, 20)·확장(1~3, 11, 12, 14~18, 21~23)
# 형은 [코드 1워드][부가 데이터 6워드][같은 코드 1워드] 총 8워드를 차지하는데, 예전엔
# 코드 1워드만 지워서 부가 데이터 6워드가 UTF-16으로 읽혀 한자처럼 보였다. 이제 8워드
# 블록을 통째로 건너뛴다(닫는 코드가 같을 때만 — 아니면 1워드만 지워 본문을 삼키지 않게).
# 탭(9)은 목차의 점선 채움 자리라 공백 하나로 남긴다.
# 글자겹치기(0x17, "spct")는 "①" 같은 원문자를 그리는 기능이라 버리지 않고, 별도
# CTRL_HEADER 레코드(tag 0x47)에 든 실제 글자로 채운다 (jiil-past-contracts 과거 실적
# 추출에서 먼저 검증한 방식, 브랜치 claude/modest-faraday-gw058m).

_HWPTAG_PARA_TEXT = 0x43
_HWPTAG_CTRL_HEADER = 0x47
_HWP_BLOCK_CONTROL_CODES = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
_HWP_BLOCK_LEN = 8  # 여는 코드 + 부가 데이터 6워드 + 닫는 코드
_HWP_TAB = 0x09
_CHAR_OVERLAP_ANCHOR_CODE = 0x17
_CHAR_OVERLAP_CTRL_ID = b"spct"


def _extract_hwp_text(data: bytes) -> str:
    try:
        import olefile
    except ImportError as err:  # pragma: no cover
        raise AttachmentError("olefile 패키지가 필요합니다 (pip install olefile)") from err

    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except OSError as err:
        raise AttachmentError(f"HWP(OLE) 파싱 실패: {err}") from err

    try:
        compressed = _hwp_is_compressed(ole)
        section_paths = _hwp_body_section_paths(ole)
        if not section_paths:
            raise AttachmentError("HWP 안에 BodyText 섹션이 없습니다")

        paragraphs: list[str] = []
        for path in section_paths:
            raw = ole.openstream(path).read()
            payload = _inflate_raw(raw) if compressed else raw
            paragraphs.extend(_hwp_section_paragraphs(payload))
        return "\n".join(paragraphs).strip()
    finally:
        ole.close()


def _hwp_is_compressed(ole) -> bool:
    if not ole.exists("FileHeader"):
        return True  # 정보가 없으면 실측상 대부분인 압축 문서로 가정한다
    header = ole.openstream("FileHeader").read()
    if len(header) < 40:
        return True
    flags = int.from_bytes(header[36:40], "little")
    return bool(flags & 0x1)


def _hwp_body_section_paths(ole) -> list[str]:
    sections = [
        entry
        for entry in ole.listdir(streams=True)
        if len(entry) == 2 and entry[0] == "BodyText" and entry[1].startswith("Section")
    ]

    def _index(entry: list[str]) -> int:
        try:
            return int(entry[1].replace("Section", ""))
        except ValueError:
            return 0

    sections.sort(key=_index)
    return ["/".join(entry) for entry in sections]


def _inflate_raw(data: bytes) -> bytes:
    try:
        return zlib.decompress(data, -15)
    except zlib.error as err:
        raise AttachmentError(f"HWP 섹션 압축 해제 실패: {err}") from err


def _iter_hwp_records(payload: bytes):
    """섹션 페이로드를 (tag_id, record_bytes) 레코드 스트림으로 순회한다."""
    offset = 0
    length = len(payload)
    while offset + 4 <= length:
        header = int.from_bytes(payload[offset : offset + 4], "little")
        tag_id = header & 0x3FF
        size = (header >> 20) & 0xFFF
        offset += 4
        if size == 0xFFF:
            if offset + 4 > length:
                break
            size = int.from_bytes(payload[offset : offset + 4], "little")
            offset += 4
        record = payload[offset : offset + size]
        offset += size
        yield tag_id, record


def _extract_char_overlap_text(record: bytes) -> str:
    """"spct"(글자겹치기) 컨트롤 레코드에서 겹쳐진 문자를 꺼낸다.
    구조: b"spct" + 문자 길이(uint16) + 그 길이만큼의 UTF-16LE 문자 + 서식 파라미터."""
    if len(record) < 6 or record[:4] != _CHAR_OVERLAP_CTRL_ID:
        return ""
    char_len = int.from_bytes(record[4:6], "little")
    end = 6 + char_len * 2
    if char_len <= 0 or end > len(record):
        return ""
    return record[6:end].decode("utf-16le", errors="ignore")


def _hwp_section_paragraphs(payload: bytes) -> list[str]:
    records = list(_iter_hwp_records(payload))
    # 글자겹치기 컨트롤을 문서 순서대로 먼저 모아, 문단 디코딩 중 0x17 블록을 만날 때마다 하나씩 쓴다
    overlap_queue = deque(
        _extract_char_overlap_text(record)
        for tag_id, record in records
        if tag_id == _HWPTAG_CTRL_HEADER and record[:4] == _CHAR_OVERLAP_CTRL_ID
    )
    paragraphs = []
    for tag_id, record in records:
        if tag_id == _HWPTAG_PARA_TEXT and record:
            text = _decode_para_text(record, overlap_queue)
            if text:
                paragraphs.append(text)
    return paragraphs


def _decode_para_text(record: bytes, overlap_queue: "deque[str] | None" = None) -> str:
    """문단 레코드를 텍스트로. 개행(0x0A)은 살리고, 8워드 컨트롤 블록은 부가 데이터까지
    통째로 건너뛰며(탭은 공백 하나), 그 밖 컨트롤 문자(<0x20)는 1워드만 지운다."""
    chars = record.decode("utf-16le", errors="ignore")
    n = len(chars)
    out: list[str] = []
    i = 0
    while i < n:
        ch = chars[i]
        code = ord(ch)
        if code == 0x0A:
            out.append("\n")
            i += 1
            continue
        if code < 0x20:
            block_end = i + _HWP_BLOCK_LEN - 1
            if code in _HWP_BLOCK_CONTROL_CODES and block_end < n and chars[block_end] == ch:
                if code == _HWP_TAB:
                    out.append(" ")
                elif code == _CHAR_OVERLAP_ANCHOR_CODE and overlap_queue:
                    out.append(overlap_queue.popleft())
                i = block_end + 1
            else:
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)
