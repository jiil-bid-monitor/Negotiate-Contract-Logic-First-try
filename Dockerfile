# 사내 NAS(Synology Container Manager)에서 웹앱을 돌리는 이미지.
# 이미지엔 파이썬과 패키지만 넣고, 코드·data·output·.env는 docker-compose.yml이 NAS 폴더를 그대로 붙인다
# — 코드를 바꿔도 이미지를 다시 만들 필요 없이 컨테이너만 다시 켜면 된다(requirements.txt가 바뀔 때만 다시 빌드).
FROM python:3.12-slim

# TZ: 슬림 이미지엔 시간대 파일이 없어 "Asia/Seoul" 대신 POSIX 표기(UTC+9)를 쓴다 — 마감 D-N·"오늘" 계산이 한국 날짜로.
ENV PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=KST-9

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

WORKDIR /work/Negotiate-Contract-Logic-First-try
CMD ["python", "-m", "webapp"]
