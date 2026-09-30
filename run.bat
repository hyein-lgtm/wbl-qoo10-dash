@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/2] 필요한 프로그램 설치 확인 중...
py -m pip install -q -r requirements.txt
echo [2/2] 대시보드 실행 중... 잠시 후 브라우저가 열려요.
echo 이 검은 창을 닫으면 대시보드도 꺼져요.
start "" http://localhost:8000
py -m uvicorn app:app --port 8000
pause
