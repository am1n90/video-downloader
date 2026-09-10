# set_version.ps1 — подстановка APP_VERSION из config.py в installer.iss
# Заменяет сломанный многострочный powershell -Command в build.bat
# (кавычки/каретки cmd ломали регулярки; здесь их нет).

param(
    [Parameter(Mandatory=$true)][string]$Version
)

$ErrorActionPreference = "Stop"
# Скрипт лежит в корне проекта — installer.iss рядом
$f = Join-Path $PSScriptRoot "installer.iss"
$iss = [IO.File]::ReadAllText($f)

$iss = $iss -replace '#define MyAppVersion "[^"]*"', ('#define MyAppVersion "' + $Version + '"')
$iss = $iss -replace 'OutputBaseFilename=VideoDownloader-Setup-[0-9.]+', ('OutputBaseFilename=VideoDownloader-Setup-' + $Version)

[IO.File]::WriteAllText($f, $iss)

# Контроль: версия реально записана
$check = [IO.File]::ReadAllText($f)
if ($check -notmatch ('#define MyAppVersion "' + $Version + '"')) {
    throw "installer.iss: версия не подставилась"
}
Write-Output ("installer.iss OK: " + $Version)
