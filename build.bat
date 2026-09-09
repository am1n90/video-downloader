@echo off
REM ============================================================
REM  Video Downloader — сборка установщика Windows
REM
REM  Требования:
REM    - Python 3.10+ в PATH (для создания venv)
REM    - Inno Setup 6 (ISCC.exe) в стандартном расположении
REM    - Доступ в сеть (pip + скачивание ffmpeg)
REM
REM  Результат:
REM    Output\VideoDownloader-Setup-1.0.0.exe
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo [1/6] Создание изолированного venv для сборки...
if exist "build-venv" rmdir /s /q "build-venv"
python -m venv build-venv || goto :fail
call "build-venv\Scripts\activate.bat" || goto :fail

echo [2/6] Установка зависимостей...
python -m pip install --upgrade pip || goto :fail
pip install -r requirements.txt || goto :fail
pip install -r requirements-dev.txt || goto :fail

echo [3/6] PyInstaller: сборка dist\VideoDownloader...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "VideoDownloader.spec" del /q "VideoDownloader.spec"
pyinstaller --noconsole --onedir --name VideoDownloader ^
  --icon assets\app.ico ^
  --collect-all qfluentwidgets ^
  --collect-all yt_dlp ^
  main.py || goto :fail
if not exist "dist\VideoDownloader\VideoDownloader.exe" (
  echo FAIL: dist\VideoDownloader\VideoDownloader.exe не создан
  goto :fail
)

echo [4/6] Скачивание ffmpeg (BtbN win64-gpl)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_ffmpeg.ps1" || goto :fail
if not exist "dist\VideoDownloader\ffmpeg.exe" (
  echo FAIL: ffmpeg.exe не на месте после загрузки
  goto :fail
)
if not exist "dist\VideoDownloader\ffprobe.exe" (
  echo FAIL: ffprobe.exe не на месте после загрузки
  goto :fail
)

echo [5/6] Тестовый запуск собранного exe (10 секунд)...
set QT_QPA_PLATFORM=windows
start "" /wait "dist\VideoDownloader\VideoDownloader.exe" -selftest
timeout /t 10 /nobreak >nul
tasklist /fi "IMAGENAME eq VideoDownloader.exe" 2>nul | find /i "VideoDownloader.exe" >nul
if errorlevel 1 (
  echo WARNING: процесс не найден через 10с - проверьте вручную dist\VideoDownloader\VideoDownloader.exe
) else (
  echo OK: exe запущен и работает
  taskkill /im VideoDownloader.exe /f >nul 2>&1
)

echo [6/6] Inno Setup: сборка установщика...
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo FAIL: ISCC.exe не найден. Установите Inno Setup 6.
  goto :fail
)

REM --- Подстановка APP_VERSION (config.py) в installer.iss ---
for /f "delims=" %%v in ('python -c "import config; print(config.APP_VERSION)"') do set "APPVER=%%v"
if "%APPVER%"=="" (
  echo FAIL: не удалось прочитать APP_VERSION из config.py
  goto :fail
)
powershell -NoProfile -Command ^
  "$f='installer.iss'; $c=[IO.File]::ReadAllText($f);" ^
  "$c=$c -replace '#define MyAppVersion \"[^\"]*\"', '#define MyAppVersion \"%APPVER%\"';" ^
  "$c=$c -replace 'OutputBaseFilename=VideoDownloader-Setup-[0-9.]+', 'OutputBaseFilename=VideoDownloader-Setup-%APPVER%';" ^
  "[IO.File]::WriteAllText($f, $c)"
echo Версия из config.py: %APPVER%

if exist "Output" rmdir /s /q "Output"
"%ISCC%" installer.iss || goto :fail

REM --- latest.json: version, url, sha256 ---
if not exist "Output\VideoDownloader-Setup-%APPVER%.exe" (
  echo FAIL: установщик не создан
  goto :fail
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_manifest.ps1" -Version %APPVER% -Setup "Output\VideoDownloader-Setup-%APPVER%.exe" || goto :fail
if not exist "Output\latest.json" (
  echo FAIL: latest.json не создан
  goto :fail
)

echo.
echo ============================================================
echo  ГОТОВО: Output\VideoDownloader-Setup-%APPVER%.exe
echo  Манифест обновлений: Output\latest.json
echo ============================================================
endlocal
exit /b 0

:fail
echo.
echo СБОРКА ПРОВАЛЕНА на шаге выше.
endlocal
exit /b 1
