"""과거사업 목록에 2026년부터의 사업 폴더를 더함(11/20 사용자 — '2026 프로젝트·2027 프로젝트에 생기는 폴더 기준으로').

`\\\\jiilserver\\002 진행프로젝트\\YYYY 프로젝트`(YYYY ≥ 2026)의 사업 폴더 → 과거사업 목록 줄(`data/past_live.json`, 기존 155건 파일은 안 건드림):
- 끝남 = 폴더 안에 준공계(납품계·완료계) 파일이 있음(파일 이름 앞 날짜 = 준공일) 또는 `실행완료` 아래 폴더 / 그 밖은 진행 중
  (11/20 사용자 — 실적은 준공 기준, 청구는 설명만: '준공 2026-03-16 · 청구 남음'). 사업 연도 앞해보다 오래된 날짜는 다른 사업에서 복사해 온 서류로 보고 무시
- 사업 폴더만 — 그 사업의 제안요청서·과업지시서·공고서·시방서가 있는 폴더. 참고로 넣어 둔 다른 사업 문서(이름에 다른 사업명)는 안 씀
  (몽골 폴더의 평창 인도교 과업지시서 등), 그런 문서뿐인 폴더(설계 파일 서식 등)는 빠짐
- 기존 155건에 같은 폴더가 있으면 빼고, 이름이 같은 폴더(날짜 앞말만 다름)·같은 문서를 쓰는 폴더는 하나만
- 분야·과업 태그는 그 문서로(jiil `task_profile.build_profile`), 같은 문서면 다시 안 읽음 — 싱크로율에도 같이 씀
NAS 웹앱은 002 진행프로젝트를 못 보므로 **서버 PC가 만들어 NAS로 보낸다**(`POST /api/past-live`). NAS는 받은 목록을 저장해 두고 켤 때 읽음.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

LIVE_FILE = "past_live.json"
PROJECT_DIR = Path(r"\\jiilserver\002 진행프로젝트")
FIRST_YEAR = 2026
DONE_DIR = "실행완료"
BID_DIR = "입찰"  # 웹앱이 낙찰 공고 폴더를 두는 곳(awardsync) — 이 안의 폴더 하나하나가 사업
DOC_SUFFIXES = (".hwp", ".hwpx", ".pdf", ".docx")
DOC_WORDS = ("제안요청", "과업지시", "과업내용", "공고서", "입찰공고", "공고문", "규격서", "시방서")
DONE_WORDS = ("준공계", "준공서류", "납품계", "완료계", "완수계", "준공검사원", "납품검사원", "완료검사원")
CLAIM_WORDS = ("청구서", "대금청구")
# 문서 이름에서 떼어 내는 말 — 남는 말이 사업 이름(다른 사업 문서 가리기)
_DOC_NOISE = re.compile(r"(제안요청서?|과업지시서?|과업내용서?|입찰공고문?|공고문|공고서|규격서|설계시방서|특수시방서|시방서|최종|초안|수정본?|부록|별첨|붙임|해제|사본|복사)")
MAX_DOCS = 4
MAX_WALK = 4000  # 폴더 하나에서 볼 파일 수(공유 폴더라 느림)
log = logging.getLogger(__name__)


def project_dir() -> Path:
    return Path(os.environ.get("PROJECT_DIR", "").strip() or PROJECT_DIR)


def clean_title(name: str) -> str:
    """'260514_동해전천스테이션'·'2026_횡성목재문화체험장'·'06_몽골_칸부미디어' → 사업 이름."""
    s = re.sub(r"^R\d{2}[A-Z]{2}\d{8}(?:-\d{2,3})?_", "", name)  # 웹앱이 만든 폴더 'R26BK…-000_공고명'
    s = re.sub(r"^(?:\d{4,6}|\d{2})[_\s-]+", "", s)
    return re.sub(r"\s+", " ", s.replace("_", " ")).strip() or name


def _norm(name: str) -> str:
    return re.sub(r"[\s_·.,()\-]", "", clean_title(name))


def project_folders(root: Path | None = None) -> list[tuple[int, Path, bool]]:
    """[(연도, 폴더, 끝남)] — YYYY 프로젝트(YYYY ≥ 2026) 바로 아래(진행 중)와 실행완료 아래(끝남)."""
    root = root or project_dir()
    out = []
    try:
        years = sorted(p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"(\d{4}) 프로젝트", p.name))
    except OSError:
        return []
    for y in years:
        year = int(y.name[:4])
        if year < FIRST_YEAR:
            continue
        for p in sorted(y.iterdir()):
            if not p.is_dir() or p.name.startswith((".", "~")):
                continue
            if p.name == BID_DIR:
                out += [(year, q, False) for q in sorted(p.iterdir()) if q.is_dir() and not q.name.startswith((".", "~", "_"))]
            elif p.name == DONE_DIR:
                out += [(year, q, True) for q in sorted(p.iterdir()) if q.is_dir() and not q.name.startswith((".", "~"))]
            else:
                out.append((year, p, False))
    return out


def _date_of(name: str):
    """파일 이름 앞 날짜 '260316_'·'20260316_' → date, 없으면 None."""
    from datetime import date

    m = re.match(r"^(\d{6}|\d{8})[_\s-]", name)
    if not m:
        return None
    s = m.group(1)
    y, mo, d = (int("20" + s[:2]), int(s[2:4]), int(s[4:6])) if len(s) == 6 else (int(s[:4]), int(s[4:6]), int(s[6:8]))
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _core(name: str) -> str:
    """문서 이름의 사업 이름 부분 — 날짜·판 번호·문서 종류 말·기호를 뺀 한글."""
    s = re.sub(r"\.(hwpx?|pdf|docx)$", "", name, flags=re.I)
    s = re.sub(r"^\d{6,8}[_\s-]*", "", s)
    s = re.sub(r"_?v\d+$", "", s)
    return "".join(re.findall(r"[가-힣]+", _DOC_NOISE.sub("", s)))


def _related(core: str, title: str) -> bool:
    t = re.sub(r"[^가-힣]", "", title)
    n = min(4, len(t))  # 4글자 — '활성화'·'디자인'처럼 흔한 3글자로 다른 사업 문서가 걸리지 않게(온천천 폴더의 영해장터 문서)
    return n >= 2 and any(core[i:i + n] in t for i in range(len(core) - n + 1))


def scan(folder: Path, title: str, year: int) -> tuple[list[Path], Any, Any]:
    """(사업 문서, 준공일, 청구일) — 한 번 훑어서. 문서: 이름에 이 사업 이름이 있는 것 먼저, 없으면 이름이 짧은(종류만 적힌) 것,
    다른 사업 이름이 붙은 문서는 안 씀. 날짜: 사업 연도 앞해부터만(예전 사업에서 복사해 온 서류는 무시)."""
    from datetime import date

    found, n, dones, claims = [], 0, [], []
    oldest = date(year - 1, 1, 1)
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "~", "#"))]
        for f in filenames:
            n += 1
            if f.startswith("~$"):
                continue
            flat = f.replace(" ", "")
            if f.lower().endswith(DOC_SUFFIXES) and any(w in flat for w in DOC_WORDS):
                found.append(Path(dirpath) / f)
            d = _date_of(f)
            if d and d >= oldest:
                if any(w in flat for w in DONE_WORDS):
                    dones.append(d)
                elif any(w in flat for w in CLAIM_WORDS):
                    claims.append(d)
        if n > MAX_WALK:
            break
    mine = [p for p in found if _related(_core(p.name), title)]
    plain = [p for p in found if len(_core(p.name)) <= 2]  # '과업지시서'·'공고서(물품)'처럼 종류만 — '공고문(월롱초)'은 다른 사업
    pick = mine or plain
    rank = lambda p: (0 if re.search("제안요청|과업지시|과업내용", p.name) else 1, p.suffix.lower() == ".pdf", len(p.parts))  # noqa: E731
    out, seen = [], set()
    for p in sorted(pick, key=rank):
        k = re.sub(r"\.(hwpx?|pdf|docx)$", "", re.sub(r"^\d{6,8}_", "", p.name.lower()))
        if k not in seen:
            seen.add(k); out.append(p)
        if len(out) >= MAX_DOCS:
            break
    return out, max(dones) if dones else None, max(claims) if claims else None


def _submitted(folder: Path):
    """웹앱이 만든 폴더 — 준공계·납품계·완료계의 제출일을 적어 저장했으면 그날(빈 틀은 처음부터 있어 파일만으로는 준공으로 안 봄)."""
    from datetime import date

    from .postaward import DONE_DIRS, DONE_TEMPLATES, read_progress

    names = {t[2] for t in DONE_TEMPLATES.values()}
    for d in DONE_DIRS:
        for f in (folder / d).glob("*.xlsx") if (folder / d).is_dir() else ():
            if f.name in names and (s := read_progress(f, "제출일")):
                return date.fromisoformat(s)
    return None


def base_folders(base: list[dict]) -> set[str]:
    """기존 155건이 온 폴더 이름(['2026/2026_이청준문학관'] → '2026_이청준문학관')."""
    out = set()
    for p in base:
        v = p.get("folders") or []
        if isinstance(v, str):
            v = re.findall(r"'([^']+)'", v)
        out |= {_norm(str(x).split("/", 1)[-1]) for x in v}
    return out


def build(data_dir: Path, tp, base: list[dict], root: Path | None = None) -> list[dict[str, Any]]:
    """사업 폴더 → 과거사업 목록 줄. 같은 문서(이름·수정 시각)면 지난번 태그를 그대로 씀."""
    from quantdocs.notice import doc_text

    path = Path(data_dir) / LIVE_FILE
    try:
        old = {r["key"]: r for r in json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError):
        old = {}
    skip, rows, seen, seen_docs = base_folders(base), [], set(), set()
    for year, folder, moved in project_folders(root):
        n = _norm(folder.name)
        if n in skip or n in seen:
            continue
        docs, done_at, claim_at = scan(folder, clean_title(folder.name), year)
        sig_docs = {d.name for d in docs}
        if not docs or sig_docs & seen_docs:  # 문서가 하나라도 같으면 같은 사업(공항사거리조형물 ↔ 260824_청주공항사거리…)
            continue  # 사업 폴더가 아님(서식·장비·자료 모음 등) 또는 같은 문서를 쓰는 폴더가 이미 있음
        seen.add(n); seen_docs |= sig_docs
        done_at = done_at or _submitted(folder)
        done = bool(done_at) or moved
        note = (("준공 " + done_at.isoformat()) if done_at else "준공계 파일 없음") + " · " + (("청구 " + claim_at.isoformat()) if claim_at else "청구 남음") \
            if done else "준공 전"
        key = f"{year}/{'실행완료/' if moved else ''}{folder.name}"
        sig = [[d.name, round(d.stat().st_mtime)] for d in docs]
        prev, title = old.get(key), clean_title(folder.name)
        if prev and prev.get("sig") == sig:
            tags, counts, evidence = prev["tags"], prev.get("counts", {}), prev.get("evidence", {})
        else:
            texts = []
            for d in docs:
                try:
                    texts.append(doc_text(d))
                except Exception:  # noqa: BLE001 — 한 문서를 못 읽어도 나머지로
                    pass
            prof = tp.build_profile(texts, title=title)
            tags, counts, evidence = prof.tags, prof.counts, {k: s[:200] for k, s in prof.evidence.items()}
        if not tags:
            continue
        rows.append({"key": key, "year": year, "title": title, "status": "끝남" if done else "진행 중", "note": note,
                     "doneDate": done_at.isoformat() if done_at else "", "claimDate": claim_at.isoformat() if claim_at else "",
                     "folders": [f"{year}/{folder.name}"], "docs": [d.name for d in docs], "sig": sig,
                     "overview": "", "exhibition": "", "tags": tags, "counts": counts, "evidence": evidence,
                     "source": "사업 폴더"})
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def load_saved(app) -> None:
    """NAS: 서버 PC가 보내 저장해 둔 목록을 켤 때 읽음."""
    try:
        rows = json.loads((app.store.path.parent / LIVE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if getattr(app.past, "available", False):
        app.past.set_live(rows)


def receive(app, rows: list) -> int:
    """NAS: 서버 PC가 보낸 목록을 저장하고 반영."""
    rows = [r for r in rows if isinstance(r, dict) and r.get("tags")]
    (app.store.path.parent / LIVE_FILE).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    if getattr(app.past, "available", False):
        app.past.set_live(rows)
    return len(rows)


def start(app, every: int = 3600) -> None:
    """서버 PC(사업 폴더를 볼 수 있는 곳): 한 시간마다 만들어 반영하고 NAS로 보냄. NAS: 저장된 목록만 읽음."""
    load_saved(app)
    if not app.team_remote or not getattr(app.past, "available", False):
        return

    def loop():
        import requests

        while True:
            try:
                rows = build(app.store.path.parent, app.past._tp, getattr(app.past, "_base", app.past.projects))
                app.past.set_live(rows)
                base_url, token = app.team_remote
                # 한글을 그대로 보냄 — json=으로 보내면 한글이 이스케이프돼 3배로 커져 NAS 요청 크기 한도에 걸렸음(11/20, 15건 126KB → 400)
                body = json.dumps({"rows": rows}, ensure_ascii=False).encode("utf-8")
                res = requests.post(base_url + "/api/past-live", data=body, timeout=60,
                                    headers={"X-App-Token": token, "Content-Type": "application/json"})
                if res.status_code != 200:
                    log.warning("과거사업 목록을 NAS로 못 보냄: %s %s", res.status_code, res.text[:200])
            except Exception:  # noqa: BLE001 — 다음 시간에 다시
                log.exception("과거사업 목록(2026년~ 사업 폴더) 갱신 실패")
            threading.Event().wait(every)

    threading.Thread(target=loop, daemon=True, name="pastlive").start()
