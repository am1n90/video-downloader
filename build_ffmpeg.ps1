# build_ffmpeg.ps1 — скачивание ffmpeg (BtbN win64-gpl) в dist\VideoDownloader
$ErrorActionPreference = "Stop"

$dist = Join-Path $PSScriptRoot "dist\VideoDownloader"
if (-not (Test-Path $dist)) { throw "dist not found: $dist" }

# Кэш: если ffmpeg уже скачан в build-ffmpeg-cache — просто копируем
$cache = Join-Path $PSScriptRoot "build-ffmpeg-cache"
New-Item -ItemType Directory -Path $cache -Force | Out-Null

# Пробуем актуальные имена релиза BtbN (первое существующее)
$names = @(
    "ffmpeg-n9.0-latest-win64-gpl-9.0.zip",
    "ffmpeg-n8.1-latest-win64-gpl-8.1.zip",
    "ffmpeg-master-latest-win64-gpl.zip"
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$zip = $null
foreach ($name in $names) {
    $candidate = Join-Path $cache $name
    if (Test-Path $candidate) { $zip = $candidate; break }

    $url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/$name"
    try {
        Write-Output "Скачиваю $url"
        Invoke-WebRequest -Uri $url -OutFile $candidate -UseBasicParsing
        if ((Get-Item $candidate).Length -gt 1MB) { $zip = $candidate; break }
        Remove-Item $candidate -Force
    } catch {
        Write-Output "  недоступен ($($_.Exception.Message))"
        if (Test-Path $candidate) { Remove-Item $candidate -Force }
    }
}

if ($null -eq $zip) { throw "Не удалось скачать ffmpeg ни по одному URL" }

$extract = Join-Path $cache "extracted"
if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
Expand-Archive -Path $zip -DestinationPath $extract -Force

$bins = Get-ChildItem -Path $extract -Recurse -Include "ffmpeg.exe", "ffprobe.exe" |
    Where-Object { $_.Name -in @("ffmpeg.exe", "ffprobe.exe") }

if ($bins.Count -lt 2) { throw "ffmpeg.exe/ffprobe.exe не найдены в архиве" }

foreach ($bin in $bins) {
    Copy-Item $bin.FullName -Destination (Join-Path $dist $bin.Name) -Force
    Write-Output "OK: $($bin.Name)"
}
