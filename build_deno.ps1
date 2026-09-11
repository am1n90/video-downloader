# build_deno.ps1 - download Deno (JS runtime for yt-dlp) into dist\VideoDownloader
# Version is pinned; checksums are official (denoland/deno v2.9.6) and are
# also hardcoded below: verified against both the .sha256sum file and the
# pinned constants.
# ASCII only: PowerShell 5.1 reads BOM-less files as ANSI.
$ErrorActionPreference = "Stop"

$Version = "2.9.6"
$ZipName = "deno-x86_64-pc-windows-msvc.zip"
$ZipSha256 = "15E5300B0BA3C3695A7621D90160A746EC9E710228CEE639AFA9D580F6E3CD11"
$ExeSha256 = "2FF9493DFA356BE2975F477025EA770088E9E9CB2C83D983236D13561B96B7A6"

$dist = Join-Path $PSScriptRoot "dist\VideoDownloader"
if (-not (Test-Path $dist)) { throw "dist not found: $dist" }

# Cache: if Deno is already in build-deno-cache, just extract
$cache = Join-Path $PSScriptRoot "build-deno-cache"
New-Item -ItemType Directory -Path $cache -Force | Out-Null

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$zip = Join-Path $cache $ZipName
if (-not (Test-Path $zip)) {
    $url = "https://github.com/denoland/deno/releases/download/v$Version/$ZipName"
    Write-Output "Downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
}

# 1) sha256 of the zip: official .sha256sum file + pinned constant
$sumUrl = "https://github.com/denoland/deno/releases/download/v$Version/$ZipName.sha256sum"
$officialHash = $null
try {
    $tmp = Join-Path $cache "$ZipName.sha256sum"
    Invoke-WebRequest -Uri $sumUrl -OutFile $tmp -UseBasicParsing
    $match = [regex]::Match((Get-Content $tmp -Raw), "Hash\s+:\s+([0-9A-Fa-f]{64})")
    if ($match.Success) { $officialHash = $match.Groups[1].Value.ToUpper() }
} catch {
    Write-Output "  (could not download official .sha256sum: $($_.Exception.Message))"
}
if ($officialHash -and ($officialHash -ne $ZipSha256)) {
    throw "Official sum $officialHash does not match pinned $ZipSha256"
}
if (-not $officialHash) {
    Write-Output "  WARNING: official sum unavailable, pinned constant only"
}

$localHash = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToUpper()
if ($localHash -ne $ZipSha256) {
    throw "zip sha256 ($localHash) does not match expected ($ZipSha256)"
}
Write-Output "OK: zip sha256 verified"

# 2) extract
$extract = Join-Path $cache "extracted"
if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
Expand-Archive -Path $zip -DestinationPath $extract -Force

$denoExe = Get-ChildItem -Path $extract -Recurse -Filter "deno.exe" | Select-Object -First 1
if (-not $denoExe) { throw "deno.exe not found in archive" }

# 3) sha256 of deno.exe itself (official, separate from the zip)
$exeHash = (Get-FileHash -Path $denoExe.FullName -Algorithm SHA256).Hash.ToUpper()
if ($exeHash -ne $ExeSha256) {
    throw "deno.exe sha256 ($exeHash) does not match expected ($ExeSha256)"
}
Write-Output "OK: deno.exe sha256 verified"

# 4) version of the binary being placed
$exePath = $denoExe.FullName
$versionOut = (& $exePath --version 2>&1 | Out-String)
if ($versionOut -notmatch "deno $Version") {
    throw "deno --version returned unexpected output: $versionOut"
}
Write-Output "OK: $($versionOut.Trim())"

Copy-Item $denoExe.FullName -Destination (Join-Path $dist "deno.exe") -Force
Write-Output "OK: deno.exe -> dist\VideoDownloader\deno.exe"
