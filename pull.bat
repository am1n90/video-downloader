@echo off
REM ============================================================
REM  pull.bat - fast-forward update from origin main
REM
REM  Uncommitted changes -> stop (protect local work first:
REM  commit, or run sync.bat);
REM  clean tree -> git pull --ff-only, show git log -1.
REM  Uses only git + cmd built-ins (%%~zA size check; no find:
REM  in Git Bash PATH, GNU find shadows Windows find.exe).
REM  ASCII only (project rule: no-BOM scripts are read as ANSI).
REM ============================================================
setlocal
cd /d "%~dp0"
set "PSTMP=%TEMP%\vd-pull-status.tmp"

git status --porcelain > "%PSTMP%" 2>&1
if errorlevel 1 (
    echo FAIL: git status failed. Is this a git repository?
    exit /b 1
)
set DIRTY=0
for %%A in ("%PSTMP%") do if not "%%~zA"=="0" set DIRTY=1
del /q "%PSTMP%" 2>nul

if "%DIRTY%"=="1" (
    echo STOP: uncommitted changes present - pull aborted.
    echo Commit them or run sync.bat first.
    git status --short
    exit /b 1
)

git pull --ff-only origin main
if errorlevel 1 (
    echo FAIL: git pull --ff-only failed (diverged history or network?).
    exit /b 1
)

echo.
git log -1 --oneline
endlocal
