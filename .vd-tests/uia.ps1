# uia.ps1 - UI Automation helper for live checks of the INSTALLED exe
# (the frozen app cannot be driven from inside the test process).
# ASCII only: element names are passed as base64 of UTF-8 text,
# output is JSON written as UTF-8 to -Out.
#
#   -Op dump     : all elements of the process windows (name, type, rect,
#                  enabled, offscreen)
#   -Op invoke   : Invoke (or Toggle/Select) the first element named -Name
#   -Op setvalue : ValuePattern.SetValue(-Value) on the first element named
#                  -Name or, if -Name is empty, of type Edit
param(
    [Parameter(Mandatory = $true)][int]$ProcessId,
    [Parameter(Mandatory = $true)][string]$Op,
    [string]$Name = "",
    [string]$Value = "",
    [string]$Type = "",
    [int]$Nth = 0,
    [Parameter(Mandatory = $true)][string]$Out
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

function FromB64([string]$s) {
    if ($s -eq "") { return "" }
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s))
}

function Write-Result($obj) {
    $json = $obj | ConvertTo-Json -Depth 5 -Compress
    [IO.File]::WriteAllText($Out, $json, (New-Object Text.UTF8Encoding $false))
}

$A = [System.Windows.Automation.AutomationElement]
$T = [System.Windows.Automation.TreeScope]
$pidCond = New-Object System.Windows.Automation.PropertyCondition(
    $A::ProcessIdProperty, $ProcessId)
$windows = $A::RootElement.FindAll($T::Children, $pidCond)

$all = @()
foreach ($w in $windows) {
    $all += $w
    foreach ($e in $w.FindAll($T::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)) {
        $all += $e
    }
}

function Describe($e) {
    $c = $e.Current
    $r = $c.BoundingRectangle
    # hidden elements of the native file dialog have an infinite rect
    if ($r.IsEmpty -or [double]::IsInfinity($r.X) -or [double]::IsInfinity($r.Width)) {
        $r = New-Object System.Windows.Rect(0, 0, 0, 0)
    }
    return [ordered]@{
        name = $c.Name; type = $c.ControlType.ProgrammaticName
        cls = $c.ClassName; aid = $c.AutomationId
        enabled = $c.IsEnabled; offscreen = $c.IsOffscreen
        x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height
    }
}

try {
    $wanted = FromB64 $Name
    $found = @($all | Where-Object {
        ($wanted -eq "" -or $_.Current.Name -eq $wanted) -and
        ($Type -eq "" -or $_.Current.ControlType.ProgrammaticName -eq $Type)
    })
    if ($Op -eq "dump") {
        Write-Result @{ ok = $true; items = @($all | ForEach-Object { Describe $_ }) }
        exit 0
    }
    if ($found.Count -le $Nth) {
        Write-Result @{ ok = $false; error = "not found"; count = $found.Count }
        exit 0
    }
    $el = $found[$Nth]
    if ($Op -eq "select") {
        # Invoke on a Qt tree item does NOT change the selection (seen on
        # the 2.4 screenshot) - use SelectionItemPattern explicitly
        $p = $null
        if ($el.TryGetCurrentPattern(
                [System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$p)) {
            $p.Select()
            Write-Result @{ ok = $true; how = "select"; el = (Describe $el) }
        } else {
            Write-Result @{ ok = $false; error = "no SelectionItem"; el = (Describe $el) }
        }
        exit 0
    }
    if ($Op -eq "invoke") {
        $p = $null
        if ($el.TryGetCurrentPattern(
                [System.Windows.Automation.InvokePattern]::Pattern, [ref]$p)) {
            $p.Invoke(); $how = "invoke"
        } elseif ($el.TryGetCurrentPattern(
                [System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$p)) {
            $p.Select(); $how = "select"
        } elseif ($el.TryGetCurrentPattern(
                [System.Windows.Automation.TogglePattern]::Pattern, [ref]$p)) {
            $p.Toggle(); $how = "toggle"
        } else {
            Write-Result @{ ok = $false; error = "no pattern"; el = (Describe $el) }
            exit 0
        }
        Write-Result @{ ok = $true; how = $how; el = (Describe $el) }
    } elseif ($Op -eq "setvalue") {
        $p = $el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
        $p.SetValue((FromB64 $Value))
        Write-Result @{ ok = $true; value = $p.Current.Value; el = (Describe $el) }
    } elseif ($Op -eq "getvalue") {
        $p = $el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
        Write-Result @{ ok = $true; value = $p.Current.Value; el = (Describe $el) }
    } else {
        Write-Result @{ ok = $false; error = "unknown op" }
    }
} catch {
    Write-Result @{ ok = $false; error = $_.Exception.Message }
}
