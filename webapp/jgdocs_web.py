"""투찰내역 '적격심사 서류' 버튼(2026-11-16 사용자 요청) — 공고번호로 기본사항을 채운 적격심사 엑셀을 만들어 돌려줌.

나라장터 공고(bidcalc.lookup)와 그 기록의 개찰 결과(예정가격·지일 투찰금액·A값)로 기본사항을 채우고,
`python -m jgdocs`를 따로 띄워 만든다(금액 묶음이 수식 모양을 정해서 한 프로세스에서 두 묶음을 못 만듦). 물품·공사만.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from jgdocs import fill

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 600


def make(entry: dict[str, Any], lookup: Callable[[str], dict[str, Any]]) -> tuple[str, bytes]:
    """(파일 이름, 엑셀) — 만들 수 없으면 ValueError(화면에 보일 이유)."""
    info = lookup(str(entry.get("noticeNo") or ""))
    work = info.get("work_type") or entry.get("workType")
    picked = fill.choose(work, info.get("est_price"))
    if not picked:
        raise ValueError(f"{work or '업무 미상'} 공고 — 적격심사 표준틀은 물품·공사만 있습니다(용역은 일반용역 기준 정리 중)")
    work, group = picked
    vals = fill.values(work, entry, info)
    with tempfile.TemporaryDirectory(prefix="jgdocs_") as d:
        out, vjson = Path(d) / "out.xlsx", Path(d) / "values.json"
        vjson.write_text(json.dumps({k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in vals.items()},
                                    ensure_ascii=False), encoding="utf-8")
        run = subprocess.run([sys.executable, "-m", "jgdocs", "--work", work, "--group", group, "--out", str(out), "--values", str(vjson)],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT)
        if run.returncode or not out.is_file():
            raise ValueError("적격심사 엑셀을 만들지 못했습니다: " + (run.stderr or run.stdout).strip()[-300:])
        body = out.read_bytes()
    no = entry.get("noticeNo") or "공고"
    return f"적격심사_{work}_{group}_{no}.xlsx", body
