param(
    [int]$Port = 8787,
    [string]$GlobalDb = "",
    [string]$WorkerChannel = "toast-fallback"
)

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

$existing = Get-TmhHubStationProcess
if ($existing) {
    $existing | Select-Object ProcessId,Name,CommandLine
    Write-Host "TMH Hub Station already appears to be running."
    exit 0
}

$command = (Get-Command tmh-tray -ErrorAction Stop).Source
$arguments = @(
    "--station",
    "--port", [string]$Port,
    "--worker-channel", $WorkerChannel
)

if ($GlobalDb) {
    $arguments += @("--global-db", $GlobalDb)
}

$process = Start-Process -FilePath $command -ArgumentList $arguments -PassThru -WindowStyle Hidden
Write-Host "Started TMH Hub Station."
Write-Host "PID: $($process.Id)"
Write-Host "URL: http://127.0.0.1:$Port"
