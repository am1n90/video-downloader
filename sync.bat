@echo off
REM ============================================================
REM  sync.bat - commit and push local changes (sync point)
REM
REM  Two independent questions, both are asked:
REM    dirty tree       -> git add -A, commit "sync: <date-time>";
REM    commits ahead    -> git push origin main.
REM  A clean tree can still be ahead of origin/main (commit made
REM  earlier, push never ran). Looking only at git status reported
REM  "nothing to sync" and left such a commit unpushed (bug 17.09),
REM  so ahead-count is checked too: git rev-list origin/main..HEAD.
REM  Nothing to do only when the tree is clean AND ahead is 0.
REM  Uses only git + cmd built-ins (%%~zA size check; no find:
REM  in Git Bash PATH, GNU find shadows Windows find.exe).
REM  Note: no delayed expansion here, so nothing that is set inside
REM  a parenthesized block may be read inside that same block.
REM  ASCII only (project rule: no-BOM scripts are read as ANSI).
REM ============================================================
setlocal
cd /d "%~dp0"
set "STTMP=%TEMP%\vd-sync-status.tmp"

REM --- 1. uncommitted changes? ---
git status --porcelain > "%STTMP%" 2>&1
if errorlevel 1 (
    echo FAIL: git status failed. Is this a git repository?
    del /q "%STTMP%" 2>nul
    exit /b 1
)
set DIRTY=0
for %%A in ("%STTMP%") do if not "%%~zA"=="0" set DIRTY=1
del /q "%STTMP%" 2>nul

REM --- 2. unpushed commits? ---
set "AHEAD="
git rev-parse --verify --quiet origin/main >nul 2>&1
if errorlevel 1 (
    echo WARN: origin/main is unknown locally - trying to push anyway.
    set "AHEAD=?"
) else (
    for /f %%i in ('git rev-list origin/main..HEAD --count') do set "AHEAD=%%i"
)
if not defined AHEAD (
    echo FAIL: git rev-list failed - cannot tell unpushed commits.
    exit /b 1
)

if "%DIRTY%"=="0" if "%AHEAD%"=="0" (
    echo Working tree is clean and nothing is ahead of origin/main.
    echo Nothing to sync.
    git log -1 --oneline
    exit /b 0
)

if "%DIRTY%"=="1" goto :do_commit

echo Working tree is clean, but %AHEAD% commit(s) are not pushed. Pushing...
goto :do_push

:do_commit
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

:do_push
git push origin main
if errorlevel 1 goto :push_failed

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
goto :eof

:push_failed
echo FAIL: git push failed.
exit /b 1
