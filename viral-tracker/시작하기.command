#!/bin/bash
# 맥용 더블클릭 실행 파일 — 처음 한 번은 우클릭 → 열기 로 실행하세요.
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "[!] Python3가 설치되어 있지 않습니다."
    echo "    https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행하세요."
    read -p "엔터를 누르면 창이 닫힙니다."
    exit 1
fi

if [ ! -d .venv ]; then
    echo "처음 실행이라 필요한 구성요소를 설치합니다. 몇 분 걸릴 수 있어요..."
    python3 -m venv .venv
    ./.venv/bin/pip install --upgrade pip -q
    ./.venv/bin/pip install -r requirements.txt -q
fi

echo
echo "서버를 시작합니다. 잠시 후 브라우저가 자동으로 열립니다."
echo "이 창을 닫으면 앱이 종료됩니다. (내려받은 영상: downloads 폴더)"
echo
./.venv/bin/python app.py
