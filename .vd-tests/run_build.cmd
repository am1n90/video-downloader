@echo off
REM Отсоединяемая сборка: лог в .vd-tests\build.log
cd /d "%~dp0.."
call build.bat > ".vd-tests\build.log" 2>&1
echo BUILD_EXIT=%ERRORLEVEL% >> ".vd-tests\build.log"
