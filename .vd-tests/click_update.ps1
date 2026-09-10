# click_update.ps1 — UI-автоматизация: найти окно Video Downloader,
# нажать кнопку «Обновить» в InfoBar, затем подтвердить MessageBox.
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$deadline = (Get-Date).AddSeconds(30)
$root = $null
while ((Get-Date) -lt $deadline) {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, "Video Downloader")
    $win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
    if ($win) { $root = $win; break }
    Start-Sleep -Milliseconds 500
}
if (-not $root) { Write-Output "WINDOW_NOT_FOUND"; exit 1 }
Write-Output "WINDOW_FOUND"

function Find-Button($name) {
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, $name)
    return $root.FindFirst(
        [System.Windows.Automation.TreeScope]::Descendants, $cond)
}

# 1) Кнопка «Обновить» в InfoBar
$btn = $null
$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
    $btn = Find-Button "Обновить"
    if ($btn) { break }
    Start-Sleep -Milliseconds 500
}
if (-not $btn) { Write-Output "BUTTON_UPDATE_NOT_FOUND"; exit 1 }
Write-Output "BUTTON_UPDATE_FOUND"
$invoke = $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
$invoke.Invoke()
Write-Output "UPDATE_CLICKED"

# 2) Подтверждение в MessageBox (кнопка «Да»/«OK»)
Start-Sleep -Seconds 2
$confirmed = $false
foreach ($name in @("Да", "Yes", "OK", "ОК")) {
    $mb = Find-Button $name
    if ($mb) {
        $mb.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
        Write-Output ("CONFIRM_CLICKED: " + $name)
        $confirmed = $true
        break
    }
}
if (-not $confirmed) { Write-Output "CONFIRM_NOT_FOUND" }
