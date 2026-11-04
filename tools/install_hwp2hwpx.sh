#!/usr/bin/env bash
# 옛 한글(.hwp) → HWPX 변환기 설치 (서버 PC, Git Bash) —  bash tools/install_hwp2hwpx.sh [시험할.hwp]
# 필요한 것: 자바 JDK(javac·jar·java). 없으면:  winget install EclipseAdoptium.Temurin.17.JDK  → Git Bash 새로 열기
# 하는 일: 공개 라이브러리(Apache-2.0) hwplib·hwpxlib를 Maven Central에서, hwp2hwpx 소스를 GitHub에서 받아
#          tools/hwp2hwpx/build/hwp2hwpx-cli.jar 하나로 빌드하고, .env에 HWP2HWPX_CMD를 적는다. 빌드 폴더는 깃에 안 올라감.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT_WIN=$(pwd -W 2>/dev/null || pwd)
B=tools/hwp2hwpx/build
HWPLIB=1.1.10
HWPXLIB=1.0.9

for t in java javac jar git curl; do
  command -v "$t" >/dev/null 2>&1 || { echo "! $t 이(가) 없습니다 — 자바 JDK 설치: winget install EclipseAdoptium.Temurin.17.JDK (설치 뒤 Git Bash 새로 열기)"; exit 1; }
done

rm -rf "$B" && mkdir -p "$B/lib" "$B/classes"
echo "1/4 라이브러리 받기"
curl -fsSL -o "$B/lib/hwplib.jar"  "https://repo1.maven.org/maven2/kr/dogfoot/hwplib/$HWPLIB/hwplib-$HWPLIB.jar"
curl -fsSL -o "$B/lib/hwpxlib.jar" "https://repo1.maven.org/maven2/kr/dogfoot/hwpxlib/$HWPXLIB/hwpxlib-$HWPXLIB.jar"
echo "2/4 hwp2hwpx 소스 받기"
git clone -q --depth 1 https://github.com/neolord0/hwp2hwpx.git "$B/src"

echo "3/4 빌드"
SEP=":"; case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) SEP=";";; esac
find "$B/src/src/main/java" -name '*.java' > "$B/sources.txt"
echo "tools/hwp2hwpx/Convert.java" >> "$B/sources.txt"
javac -nowarn -encoding UTF-8 --release 8 -cp "$B/lib/hwplib.jar${SEP}$B/lib/hwpxlib.jar" -d "$B/classes" @"$B/sources.txt" 2>&1 | grep -v "^Note:\|^warning:\|^경고" || true
[ -f "$B/classes/Convert.class" ] || { echo "! 빌드 실패 — 위 오류를 보여 주세요"; exit 1; }
( cd "$B/classes" && jar xf ../lib/hwplib.jar && jar xf ../lib/hwpxlib.jar && rm -rf META-INF )
jar cfe "$B/hwp2hwpx-cli.jar" Convert -C "$B/classes" .
JAR="$ROOT_WIN/$B/hwp2hwpx-cli.jar"
echo "   → $JAR"

echo "4/4 .env에 HWP2HWPX_CMD 적기"
# 작은따옴표로 감쌈 — start_web.sh가 .env를 bash로 읽어서, 없으면 'java'만 값이 되고 나머지는 명령으로 실행됨
LINE="HWP2HWPX_CMD='java -jar \"$JAR\" \"{src}\" \"{dst}\"'"
touch .env
grep -v '^#\? *HWP2HWPX_CMD=' .env > .env.tmp || true
echo "$LINE" >> .env.tmp && mv .env.tmp .env
echo "   $LINE"

if [ $# -ge 1 ]; then
  OUT="${1%.*}_변환시험.hwpx"
  java -jar "$B/hwp2hwpx-cli.jar" "$1" "$OUT" && echo "시험 변환 성공: $OUT (한글로 열어 확인)"
fi
echo "끝 — 웹앱을 다시 켜면(bash start_web.sh) .hwp 공고도 서식을 자릅니다."
