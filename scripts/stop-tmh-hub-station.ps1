$ErrorActionPreference = "Stop"

function Get-TmhHubStationProcess {
    $processes = @()
    foreach ($name in @("python.exe", "tmh-tray.exe")) {
        $processes += Get-CimInstance Win32_Process -Filter "Name = '$name'" -ErrorAction SilentlyContinue
    }
    $processes | Where-Object {
        $_.CommandLine -match "tmh-tray|task_memory_hub\.tray"
    }
}

$targets = Get-TmhHubStationProcess
if (-not $targets) {
    Write-Host "TMH Hub Station is not running."
    exit 0
}

foreach ($target in $targets) {
    Write-Host "Stopping PID $($target.ProcessId): $($target.CommandLine)"
    Stop-Process -Id $target.ProcessId -Force
}
