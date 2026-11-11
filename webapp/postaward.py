"""낙찰 후 서류 — 1단계: 낙찰 여부 확인 목록.

투찰 기록(bid_log.json)에 있는 공고와 사람이 직접 넣은 공고를 지켜보다가, 나라장터 낙찰현황에 낙찰자가 뜨면
지일이면 '낙찰', 다른 업체면 '다른 업체 낙찰'로 바꾼다. 매일 22시 밤 작업(bidnightly)이 부르고, 화면에서 바로 확인할 수도 있다.
개찰 1순위는 적격심사에서 바뀔 수 있어 쓰지 않는다 — 낙찰현황(심사 끝난 뒤 낙찰자)만 본다.

실측(2026-11-10): 낙찰현황은 공사·용역·물품 오퍼레이션이 따로, 공고 하나는 inqryDiv=4 + bidNtceNo.
개찰부터 낙찰 확정까지 20일 걸린 공사가 있어 GIVE_UP_DAYS를 넉넉히 둔다.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from nego import fields as F

KST = timezone(timedelta(hours=9))
STATE_FILE = "postaward.json"
GIVE_UP_DAYS = 60  # 추가(개찰)한 뒤 이만큼 지나도 낙찰자가 없으면 '결과 없음'
WORKS = ("공사", "용역", "물품")
GROUPS = {"공사": "공사", "용역": "물품·용역", "물품": "물품·용역"}  # 화면·폴더 구분(사용자 결정 11/10)
_NO_RE = re.compile(r"^[A-Z0-9]{8,14}$")


def _now() -> datetime:
    return datetime.now(KST)


def _digits(text: Any) -> str:
    return re.sub(r"\D", "", str(text or ""))


def split_no(text: str) -> tuple[str, str]:
    """'R26BK01800345-000' → (번호, 차수). 차수가 없으면 ''."""
    raw = re.sub(r"\s", "", str(text or "")).upper()
    no, _, ord_ = raw.partition("-")
    if not _NO_RE.match(no):
        raise ValueError("공고번호 형식이 맞지 않습니다 (예: R26BK01800345-000)")
    return no, ord_[:3]


def group_of(work: str) -> str:
    return GROUPS.get(work or "", "업무 미확인")


# 표준틀이 만드는 서류(시험본 v4 탭 이름에서) — 틀이 정해지면 이 목록만 바꾼다. 서류 이름에 이 낱말이 있으면 '틀에 있음'
TEMPLATE_DOCS = ("공문", "착수계", "착공계", "조직도", "선임신고서", "보안각서", "재직증명서", "참여기술자")


def required_docs(files: list[Path]) -> list[dict]:
    """첨부가 요구하는 제출서류(정량평가서의 제출서류 읽기 quantdocs.notice.read_submit_items). 같은 이름은 한 번."""
    from quantdocs.notice import doc_text, read_submit_items

    out, seen = [], set()
    # 같은 문서가 한글·PDF로 함께 오면 한글 쪽을 먼저(PDF는 띄어쓰기가 흐트러짐)
    for f in sorted(files, key=lambda f: f.suffix.lower() == ".pdf"):
        if f.suffix.lower() not in (".hwp", ".hwpx", ".pdf", ".docx"):
            continue
        try:
            items = read_submit_items(doc_text(f), f.name, [])
        except Exception:  # noqa: BLE001 — 한 첨부를 못 읽어도 나머지로
            continue
        for it in items:
            name = re.sub(r"\s+", "", it.text)
            if not re.search(r"[가-힣A-Za-z]", name) or name[:20] in seen:  # 구분선 '-----', 한글·PDF 같은 줄
                continue
            seen.add(name[:20])
            out.append({"text": it.text, "source": f.name, "covered": any(w in name for w in TEMPLATE_DOCS)})
    return out


# 착공·착수 표준틀 v5(차장님 확인 11/11) — 회사 정보가 들어 있어 저장소 밖 NAS 공유 폴더 '낙찰후서류/_표준틀' 한 곳에 둠
NAS_TEMPLATES = Path(r"\\jiilserver\jiil-webapp\낙찰후서류\_표준틀")


def template_dir(root: Path) -> Path:
    """표준틀 폴더 — POSTAWARD_TEMPLATE_DIR > NAS 공유 폴더(서버 PC도 여기서 읽어 틀을 따로 복사하지 않음, 11/11) > root/_표준틀(NAS 컨테이너 안)."""
    env = os.environ.get("POSTAWARD_TEMPLATE_DIR", "").strip()
    for d in ([Path(env)] if env else []) + [NAS_TEMPLATES]:
        try:
            if d.is_dir():
                return d
        except OSError:  # 공유 폴더가 안 닿으면 다음 후보로
            pass
    return Path(root) / "_표준틀"


TEMPLATES = {"공사": ("표준틀_착공서류(공사).xlsx", "2_착공", "착공서류.xlsx"),
             "물품·용역": ("표준틀_착수서류(물품·용역).xlsx", "2_착수", "착수서류.xlsx")}
_SITE = re.compile(r"공\s*사\s*(?:위\s*치|장\s*소|현\s*장)\s*[:：]\s*([^\n]{4,60})")
_PERIOD = re.compile(r"(?:착\s*공|착\s*수|계\s*약)\s*일\s*(?:로\s*)?부\s*터\s*(\d{1,4})\s*일")
_LONG = re.compile(r"장\s*기\s*계\s*속\s*(?:계\s*약|공\s*사)")


def basic_values(v: dict, title: str, institution: str, contract_method: str, texts: list[str]) -> tuple[dict, list[str]]:
    """기본사항 행 → (값, 출처) — 표준틀 v5 칸 위치(4 업무·5 발주기관·8 건명·11 계약 형태·12 금액·17 준공예정일 메모·21 공사위치).
    계약번호·계약일·수신·문서번호·인력은 계약 뒤에 정해지거나 사람이 고르는 값이라 비움. 돌려주는 줄 = 서류_확인.txt 안내."""
    work, gong = v.get("work", ""), v.get("work") == "공사"
    joined = "\n".join(texts)
    out: dict[int, tuple[Any, str]] = {}
    if work in WORKS:
        out[4] = (work, "나라장터 업무")
    if institution:
        out[5] = (institution, "나라장터 수요기관")
    if title:
        out[8] = (title, "나라장터 공고명 — 계약서 건명과 대조")
    amount = _digits(str((v.get("winner") or {}).get("amount") or "").split(".")[0])
    if amount:
        out[12] = (int(amount), "나라장터 낙찰금액 — 계약금액과 대조")
    if not gong and ("장기" in contract_method or _LONG.search(joined)):
        out[11] = ("장기계속", "계약방법·공고문의 '장기계속' — 차수 금액 확인")
    site = _SITE.search(joined) if gong else None
    if site:
        out[21] = (site.group(1).strip(" ,."), "공고문 공사위치")
    notes = []
    period = _PERIOD.search(joined)
    if period:
        notes.append(f"준공예정일: 공고문에 '{re.sub(chr(10), ' ', period.group(0))}' — 계약일·착수일을 넣고 계산하세요")
    if not amount:
        notes.append("낙찰금액을 아직 모름 — 낙찰 확인 뒤 다시 만들거나 직접 넣으세요")
    for word, tab in (("안전관리자", "안전관리자 선임계"), ("품질관리자", "품질관리자 선임계")) if gong else (("현장대리인", "현장대리인 서류(해당시)"),):
        if re.search(r"\s*".join(word), joined):
            notes.append(f"공고문에 '{word}'가 있음 — {tab} 탭도 제출할지 확인")
    return out, notes


def fill_basic(template: Path, target: Path, values: dict) -> None:
    """표준틀을 복사해 기본사항 B열에 값, C열에 '[자동] 출처'. 채운 칸은 하늘색(사람이 넣는 노란 칸과 구분)."""
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill

    wb = load_workbook(template)
    ws = wb["기본사항"]
    blue = PatternFill("solid", fgColor="FFDDEBF7")
    for row, (value, source) in values.items():
        ws.cell(row, 2).value = value
        ws.cell(row, 2).fill = blue
        ws.cell(row, 3).value = f"[자동] {source}"
    target.parent.mkdir(parents=True, exist_ok=True)
    wb.save(target)


# 서류 현황(화면 '서류 현황' 칸) — 단계 폴더마다 무엇이 만들어졌는지. 표준틀은 착공·착수만, 나머지는 샘플을 받은 뒤(11/11)
STAGE_NAME = {"0_적격심사": "적격심사", "1_계약": "계약", "2_착공": "착공", "2_착수": "착수", "3_준공": "준공", "4_청구": "청구"}
READY_STAGES = {"2_착공", "2_착수"}
# 기본사항에서 담당자가 꼭 넣을 칸(행 번호 — 이름은 A열에서 읽어 틀 이름이 바뀌어도 맞음). 공사는 21행 공사위치도
REQUIRED_ROWS = (6, 8, 9, 10, 12, 15, 16, 17, 18, 19, 20)


def share_path(path: Path | str) -> str:
    """화면에 보일 폴더 주소 — NAS 컨테이너(/work = \\\\jiilserver\\jiil-webapp)면 윈도우 탐색기에 붙여 넣을 수 있는 주소로."""
    s = str(path)
    return "\\\\jiilserver\\jiil-webapp\\" + s[len("/work/"):].replace("/", "\\") if s.startswith("/work/") else s


def sheet_todo(xlsx: Path, gong: bool) -> tuple[list[str], list[list[str]]]:
    """착공·착수서류 기본사항에서 (아직 빈 필수 칸 이름, [자동으로 채운 칸 이름, 값]). 숨긴 줄은 안 봄."""
    from openpyxl import load_workbook

    ws = load_workbook(xlsx)["기본사항"]
    todo, auto = [], []
    for r in REQUIRED_ROWS + ((21,) if gong else ()):
        if ws.row_dimensions[r].hidden or not ws.cell(r, 1).value:
            continue
        if ws.cell(r, 2).value in (None, ""):
            todo.append(str(ws.cell(r, 1).value))
    # 인력 — 표준틀의 빨간 칸과 같은 규칙(11/11 사용자 결정 '선임 대상만 필수'): 1번(공사 현장대리인·착수 용역 책임자)의
    # 선임신고서·재직증명서에 들어가는 칸만. 나머지 줄은 쓰는 사람만 채우면 됨
    heads = {c: str(ws.cell(23, c).value or "") for c in range(2, 11)}
    label = str(ws.cell(24, 1).value or "1번")
    empty = [heads[c] or str(c) for c in (2, 3, 4, 6, 7, 8, 9, 10) if ws.cell(24, c).value in (None, "")]
    if empty:
        todo.append(f"{label}: " + "·".join(empty))
    for r in range(4, 22):
        if str(ws.cell(r, 3).value or "").startswith("[자동]") and ws.cell(r, 2).value not in (None, ""):
            value = ws.cell(r, 2).value
            auto.append([str(ws.cell(r, 1).value), f"{value:,}" if isinstance(value, int) else str(value)])
    return todo, auto


def lookup(client: Any, base_url: str, no: str, ord_: str, work: str) -> tuple[str, dict | None]:
    """낙찰현황에서 공고 하나의 낙찰자. (확인한 업무, 줄 또는 None). 업무를 모르면 공사·용역·물품 순으로."""
    for w in ([work] if work in WORKS else list(WORKS)):
        rows = client.fetch_all_pages(base_url, F.SCSBID_OPERATIONS[f"낙찰현황/{w}"],
                                      {"inqryDiv": "4", "bidNtceNo": no}, f"낙찰현황/{w}")
        rows = [r for r in rows if not ord_ or str(r.get("bidNtceOrd") or "") == ord_] or rows
        if rows:
            return w, max(rows, key=lambda r: str(r.get("bidNtceOrd") or ""))
    return work, None


class PostAward:
    def __init__(self, data_dir: Path, bidlog=None, own_bizno: str = "", now=_now):
        self.path = Path(data_dir) / STATE_FILE
        self.bidlog = bidlog
        self.own = _digits(own_bizno)
        self.now = now
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, items: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def items(self) -> list[dict]:
        """화면용 — 공사 / 물품·용역 / 업무 미확인 순, 그 안에선 최근 추가 먼저."""
        order = {"공사": 0, "물품·용역": 1}
        rows = [{**v, "group": group_of(v.get("work", ""))} for v in self._read().values()]
        return sorted(rows, key=lambda v: (order.get(v["group"], 2), v.get("added", "")), reverse=False)

    def _add(self, items: dict, no: str, ord_: str, **info) -> bool:
        key = f"{no}-{ord_}" if ord_ else no
        if key in items or (not ord_ and any(k.startswith(no + "-") for k in items)):
            return False
        items[key] = {"no": no, "ord": ord_, "status": "대기", "added": self.now().isoformat(timespec="seconds"), **info}
        return True

    def add(self, text: str, title: str = "", work: str = "") -> dict:
        """공고번호 직접 입력(협상·규격가격동시입찰도 여기로)."""
        no, ord_ = split_no(text)
        with self._lock:
            items = self._read()
            self._add(items, no, ord_, title=title[:200], work=work if work in WORKS else "", source="직접 입력")
            self._write(items)
        return items.get(f"{no}-{ord_}" if ord_ else no, {})

    def remove(self, key: str) -> None:
        with self._lock:
            items = self._read()
            items.pop(key, None)
            self._write(items)

    def sync_bidlog(self) -> int:
        """투찰 기록의 공고를 목록에 올림(이미 있으면 그대로). 올린 수."""
        if self.bidlog is None:
            return 0
        with self._lock:
            items = self._read()
            added = sum(self._add(items, e["noticeNo"], (e.get("noticeOrd") or "")[:3], title=e.get("title", ""),
                                  institution=e.get("institution", ""), work=e.get("workType", ""), source="투찰 기록")
                        for e in self.bidlog.entries() if e.get("noticeNo"))
            self._write(items)
        return added

    def make_folder(self, key: str, root: Path) -> Path:
        """공고 폴더 — root/공사|물품·용역/번호-차수_공고명/ 아래 단계별 폴더. 이미 있으면 빈 폴더만 채우고 파일은 안 건드림."""
        with self._lock:
            items = self._read()
            v = items.get(key)
            if v is None:
                raise ValueError("목록에 없는 공고입니다")
            name = re.sub(r'[\\/:*?"<>|\s]+', " ", f"{key}_{v.get('title') or ''}").strip()[:90].rstrip(" ._")
            folder = Path(root) / group_of(v.get("work", "")) / name
            stages = ["0_적격심사", "1_계약", "2_착공" if v.get("work") == "공사" else "2_착수", "3_준공", "4_청구", "공고문서"]
            for s in stages:
                (folder / s).mkdir(parents=True, exist_ok=True)
            v["folder"] = str(folder)
            self._write(items)
        return folder

    def prepare(self, key: str, root: Path, fetch=None, templates: Path | None = None) -> tuple[Path, list[dict]]:
        """공고 폴더 만들기 — 나라장터에서 공고 정보·첨부를 받아(정량평가서와 같은 quantdocs.g2b.fetch_notice) 업무를 정하고,
        첨부는 공고문서/에 두고, 첨부가 요구하는 제출서류를 읽어 표준틀에 없는 것을 '서류_확인.txt'에 적는다.
        표준틀(templates, 기본 root/_표준틀)이 있으면 착공·착수서류를 복사해 기본사항을 채운다(시험판 11/11)."""
        import shutil
        import tempfile

        if fetch is None:
            from quantdocs.g2b import fetch_notice as fetch
        v = {**(self._read().get(key) or {})}
        if not v:
            raise ValueError("목록에 없는 공고입니다")
        with tempfile.TemporaryDirectory(prefix="postaward_") as tmp:
            nf = fetch(key, Path(tmp))
            if nf.title:
                with self._lock:
                    items = self._read()
                    items[key].update(title=nf.title, institution=nf.institution or items[key].get("institution", ""),
                                      work=nf.work_type or items[key].get("work", ""), method=nf.award_method)
                    self._write(items)
            folder = self.make_folder(key, root)
            docs = required_docs(nf.files)
            for f in nf.files:
                target = folder / "공고문서" / f.name
                if not target.exists():  # 담당자가 고쳤을 수 있어 덮어쓰지 않음
                    shutil.copyfile(f, target)
            basic = self._fill_template(key, folder, nf, Path(templates) if templates else template_dir(root))
        lines = [f"{key} {v.get('title') or nf.title} — 첨부에서 읽은 제출서류 (자동, 원문과 대조하세요)", ""]
        lines += basic + [""]
        if not nf.title:
            lines.append("나라장터에서 공고를 찾지 못해 첨부를 받지 못했습니다.")
        elif not docs:
            lines.append("첨부에서 '제출서류' 목록을 찾지 못했습니다 — 공고문을 직접 확인하세요.")
        for d in docs:
            lines.append(f"[{'틀에 있음' if d['covered'] else '틀에 없음 — 따로 준비'}] {d['text']}  ({d['source']})")
        lines += [""] + [f"첨부를 받지 못함: {s}" for s in nf.skipped]
        (folder / "공고문서" / "서류_확인.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self._lock:
            items = self._read()
            items[key]["docs"] = {"found": len(docs), "missing": sum(not d["covered"] for d in docs), "files": len(nf.files)}
            # 서류 현황 화면용 — 첨부 제출서류(틀에 있음/없음)와 기본사항 안내(공고문 기간·해당 시 서류)
            items[key]["docList"] = [{"text": d["text"][:120], "covered": d["covered"]} for d in docs][:40]
            items[key]["notes"] = [n.strip() for n in basic if n.startswith("  ") and "직접 넣으세요" not in n]
            self._write(items)
        return folder, docs

    def status(self, key: str) -> dict:
        """서류 현황 — 단계 폴더마다 파일, 착공·착수서류의 빈 필수 칸·자동 칸, 첨부 제출서류, 확인할 것."""
        v = self._read().get(key) or {}
        folder = Path(v["folder"]) if v.get("folder") else None
        if folder is None or not folder.is_dir():
            return {"folder": None}
        gong = v.get("work") == "공사"
        stages = []
        for d in sorted(p for p in folder.iterdir() if p.is_dir() and p.name in STAGE_NAME):
            files = sorted(f.name for f in d.iterdir() if f.is_file() and not f.name.startswith("~$"))
            stage = {"dir": d.name, "name": STAGE_NAME[d.name], "files": files, "ready": d.name in READY_STAGES}
            doc = d / TEMPLATES["공사" if gong else "물품·용역"][2]
            if d.name in READY_STAGES and doc.exists():
                try:
                    stage["todo"], stage["auto"] = sheet_todo(doc, gong)
                    stage["doc"] = doc.name
                except Exception as err:  # noqa: BLE001 — 엑셀이 열려 있거나 깨졌어도 나머지는 보임
                    stage["error"] = f"{doc.name}을 읽지 못함({err.__class__.__name__}) — 엑셀에서 닫고 다시 보세요"
            stages.append(stage)
        notice = folder / "공고문서"
        attach = sorted(f.name for f in notice.iterdir() if f.is_file()) if notice.is_dir() else []
        return {"folder": share_path(folder), "stages": stages, "attach": attach,
                "docList": v.get("docList") or [], "notes": v.get("notes") or []}

    def file(self, key: str, rel: str) -> Path:
        """공고 폴더 안 파일 하나(내려받기용). 폴더 밖을 가리키면 오류."""
        v = self._read().get(key) or {}
        if not v.get("folder"):
            raise ValueError("폴더를 아직 만들지 않은 공고입니다")
        folder = Path(v["folder"]).resolve()
        target = (folder / rel).resolve()
        if folder not in target.parents or not target.is_file():
            raise ValueError("파일을 찾지 못했습니다")
        return target

    def _fill_template(self, key: str, folder: Path, nf, templates: Path) -> list[str]:
        """착공(공사)·착수(물품·용역)서류를 단계 폴더에 복사하고 기본사항을 채움. 이미 있으면 담당자 파일이라 그대로. 안내 줄을 돌려줌."""
        v = self._read().get(key) or {}
        group = group_of(v.get("work", ""))
        if group not in TEMPLATES:
            return ["[기본사항] 업무(공사/물품/용역)를 몰라 착공·착수서류를 만들지 않음"]
        name, stage, doc = TEMPLATES[group]
        target = folder / stage / doc
        if target.exists():
            return [f"[기본사항] {stage}/{doc}가 이미 있어 그대로 둠(다시 채우려면 그 파일을 지우고 폴더 만들기)"]
        if not (templates / name).exists():
            return [f"[기본사항] 표준틀 '{name}'이 {templates}에 없어 만들지 않음"]
        from quantdocs.notice import doc_text

        texts = []
        for f in nf.files:
            if f.suffix.lower() in (".hwp", ".hwpx", ".pdf", ".docx"):
                try:
                    texts.append(doc_text(f))
                except Exception:  # noqa: BLE001 — 한 첨부를 못 읽어도 나머지로
                    pass
        values, notes = basic_values(v, nf.title or v.get("title", ""), nf.institution or v.get("institution", ""),
                                     nf.contract_method, texts)
        fill_basic(templates / name, target, values)
        return ([f"[기본사항] {stage}/{doc} — 자동으로 채운 칸(하늘색): " + ", ".join(src.split(" — ")[0] for _, src in values.values()),
                 "  계약번호·계약일·착수일·수신·문서번호·제출일·인력은 직접 넣으세요"] + [f"  {n}" for n in notes])

    def check(self, client: Any, base_url: str, keys: list[str] | None = None) -> dict[str, int]:
        """'대기' 공고의 낙찰자를 확인. keys를 주면 그 공고만(상태와 상관없이 다시)."""
        now = self.now()
        out = {"won": 0, "lost": 0, "waiting": 0, "gaveUp": 0}
        with self._lock:
            items = self._read()
            for key, v in items.items():
                if keys is not None and key not in keys:
                    continue
                if keys is None and v.get("status") != "대기":
                    continue
                work, row = lookup(client, base_url, v["no"], v.get("ord", ""), v.get("work", ""))
                v["checkedAt"] = now.isoformat(timespec="seconds")
                if row:
                    mine = bool(self.own) and _digits(row.get("bidwinnrBizno")) == self.own
                    v.update(work=work, status="낙찰" if mine else "다른 업체 낙찰",
                             winner={"name": row.get("bidwinnrNm") or "", "amount": row.get("sucsfbidAmt") or "",
                                     "rate": row.get("sucsfbidRate") or "", "date": row.get("fnlSucsfDate") or ""})
                    v["title"] = v.get("title") or row.get("bidNtceNm") or ""
                    v["institution"] = v.get("institution") or row.get("dminsttNm") or ""
                    out["won" if mine else "lost"] += 1
                elif now - datetime.fromisoformat(v["added"]) > timedelta(days=GIVE_UP_DAYS):
                    v["status"] = "결과 없음"
                    out["gaveUp"] += 1
                else:
                    out["waiting"] += 1
            self._write(items)
        return out


def selfcheck() -> None:
    """가짜 낙찰현황 응답으로 목록·판정 확인(tests/test_postaward.py, python -m webapp.postaward)."""
    import tempfile

    class _Log:
        def entries(self):
            return [{"noticeNo": "R26BK00000001", "noticeOrd": "000", "title": "A 공사", "workType": "공사"},
                    {"noticeNo": "R26BK00000002", "noticeOrd": "000", "title": "B 물품", "workType": "물품"}]

    class _Client:
        def fetch_all_pages(self, url, op, params, label):
            if params["bidNtceNo"] == "R26BK00000001" and op.endswith("Cnstwk"):
                return [{"bidNtceOrd": "000", "bidwinnrBizno": "206-86-36338", "bidwinnrNm": "(주)지일"}]
            if params["bidNtceNo"] == "R26BK00000003" and op.endswith("Servc"):
                return [{"bidNtceOrd": "000", "bidwinnrBizno": "1112233333", "bidwinnrNm": "다른곳"}]
            return []

    with tempfile.TemporaryDirectory() as d:
        pa = PostAward(Path(d), _Log(), "2068636338")
        assert pa.sync_bidlog() == 2 and pa.sync_bidlog() == 0
        pa.add("R26BK00000003-000")
        r = pa.check(_Client(), "u")
        assert r == {"won": 1, "lost": 1, "waiting": 1, "gaveUp": 0}, r
        by = {v["no"]: v for v in pa.items()}
        assert by["R26BK00000001"]["status"] == "낙찰" and by["R26BK00000001"]["group"] == "공사"
        assert by["R26BK00000003"]["status"] == "다른 업체 낙찰" and by["R26BK00000003"]["group"] == "물품·용역"
        assert by["R26BK00000002"]["status"] == "대기"
        f = pa.make_folder("R26BK00000001-000", Path(d) / "out")
        assert f.parent.name == "공사" and (f / "2_착공").is_dir(), f
        f = pa.make_folder("R26BK00000003-000", Path(d) / "out")
        assert f.parent.name == "물품·용역" and (f / "2_착수").is_dir(), f

        # 기본사항 채우기(시험판) — 공고문 글에서 공사위치·기간·안전관리자, 낙찰금액은 낙찰현황 값
        vals, notes = basic_values({"work": "공사", "winner": {"amount": "184131570"}}, "A 공사", "정선군", "",
                                   ["3. 공사위치 : 강원특별자치도 정선군 고한읍 1\n착공일로부터 60일 이내\n안전관리자 선임"])
        assert vals[12][0] == 184131570 and vals[21][0] == "강원특별자치도 정선군 고한읍 1" and 11 not in vals, vals
        assert any("60일" in n for n in notes) and any("안전관리자 선임계" in n for n in notes), notes
        vals, notes = basic_values({"work": "용역"}, "B 용역", "아산시", "장기계속계약", ["현장대리인을 선임"])
        assert vals[11][0] == "장기계속" and 12 not in vals and any("현장대리인 서류" in n for n in notes), (vals, notes)

        from openpyxl import Workbook, load_workbook

        tpl = Path(d) / "tpl"
        tpl.mkdir()
        for name, _, _ in TEMPLATES.values():
            wb = Workbook()
            wb.active.title = "기본사항"
            wb.active["A6"], wb.active["A8"] = "수신", "공사명"
            wb.save(tpl / name)

        class _NF:
            title, institution, contract_method, files = "A 공사", "정선군", "", []
            work_type, award_method, skipped = "공사", "", []

        folder, _ = pa.prepare("R26BK00000001-000", Path(d) / "out", fetch=lambda k, t: _NF(), templates=tpl)
        ws = load_workbook(folder / "2_착공" / "착공서류.xlsx")["기본사항"]
        assert ws["B8"].value == "A 공사" and ws["B5"].value == "정선군" and ws["C8"].value.startswith("[자동]"), ws["C8"].value
        assert "자동으로 채운 칸" in (folder / "공고문서" / "서류_확인.txt").read_text(encoding="utf-8")
        _, _ = pa.prepare("R26BK00000001-000", Path(d) / "out", fetch=lambda k, t: _NF(), templates=tpl)  # 두 번째는 담당자 파일이라 그대로
        assert "이미 있어 그대로" in (folder / "공고문서" / "서류_확인.txt").read_text(encoding="utf-8")

        # 서류 현황 — 착공 단계는 채울 칸(비어 있는 '수신', 인력 1번)과 자동 칸, 준공은 아직 틀 없음
        st = pa.status("R26BK00000001-000")
        by_dir = {s["dir"]: s for s in st["stages"]}
        assert by_dir["2_착공"]["doc"] == "착공서류.xlsx" and "수신" in by_dir["2_착공"]["todo"], by_dir["2_착공"]
        assert "공사명" not in by_dir["2_착공"]["todo"] and ["공사명", "A 공사"] in by_dir["2_착공"]["auto"]
        assert by_dir["3_준공"]["ready"] is False and by_dir["3_준공"]["files"] == []
        assert pa.file("R26BK00000001-000", "2_착공/착공서류.xlsx").name == "착공서류.xlsx"
        for bad in ("../../postaward.json", "공고문서/없는파일.hwp"):
            try:
                pa.file("R26BK00000001-000", bad)
                raise AssertionError("폴더 밖·없는 파일은 막아야 함: " + bad)
            except ValueError:
                pass
        assert share_path("/work/낙찰후서류/공사/A") == "\\\\jiilserver\\jiil-webapp\\낙찰후서류\\공사\\A"


if __name__ == "__main__":
    selfcheck()
    print("ok")
