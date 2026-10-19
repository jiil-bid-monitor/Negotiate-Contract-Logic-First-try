"""싱크로율 자료 묶음 암호화·복호화 (2026-10-19 통합 결정 16번 — 팀원 수집기 `scripts/corpusCrypto.ts`와 같은 방식).

싱크로율은 비공개 레포 jiil-past-contracts의 과거 실적 프로필(`docs/summaries/past_task_profiles.json`)과
프로필 코드(`evaluation/`)가 있어야 계산된다. 이 레포는 공개라 그대로 올릴 수 없어서,
**암호화한 묶음만 커밋하고 비밀번호는 GitHub Secret `CORPUS_PASSPHRASE`에 둔다**.

    # 과거 실적이 바뀌었을 때(담당자 PC, jiil-past-contracts가 옆에 있을 때) — 묶어서 잠그고 커밋
    CORPUS_PASSPHRASE=… python -m nego.corpus seal [--jiil-repo ../jiil-past-contracts]
    # GitHub Actions — 풀어서 data/jiil에 두고 JIIL_REPO=data/jiil
    CORPUS_PASSPHRASE=… python -m nego.corpus open

형식: b"JCORP1" | salt(16) | iv(12) | tag(16) | AES-256-GCM(gzip(tar))  — 키는 scrypt(N=2^15, r=8, p=1, 32바이트).
GCM이라 비밀번호가 틀리거나 파일이 깨지면 바로 실패한다. 비밀번호가 없으면 아무것도 안 하고 0으로 끝낸다
(싱크로율만 빠지고 수집은 그대로).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEALED = ROOT / "corpus" / "sync_bundle.enc"
OPENED = ROOT / "data" / "jiil"
MAGIC = b"JCORP1"
# 묶을 것 — 과거 실적 프로필과 그걸 읽는 코드
BUNDLE_PATHS = ("docs/summaries/past_task_profiles.json", "evaluation")


def _key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=1 << 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def seal(plain: bytes, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, iv = os.urandom(16), os.urandom(12)
    sealed = AESGCM(_key(passphrase, salt)).encrypt(iv, gzip.compress(plain), None)
    # AESGCM은 암호문 뒤에 tag(16)를 붙여 준다 — 팀원 형식(salt|iv|tag|암호문)으로 순서를 맞춘다
    return MAGIC + salt + iv + sealed[-16:] + sealed[:-16]


def unseal(data: bytes, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not data.startswith(MAGIC):
        raise ValueError("싱크로율 묶음 형식이 아닙니다")
    body = data[len(MAGIC):]
    salt, iv, tag, cipher = body[:16], body[16:28], body[28:44], body[44:]
    return gzip.decompress(AESGCM(_key(passphrase, salt)).decrypt(iv, cipher + tag, None))


def _tar(repo: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel in BUNDLE_PATHS:
            path = repo / rel
            if not path.exists():
                raise FileNotFoundError(f"묶을 파일이 없습니다: {path}")
            tar.add(path, arcname=rel, filter=lambda info: None if "__pycache__" in info.name else info)
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nego.corpus", description="싱크로율 자료 묶음 암호화·복호화")
    parser.add_argument("action", choices=["seal", "open"])
    parser.add_argument("--jiil-repo", default=os.environ.get("JIIL_REPO") or str(ROOT.parent / "jiil-past-contracts"))
    args = parser.parse_args(argv)

    passphrase = os.environ.get("CORPUS_PASSPHRASE", "")
    if not passphrase:
        print("CORPUS_PASSPHRASE 없음 → 건너뜀 (싱크로율 없이 진행)")
        return 0 if args.action == "open" else 1

    if args.action == "seal":
        SEALED.parent.mkdir(parents=True, exist_ok=True)
        SEALED.write_bytes(seal(_tar(Path(args.jiil_repo)), passphrase))
        print(f"암호화 묶음 저장: {SEALED} — 이 파일만 커밋하세요")
        return 0

    if not SEALED.exists():
        print(f"{SEALED} 없음 → 건너뜀 (싱크로율 없이 진행)")
        return 0
    try:
        plain = unseal(SEALED.read_bytes(), passphrase)
    except Exception as err:  # noqa: BLE001 — 비밀번호 틀림·파일 깨짐: 싱크로율만 빠지게
        print(f"::warning::싱크로율 묶음을 풀지 못했습니다(비밀번호 확인): {type(err).__name__}")
        return 0
    OPENED.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r") as tar:
        tar.extractall(OPENED, filter="data")
    print(f"싱크로율 자료 풀기: {OPENED} (JIIL_REPO로 지정)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
