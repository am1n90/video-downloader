# start_update_test.ps1 — подготовка и отсоединённый запуск регресса обновления
# 1) поднимает локальный сервер манифеста (localhost:8765)
# 2) добавляет update_manifest_url в настройки УСТАНОВЛЕННОГО 1.0.0
# 3) запускает установленное приложение отсоединённо (WMI)
$ErrorActionPreference = "Stop"
$wd = "E:\OpenCode Project\video-downloader"

# 1) сервер манифеста: отсоединённо
$py = Join-Path $wd "build-venv\Scripts\python.exe"
$srv = ([wmiclass]"Win32_Process").Create("`"$py`" `"$wd\.vd-tests\update_server.py`" --port 8765")
Write-Output ("SERVER_PID=" + $srv.ProcessId)
Start-Sleep -Seconds 2

# 2) настройки установленного приложения: точечная правка JSON
$sf = Join-Path $env:LOCALAPPDATA "VideoDownloader\settings.json"
$json = Get-Content $sf -Raw -Encoding UTF8 | ConvertFrom-Json
$manifestUrl = "http://127.0.0.1:8765/latest.json"
if ($json.PSObject.Properties["update_manifest_url"]) {
    $json.update_manifest_url = $manifestUrl
} else {
    $json | Add-Member -NotePropertyName update_manifest_url -NotePropertyValue $manifestUrl
}
$json | ConvertTo-Json -Depth 10 | Set-Content $sf -Encoding UTF8
Write-Output "SETTINGS_PATCHED"

# 3) запуск установленного 1.0.0
$exe = Join-Path $env:LOCALAPPDATA "Programs\VideoDownloader\VideoDownloader.exe"
$app = ([wmiclass]"Win32_Process").Create("`"$exe`"")
Write-Output ("APP_PID=" + $app.ProcessId)
