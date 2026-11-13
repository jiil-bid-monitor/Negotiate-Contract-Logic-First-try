"""정량평가서 서식 읽기 건강 검사 — 공고 하나의 첨부로 '규칙이 모르는 모양' 때문에 조용히 빠지는 것을 모두 찾음(11/10 사용자 요청).

계기: 서식 목록·자기평가표·회사 정보 칸을 못 읽는 일이 공고마다 반복됨 — 규칙은 본 모양만 알고, 예전 검사는 '이름표 + 빈 칸'만 셌음.
여기서 세는 것(`check` → issues의 kind):
  서식 못 자름     — 공고가 서식 목록을 적었는데 한글 첨부에서 서식이 하나도 안 잘림
  서식 빠짐        — 목록에 있는 서식 번호가 잘린 서식에 없음(목록 번호 ↔ 본문 머리 모양이 다름)
  목록 못 읽음     — 한글 첨부에서 서식은 잘렸는데 목록은 비어 있음
  배점기준 못 읽음  — 자기평가표 서식이 있는데 제안요청서 본문 정량평가 배점기준을 못 읽음
  평가표 못 읽음   — 자기평가표 서식인데 '배점'·'평점' 표를 못 읽음(점수를 넣을 칸을 모름)
  기준 없음        — 자동 채점 항목(기술인력·신용·신인도·특허)인데 본문에 그 항목 기준이 없음
  빈 칸            — 회사 정보 이름표인데 채운 뒤에도 빈 칸(`fill.unfilled_slots`)
  모르는 이름표    — 회사 정보로 보이는 낱말이 든 이름표 옆이 빈 칸인데 규칙이 그 이름표를 모름
회사 값은 가짜(FAKE)로 채움 — 결과엔 회사 정보가 없음. 과거 제안요청서 검사(scripts/quant_fill_check.py)가 씀
(새 공고 미리 검사·개발자 방 알림은 11/11 사용자 결정으로 삭제).
"""

from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

FAKE = {"회사명": "(주)가상", "대표자": "홍길동", "주소": "강원특별자치도 정선군 가상로 1", "전화번호": "033-000-0000",
        "팩스번호": "033-000-0001", "사업자등록번호": "000-00-00000", "법인등록번호": "000000-0000000", "이메일": "a@example.com",
        "홈페이지": "www.example.com", "설립일": "2009-09-23", "대표자 주민등록번호": "800101-1******", "업태": "제조업",
        "종목": "전시물", "신용평가등급": "BBB0", "신용평가 종류": "기업신용평가등급", "신용평가 평가일": "2026-06-30",
        "부정당업자 제재처분": "해당없음"}

SELF_FORM = re.compile(r"자\s*[기가]\s*(?:평\s*가|점\s*검)|자\s*체\s*평\s*가|평\s*가\s*표|심\s*사\s*표|자\s*기\s*평\s*점")
# 회사 정보로 보이는 이름표 낱말 — 규칙(_label_key)이 모르는데 옆 칸이 비면 '모르는 이름표'
_HINT = re.compile(r"상호|업체|회사|법인|대표|성명|생년|주소|소재|전화|연락|팩스|FAX|사업자|공고|등급|평가일|유효|메일|홈페이지")
# 비워 두는 게 맞는 이름표(날짜·접수·담당자·휴대전화·서명 등)
_INTENDED_BLANK = re.compile(r"일자|년월일|날짜|접수|담당|휴대|핸드폰|서명|날인|직위|직급|부서|확인자|발급|용도|비고|번호$|제출처")

KINDS = ["서식 못 자름", "서식 빠짐", "목록 못 읽음", "배점기준 못 읽음", "평가표 못 읽음", "기준 없음", "빈 칸", "모르는 이름표"]


@dataclass
class Issue:
    kind: str
    detail: str
    form: str = ""

    def line(self) -> str:
        return f"{self.kind}: " + (f"[{self.form}] " if self.form else "") + self.detail


