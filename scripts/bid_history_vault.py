"""지일 투찰 이력(21라인 내보내기 .xls 등)을 암호화해 공개 레포에 두고, 필요할 때 푼다 (2026-10-26 사용자 결정).

투찰 이력은 공개하면 안 되는 자료라 원본은 깃 밖 `data/bid_history/`(data/는 .gitignore)에만 두고,
**잠근 묶음 `corpus/bid_history.enc`만 커밋**한다. 비밀번호는 환경변수 `BID_HISTORY_PASSPHRASE` —
레포·채팅·커밋 메시지에 적지 않는다(싱크로율 묶음 `CORPUS_PASSPHRASE`와 따로 둠, Actions에는 넣지 않음).
암호화는 `nego.corpus`와 같음(AES-256-GCM, scrypt 키) — 비밀번호가 틀리거나 파일이 깨지면 바로 실패한다.

    # 잠그기 — data/bid_history/ 안 파일 전부 → corpus/bid_history.enc (이 파일만 커밋)
    BID_HISTORY_PASSPHRASE=… python scripts/bid_history_vault.py seal
    # 풀기 — corpus/bid_history.enc → data/bid_history/
    BID_HISTORY_PASSPHRASE=… python scripts/bid_history_vault.py open
    # 묶음 안 파일 목록만 보기(풀지 않음)
    BID_HISTORY_PASSPHRASE=… python scripts/bid_history_vault.py list
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nego.corpus import seal, unseal  # noqa: E402

SEALED = ROOT / "corpus" / "bid_history.enc"
PLAIN_DIR = ROOT / "data" / "bid_history"
ENV = "BID_HISTORY_PASSPHRASE"
MIN_PASSPHRASE = 16  # 묶음이 공개 레포에 있어 짧은 비밀번호는 대입으로 풀릴 수 있다


def _tar(folder: Path) -> bytes:
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    if not files:
        raise FileNotFoundError(f"잠글 파일이 없습니다: {folder}")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path in files:
            tar.add(path, arcname=path.relative_to(folder).as_posix())
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/bid_history_vault.py", description="투찰 이력 암호화 묶음")
    parser.add_argument("action", choices=["seal", "open", "list"])
    parser.add_argument("--dir", default=str(PLAIN_DIR), help="원본 폴더(잠글 때) / 풀 폴더(풀 때)")
    args = parser.parse_args(argv)
    folder = Path(args.dir)

    passphrase = os.environ.get(ENV, "")
    if not passphrase:
        print(f"{ENV} 환경변수가 없습니다.")
        return 1

    if args.action == "seal":
        if len(passphrase) < MIN_PASSPHRASE:
            print(f"비밀번호가 너무 짧습니다({MIN_PASSPHRASE}자 이상) — 공개 레포에 올라가는 묶음이라 대입 공격을 막아야 합니다.")
            return 1
        SEALED.parent.mkdir(parents=True, exist_ok=True)
        SEALED.write_bytes(seal(_tar(folder), passphrase))
        print(f"암호화 묶음 저장: {SEALED} — 이 파일만 커밋하세요(원본 {folder}은 깃 밖)")
        return 0

    if not SEALED.exists():
        print(f"{SEALED} 없음")
        return 1
    try:
        plain = unseal(SEALED.read_bytes(), passphrase)
    except Exception as err:  # noqa: BLE001 — 비밀번호 틀림·파일 깨짐
        print(f"묶음을 풀지 못했습니다(비밀번호 확인): {type(err).__name__}")
        return 1
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r") as tar:
        if args.action == "list":
            for member in tar.getmembers():
                print(f"{member.size:>12,}  {member.name}")
            return 0
        folder.mkdir(parents=True, exist_ok=True)
        tar.extractall(folder, filter="data")
    print(f"투찰 이력 풀기: {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
