@echo off
rem     ____                       _____
rem    / __ \____  ____  ____ ___ / ___/___  ____  ________
rem   / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
rem  / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
rem /_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/
rem
rem @File      :   package/build.cmd
rem @Author    :   RmCat
rem @Contact   :   RemiliaCatNero@gmail.com
rem @License   :   AGPL
rem @Copyright :   (C) 2026, RoomSense-Team
rem @Desc      :   打包入口：双击运行，或把参数转发给 build.ps1。
rem
setlocal

set "PS=powershell"
where pwsh >nul 2>nul && set "PS=pwsh"

%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
    echo.
    echo Build failed with exit code %CODE%.
)

rem 双击运行时保持窗口不关闭。
if "%~1"=="" pause
exit /b %CODE%
