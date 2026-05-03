param(
    [string]$TaskName = "TaskMemoryHubStation",
    [int]$Port = 8787,
    [string]$ShortcutFolderName = "Task Memory Hub"
)

$ErrorActionPreference = "Continue"

function Get-TmhHubStationProcess {
    $processes = @()
    foreach ($name in @("python.exe", "tmh-tray.exe")) {
        $processes += Get-CimInstance Win32_Process -Filter "Name = '$name'" -ErrorAction SilentlyContinue
    }
    $processes | Where-Object {
        $_.CommandLine -match "tmh-tray|task_memory_hub\.tray"
    }
}

Write-Host "== Scheduled Task =="
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $task | Select-Object TaskName,TaskPath,State | Format-List
    Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object LastRunTime,LastTaskResult,NextRunTime,NumberOfMissedRuns | Format-List
} else {
    Write-Host "Not installed."
}

Write-Host "== Startup Folder =="
$startupDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
foreach ($path in @(
    (Join-Path $startupDir "$TaskName.vbs"),
    (Join-Path $startupDir "$TaskName.cmd")
)) {
    if (Test-Path -LiteralPath $path) {
        Get-Item -LiteralPath $path | Select-Object FullName,Length,LastWriteTime | Format-List
        Get-Content -LiteralPath $path
    }
}

Write-Host "== Shortcuts =="
$programsDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
$shortcutDir = Join-Path $programsDir $ShortcutFolderName
if (Test-Path -LiteralPath $shortcutDir) {
    Get-ChildItem -LiteralPath $shortcutDir | Select-Object Name,FullName,Length,LastWriteTime | Format-List
} else {
    Write-Host "Start Menu shortcut folder not found: $shortcutDir"
}
$desktopDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
foreach ($path in @(
    (Join-Path $desktopDir "Task Memory Hub Station.lnk"),
    (Join-Path $desktopDir "Task Memory Hub Web UI.url")
)) {
    if (Test-Path -LiteralPath $path) {
        Get-Item -LiteralPath $path | Select-Object Name,FullName,Length,LastWriteTime | Format-List
    }
}

Write-Host "== Process =="
$process = Get-TmhHubStationProcess
if ($process) {
    $process | Select-Object ProcessId,Name,CommandLine | Format-List
} else {
    Write-Host "Not running."
}

Write-Host "== Port =="
$connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
if ($connections) {
    $connections | Select-Object LocalAddress,LocalPort,State,OwningProcess | Format-List
} else {
    Write-Host "No listener on port $Port."
}

Write-Host "== API Health =="
try {
    Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/ready" -TimeoutSec 3 | ConvertTo-Json -Depth 4
} catch {
    Write-Host "Health check failed: $($_.Exception.Message)"
}