@dataclass
class Health:
    listed: list[int] = field(default_factory=list)  # 공고가 적은 서식 번호
    cut: list[str] = field(default_factory=list)  # 잘린 서식 번호('4-1' 포함)
    forms: list[tuple[str, str, int]] = field(default_factory=list)  # (번호, 이름, 채운 수)
    issues: list[Issue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # 변환 실패 등 검사 자체의 알림

    @property
    def ok(self) -> bool:
        return not self.issues

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.issues:
            out[i.kind] = out.get(i.kind, 0) + 1
        return out


def _is_hwpx(p: Path) -> bool:
    try:
        with zipfile.ZipFile(p) as z:
            return any(n.startswith("Contents/section") for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def _default_convert(src: Path, work: Path) -> Path | None:
    from .build import convert_hwp

    hx, _ = convert_hwp(src, work)
    return hx


def to_hwpx(f: Path, work: Path, convert=None) -> Path | None:
    """첨부 → HWPX(이름만 .hwp인 HWPX·이름만 .hwpx인 옛 한글 포함). 못 바꾸면 None."""
    f = Path(f)
    if f.suffix.lower() not in (".hwp", ".hwpx"):
        return None
    if _is_hwpx(f):
        if f.suffix.lower() == ".hwpx":
            return f
        dst = work / (f.stem + ".hwpx")
        shutil.copyfile(f, dst)
        return dst
    src = f
    if f.suffix.lower() == ".hwpx":
        src = work / (f.stem + "_원본.hwp")
        shutil.copyfile(f, src)
    try:
        return (convert or _default_convert)(src, work)
    except Exception:  # noqa: BLE001 — 깨진 문서·보안 문서
        return None


def _main_no(no: str) -> int | None:
    m = re.match(r"\d+", str(no))
    return int(m.group()) if m else None


def unknown_labels(xml: str) -> list[str]:
    """표 안 이름표 칸 중 회사 정보로 보이는 낱말이 있는데 규칙이 모르고(_label_key 없음) 오른쪽 칸이 빈 것."""
    from .fill import _OTHER_PARTY, _agent_rows, _cells, _label_key, _row_group, _spans, _sq

    out: list[str] = []
    for tbl in _spans(xml, "tbl"):
        cells = _cells(xml, tbl)
        if re.search(r"<(?:[\w.-]+:)?tbl\b", xml[tbl[0] + 4:tbl[1]]):
            cells = [c for c in cells if not re.search(r"<(?:[\w.-]+:)?tbl\b", xml[c.start:c.end])]
        by_pos = {(c.row, c.col): c for c in cells}
        other = {c.row for c in cells if c.col == 0 and _OTHER_PARTY.search(_sq(c.text))}
        agent = _agent_rows(xml, cells)
        for c in cells:
            t = _sq(c.text)
            if not t or len(t) > 16 or c.row in other or c.row in agent or re.search(r"\d", t):
                continue
            if not _HINT.search(t) or _INTENDED_BLANK.search(t) or _label_key(c.text) is not None:
                continue
            if _row_group(cells, c) == "대리인":
                continue
            right = by_pos.get((c.row, c.col + c.colspan))
            if right is None or not right.blank or _label_key(right.text) is not None:
                continue
            # 머리글 줄(같은 줄 칸이 모두 짧은 이름표이고 아래 줄이 값)은 표 머리 — 빈 칸 아님
            if c.row == 0 and all(not by.blank for (r, _), by in by_pos.items() if r == 0 and by is not right):
                continue
            out.append(t)
    return out


def check(files: list[Path], work: Path, convert=None, title: str = "가상 전시관 조성 사업",
          g2b_no: str = "R26BK00000000-000", ref: date | None = None) -> Health:
    """공고 첨부(files) 하나 묶음 검사. work: 임시 폴더(부르는 쪽이 지움). convert: .hwp → HWPX 함수(src, work) → Path|None."""
    from . import vault as vault_mod
    from .build import _BLANK_FORM, is_manual
    from .fill import fill_hwpx, unfilled_slots
    from .forms_cut import cut
    from .notice import load_catalog, read_notice
    from .selfscore import SelfScore, read_criteria

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    files = [Path(f) for f in files]
    h = Health()
    hwpx: list[Path] = []
    for f in files:
        if f.suffix.lower() not in (".hwp", ".hwpx"):
            continue
        hx = to_hwpx(f, work, convert)
        if hx is None:
            h.notes.append(f"{f.name}: HWPX로 바꾸지 못함")
        else:
            hwpx.append(hx)
    try:
        sheet = read_notice(files, load_catalog())
        h.listed = sorted({int(f.no) for f in sheet.forms if str(f.no).isdigit()})
    except Exception as err:  # noqa: BLE001
        h.notes.append(f"공고 읽기 실패: {str(err)[:80]}")
    hwpx.sort(key=lambda p: (0 if "제안요청" in p.name else 1 if "과업" in p.name else 2))
    try:
        criteria = read_criteria(hwpx)
    except Exception:  # noqa: BLE001
        criteria = []
    v = vault_mod.Vault({}, info=dict(FAKE))
    ctx = SelfScore(criteria, v, ref or date.today())
    seen: set[str] = set()
    self_forms = 0
    for i, src in enumerate(hwpx):
        out_dir = work / f"cut{i}"
        try:
            forms = cut(src, out_dir)
        except Exception as err:  # noqa: BLE001
            h.notes.append(f"{src.name}: 서식 자르기 실패({str(err)[:60]})")
            continue
        for f in forms:
            if f.no in seen:  # 같은 서식이 제안요청서·공고문에 반복되면 처음 것만
                continue
            seen.add(f.no)
            h.cut.append(f.no)
            if _BLANK_FORM.search(f.title):
                h.forms.append((f.no, f.title, 0))
                continue
            manual = is_manual(f.title)
            is_self = bool(SELF_FORM.search(f.title)) and not manual
            self_forms += is_self
            name = f"{f.no} {f.title}"[:30]
            dst = work / f"filled{i}_{f.file}"
            try:
                rep = fill_hwpx(out_dir / f.file, dst, v, title=title, g2b_no=g2b_no, basis=100_000_000,
                                sign_only=manual, self_scores=ctx if (is_self and criteria) else None)
                slots, unknown = [], []
                with zipfile.ZipFile(dst) as z:
                    for n in z.namelist():
                        if re.fullmatch(r"Contents/section\d+\.xml", n):
                            xml = z.read(n).decode("utf-8")
                            slots += unfilled_slots(xml, manual)
                            if not manual:
                                unknown += unknown_labels(xml)
            except Exception as err:  # noqa: BLE001
                h.notes.append(f"[{name}] 채우다 오류: {str(err)[:80]}")
                continue
            h.forms.append((f.no, f.title, len(rep.filled)))
            for s in dict.fromkeys(slots):
                h.issues.append(Issue("빈 칸", s, name))
            for s in dict.fromkeys(unknown):
                h.issues.append(Issue("모르는 이름표", s, name))
            if is_self and criteria:
                if not rep.self_rows:
                    h.issues.append(Issue("평가표 못 읽음", "'배점'·'평점' 칸이 있는 점수 표를 못 읽음", name))
                for row in rep.self_rows:
                    if "배점기준이 없어" in (row[4] or ""):
                        h.issues.append(Issue("기준 없음", f"'{row[0]}' — 본문 배점기준에 이 항목이 없음", name))
    if self_forms and not criteria:
        h.issues.append(Issue("배점기준 못 읽음", f"자기평가표 서식 {self_forms}개인데 제안요청서 정량평가 배점기준을 못 읽음"))
    cut_main = {_main_no(n) for n in h.cut}
    if hwpx:
        if h.listed and not h.cut:
            h.issues.append(Issue("서식 못 자름", f"목록 서식 {len(h.listed)}개인데 한글 첨부에서 잘린 서식 없음"))
        elif h.listed and h.cut:
            missing = [n for n in h.listed if n not in cut_main]
            if missing:
                h.issues.append(Issue("서식 빠짐", "목록에 있는데 잘리지 않은 서식 " + ", ".join(map(str, missing))))
        elif h.cut and not h.listed:
            h.issues.append(Issue("목록 못 읽음", f"서식 {len(h.cut)}개가 잘렸는데 공고의 서식 목록을 못 읽음"))
    return h


def summary(h: Health, limit: int = 8) -> str:
    """알림용 몇 줄."""
    lines = [i.line() for i in h.issues[:limit]]
    if len(h.issues) > limit:
        lines.append(f"… 외 {len(h.issues) - limit}건")
    return "\n".join(lines)
