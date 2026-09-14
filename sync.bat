@echo off
REM ============================================================
REM  sync.bat - commit and push local changes (sync point)
REM
REM  Clean tree  -> report and exit (nothing to do);
REM  dirty tree  -> git add -A, commit "sync: <date-time>",
REM                 push to origin main, show git log -1
REM                 and check main == origin/main.
REM  Uses only git + cmd built-ins (%%~zA size check; no find:
REM  in Git Bash PATH, GNU find shadows Windows find.exe).
REM  ASCII only (project rule: no-BOM scripts are read as ANSI).
REM ============================================================
setlocal
cd /d "%~dp0"
set "STTMP=%TEMP%\vd-sync-status.tmp"

git status --porcelain > "%STTMP%" 2>&1
if errorlevel 1 (
    echo FAIL: git status failed. Is this a git repository?
    exit /b 1
)
set DIRTY=0
for %%A in ("%STTMP%") do if not "%%~zA"=="0" set DIRTY=1
del /q "%STTMP%" 2>nul

if "%DIRTY%"=="0" (
    echo Working tree is clean. Nothing to sync.
    git log -1 --oneline
    exit /b 0
)

echo Working tree has changes. Committing...
git add -A
if errorlevel 1 (
    echo FAIL: git add -A failed.
    exit /b 1
)

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd-HHmm"') do set "STAMP=%%i"
git commit -m "sync: %STAMP%"
if errorlevel 1 (
    echo FAIL: git commit failed.
    exit /b 1
)

git push origin main
if errorlevel 1 (
    echo FAIL: git push failed.
    exit /b 1
)

echo.
git log -1 --oneline
for /f %%i in ('git rev-parse HEAD') do set "L=%%i"
for /f %%i in ('git rev-parse origin/main') do set "R=%%i"
if "%L%"=="%R%" (
    echo OK: main == origin/main [%L%]
) else (
    echo WARN: main differs from origin/main - check git log.
)
endlocal
