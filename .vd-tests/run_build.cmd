@echo off
REM Отсоединяемая сборка: лог на каждую попытку свой - build-<дата-время>.log
setlocal
for /f "delims=" %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%T"
set "LOG=%~dp0build-%STAMP%.log"
cd /d "%~dp0.."
call .\build.bat > "%LOG%" 2>&1
echo BUILD_EXIT=%ERRORLEVEL% >> "%LOG%"
endlocal
