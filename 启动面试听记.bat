@echo off
chcp 65001 >nul
cd /d "E:\Software\InterviewLiveNote"

set PY=E:\Software\TMSpeech\external-recognizer\WPy64-31700\python\pythonw.exe
if not exist "%PY%" set PY=E:\Software\TMSpeech\external-recognizer\WPy64-31700\python\python.exe

REM 若服务已在运行，直接打开界面
powershell -NoProfile -Command "try{(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:7788/api/state -TimeoutSec 2)|Out-Null; exit 0}catch{exit 1}"
if %errorlevel%==0 (
    start "" "http://127.0.0.1:7788"
    exit /b
)

start "InterviewLiveNote" /min "%PY%" server.py
timeout /t 4 >nul
start "" "http://127.0.0.1:7788"
