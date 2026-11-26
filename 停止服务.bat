@echo off
chcp 65001 >nul
REM 仅结束面试听记服务（端口 7788 的 python 进程）
for /f "tokens=5" %%i in ('netstat -ano ^| findstr :7788 ^| findstr LISTENING') do (
    echo 结束进程 PID %%i
    taskkill /PID %%i /F >nul 2>&1
)
echo 已停止。
timeout /t 2 >nul
