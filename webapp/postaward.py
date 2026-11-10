"""낙찰 후 서류 — 1단계: 낙찰 여부 확인 목록.

투찰 기록(bid_log.json)에 있는 공고와 사람이 직접 넣은 공고를 지켜보다가, 나라장터 낙찰현황에 낙찰자가 뜨면
지일이면 '낙찰', 다른 업체면 '다른 업체 낙찰'로 바꾼다. 매일 22시 밤 작업(bidnightly)이 부르고, 화면에서 바로 확인할 수도 있다.
개찰 1순위는 적격심사에서 바뀔 수 있어 쓰지 않는다 — 낙찰현황(심사 끝난 뒤 낙찰자)만 본다.

실측(2026-11-10): 낙찰현황은 공사·용역·물품 오퍼레이션이 따로, 공고 하나는 inqryDiv=4 + bidNtceNo.
개찰부터 낙찰 확정까지 20일 걸린 공사가 있어 GIVE_UP_DAYS를 넉넉히 둔다.
"""
from __future__ import annotations

import json
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

    def prepare(self, key: str, root: Path, fetch=None) -> tuple[Path, list[dict]]:
        """공고 폴더 만들기 — 나라장터에서 공고 정보·첨부를 받아(정량평가서와 같은 quantdocs.g2b.fetch_notice) 업무를 정하고,
        첨부는 공고문서/에 두고, 첨부가 요구하는 제출서류를 읽어 표준틀에 없는 것을 '서류_확인.txt'에 적는다."""
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
        lines = [f"{key} {v.get('title') or nf.title} — 첨부에서 읽은 제출서류 (자동, 원문과 대조하세요)", ""]
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
            self._write(items)
        return folder, docs

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


if __name__ == "__main__":
    selfcheck()
    print("ok")
