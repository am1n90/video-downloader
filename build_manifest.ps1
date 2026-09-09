# build_manifest.ps1 — генерация Output\latest.json для автообновления
# Использование: build_manifest.ps1 -Version 1.0.0 -Setup Output\VideoDownloader-Setup-1.0.0.exe

param(
    [Parameter(Mandatory=$true)][string]$Version,
    [Parameter(Mandatory=$true)][string]$Setup
)

$ErrorActionPreference = "Stop"
$repo = "am1n90/video-downloader"

$setupItem = Get-Item $Setup
if (-not $setupItem) { throw "Setup not found: $Setup" }

# sha256 установщика
$sha256 = (Get-FileHash -Path $setupItem.FullName -Algorithm SHA256).Hash.ToLower()

# URL asset в релизе (имя файла = имя установщика)
$url = "https://github.com/$repo/releases/download/v$Version/$($setupItem.Name)"

$manifest = [ordered]@{
    version = $Version
    url     = $url
    sha256  = $sha256
    notes   = "Video Downloader $Version"
}

$json = $manifest | ConvertTo-Json
$outPath = Join-Path $PSScriptRoot "Output\latest.json"
[System.IO.File]::WriteAllText($outPath, $json)

Write-Output "latest.json OK: version=$Version"
Write-Output "  url: $url"
Write-Output "  sha256: $sha256"
