@echo off
REM ============================================================
REM  Video Downloader — сборка установщика Windows
REM
REM  Требования:
REM    - Python 3.13 (py launcher: py -3.13)
REM    - Inno Setup 6 (ISCC.exe) в стандартном расположении
REM    - Доступ в сеть (pip-пакеты, ffmpeg ~80 МБ, deno ~40 МБ)
REM
REM  Результат:
REM    Output\VideoDownloader-Setup-1.0.0.exe
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo [1/7] Создание изолированного venv для сборки...
if exist "build-venv" rmdir /s /q "build-venv"
REM Python 3.13 via py launcher: libtorrent has no cp314 wheel
py -3.13 -m venv build-venv || goto :fail
call "build-venv\Scripts\activate.bat" || goto :fail

echo [2/7] Установка зависимостей...
python -m pip install --upgrade pip || goto :fail
pip install -r requirements.txt || goto :fail
pip install -r requirements-dev.txt || goto :fail

echo [3/7] PyInstaller: сборка dist\VideoDownloader...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "VideoDownloader.spec" del /q "VideoDownloader.spec"
pyinstaller --noconsole --onedir --name VideoDownloader ^
  --icon assets\app.ico ^
  --collect-all qfluentwidgets ^
  --collect-all yt_dlp ^
  --collect-all yt_dlp_ejs ^
  --collect-all curl_cffi ^
  --collect-all libtorrent ^
  main.py || goto :fail
if not exist "dist\VideoDownloader\VideoDownloader.exe" (
  echo FAIL: dist\VideoDownloader\VideoDownloader.exe not created
  goto :fail
)

REM curl_cffi must ship with its native binaries, not just Python code
REM (TikTok needs libcurl-impersonate). Verify both landed in dist.
if not exist "dist\VideoDownloader\_internal\curl_cffi\_wrapper.pyd" (
  echo FAIL: curl_cffi\_wrapper.pyd missing in dist
  goto :fail
)
dir /b "dist\VideoDownloader\_internal\curl_cffi.libs\libcurl-impersonate*.dll" >nul 2>&1
if errorlevel 1 (
  echo FAIL: libcurl-impersonate dll missing in curl_cffi.libs
  goto :fail
)
echo OK: curl_cffi collected with native binaries

REM libtorrent is a package whose __init__ IS the compiled module
REM (libtorrent\__init__.cp313-win_amd64.pyd, 13 MB) — without it Torrent
REM mode cannot start at all. Wildcard: the cpXXX tag follows the Python.
dir /b "dist\VideoDownloader\_internal\libtorrent\__init__*.pyd" >nul 2>&1
if errorlevel 1 (
  echo FAIL: libtorrent __init__*.pyd missing in dist
  goto :fail
)
echo OK: libtorrent collected with its native module

echo [4/7] Скачивание ffmpeg (BtbN win64-gpl)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_ffmpeg.ps1" || goto :fail
if not exist "dist\VideoDownloader\ffmpeg.exe" (
  echo FAIL: ffmpeg.exe не на месте после загрузки
  goto :fail
)
if not exist "dist\VideoDownloader\ffprobe.exe" (
  echo FAIL: ffprobe.exe не на месте после загрузки
  goto :fail
)

echo [5/7] Скачивание Deno v2.9.6 (JS-рантайм для yt-dlp)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_deno.ps1" || goto :fail
if not exist "dist\VideoDownloader\deno.exe" (
  echo FAIL: deno.exe не на месте после загрузки
  goto :fail
)

echo [6/7] Тестовый запуск собранного exe (-selftest: авто-выход через 8с)...
set QT_QPA_PLATFORM=windows
"dist\VideoDownloader\VideoDownloader.exe" -selftest
if errorlevel 1 (
  echo FAIL: exe завершился с ошибкой
  goto :fail
)
echo OK: exe запустился и завершился сам (selftest)

echo [7/7] Inno Setup: сборка установщика...
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
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set_version.ps1" -Version %APPVER% || goto :fail
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
