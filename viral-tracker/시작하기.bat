@echo off
chcp 65001 >nul
title 급상승 영상 트래커
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo  [!] Python이 설치되어 있지 않습니다.
    echo      https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행하세요.
    echo      설치 화면에서 "Add python.exe to PATH" 체크를 꼭 해주세요!
    echo.
    pause
    exit /b 1
)

if not exist .venv (
    echo.
    echo  처음 실행이라 필요한 구성요소를 설치합니다. 몇 분 걸릴 수 있어요...
    echo.
    python -m venv .venv
    .venv\Scripts\python -m pip install --upgrade pip -q
    .venv\Scripts\python -m pip install -r requirements.txt -q
)

echo.
echo  서버를 시작합니다. 잠시 후 브라우저가 자동으로 열립니다.
echo  이 검은 창을 닫으면 앱이 종료됩니다. (내려받은 영상: downloads 폴더)
echo.
.venv\Scripts\python app.py
pause
