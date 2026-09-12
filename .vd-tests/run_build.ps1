# run_build.ps1 — полностью отсоединяемый запуск run_build.cmd через WMI
# (процесс создаётся службой WMI, наш шелл завершается мгновенно)
$cmd = 'cmd /c ""C:\Claude Projects\video-downloader\.vd-tests\run_build.cmd""'
$result = ([wmiclass]"Win32_Process").Create($cmd)
if ($result.ReturnValue -ne 0) {
    Write-Output ("WMI_CREATE_FAILED: " + $result.ReturnValue)
    exit 1
}
Write-Output ("DETACHED_BUILD_STARTED pid=" + $result.ProcessId)
