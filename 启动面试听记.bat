@echo off
chcp 65001 >nul
REM 切换到脚本所在目录（仓库根目录）
cd /d "%~dp0"

REM Python 解释器探测顺序：环境变量 IR_PYTHON > 本机便携 Python > PATH 中的 pythonw/python
set "PY=%IR_PYTHON%"
if not defined PY set "PY=E:\Software\TMSpeech\external-recognizer\WPy64-31700\python\pythonw.exe"
if not exist "%PY%" set "PY=E:\Software\TMSpeech\external-recognizer\WPy64-31700\python\python.exe"
if not exist "%PY%" set "PY=pythonw.exe"

REM 若服务已在运行，直接打开界面
powershell -NoProfile -Command "try{(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:7788/api/state -TimeoutSec 2)|Out-Null; exit 0}catch{exit 1}"
if %errorlevel%==0 (
    start "" "http://127.0.0.1:7788"
    exit /b
)

start "InterviewLiveNote" /min "%PY%" server.py
timeout /t 4 >nul
start "" "http://127.0.0.1:7788"
