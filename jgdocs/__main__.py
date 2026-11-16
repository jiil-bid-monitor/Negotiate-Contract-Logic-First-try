"""적격심사 서류 엑셀 만들기.

    python -m jgdocs --work 물품|공사 --group 10억이상|10억미만 --out 파일.xlsx [--values 값.json]

금액 묶음(JG_GROUP)이 수식 모양을 정해서 모듈을 읽기 전에 정함 — 그래서 웹앱은 이 명령을 따로 띄움(webapp/server.py /api/jgdocs).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="적격심사 서류 엑셀")
    p.add_argument("--work", required=True, choices=("물품", "공사"))
    p.add_argument("--group", required=True, choices=("10억이상", "10억미만"))
    p.add_argument("--out", required=True)
    p.add_argument("--values", help="기본사항 칸 값 JSON(날짜는 'YYYY-MM-DDTHH:MM:SS')")
    a = p.parse_args(argv)
    os.environ["JG_GROUP"] = a.group
    from . import fill

    if a.work == "물품":
        from . import goods as mod
    else:
        from . import works as mod
    out = Path(a.out)
    ev = mod.build(out) if a.work == "공사" else mod.build(out, "물품")
    from . import goods

    goods.add_doc_tabs(out, ev)
    if a.values:
        vals = json.loads(Path(a.values).read_text(encoding="utf-8"))
        for k, v in list(vals.items()):
            if isinstance(v, str) and len(v) == 19 and v[10] == "T":
                vals[k] = dt.datetime.fromisoformat(v)
        print("채운 칸:", ", ".join(fill.apply(out, mod.FIELDS, vals)))
    print("ok", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
