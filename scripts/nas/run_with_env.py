"""레포 .env를 읽어 환경변수로 넣고 명령을 실행한다 — .env를 고친 뒤 웹앱을 다시 켜면 반영되게(관리자 없이).

컨테이너의 env_file은 만들 때 한 번만 읽고, 셸로 읽으면(. .env) 비밀번호의 & ; 같은 글자가 명령으로 실행된다.
그래서 글자 그대로 읽는다. docker-compose.yml에 적은 HOST·PORT·BACKUP_DIR은 .env보다 우선한다.

    python scripts/nas/run_with_env.py python -m webapp
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
COMPOSE_WINS = {"HOST", "PORT", "BACKUP_DIR"}


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def merged_env(file_values: dict[str, str], current: dict[str, str]) -> dict[str, str]:
    env = dict(current)
    env.update({k: v for k, v in file_values.items() if k not in COMPOSE_WINS or k not in current})
    return env


def main(argv: list[str]) -> int:
    if not argv:
        print("실행할 명령이 없습니다", file=sys.stderr)
        return 2
    env = dict(os.environ)
    if ENV_FILE.exists():
        env = merged_env(read_env(ENV_FILE), env)
    else:
        print(f"{ENV_FILE}이 없습니다 — 서비스키 없이 실행합니다", file=sys.stderr)
    # 웹앱이 수집 뒤 스스로 다시 켜게 — webapp_run.sh가 보는 요청 파일(webapp/autorestart.py). 이 스크립트는 NAS에서만 쓴다.
    env.setdefault("WEBAPP_RESTART_FILE", str(ENV_FILE.parents[1] / "control" / "restart-webapp"))
    os.execvpe(argv[0], argv, env)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
