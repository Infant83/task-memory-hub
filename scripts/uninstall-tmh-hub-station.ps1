param(
    [string]$TaskName = "TaskMemoryHubStation",
    [switch]$StopNow,
    [string]$ShortcutFolderName = "Task Memory Hub",
    [switch]$KeepShortcuts
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StopScript = Join-Path $ScriptDir "stop-tmh-hub-station.ps1"

if ($StopNow) {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StopScript
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Uninstalled TMH Hub Station startup task: $TaskName"
} else {
    Write-Host "TMH Hub Station scheduled task is not installed."
}

$startupDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
foreach ($path in @(
    (Join-Path $startupDir "$TaskName.vbs"),
    (Join-Path $startupDir "$TaskName.cmd")
)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
        Write-Host "Removed startup launcher: $path"
    }
}

if (-not $KeepShortcuts) {
    $programsDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
    $shortcutDir = Join-Path $programsDir $ShortcutFolderName
    if (Test-Path -LiteralPath $shortcutDir) {
        Remove-Item -LiteralPath $shortcutDir -Recurse -Force
        Write-Host "Removed Start Menu shortcuts: $shortcutDir"
    }

    $desktopDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
    foreach ($path in @(
        (Join-Path $desktopDir "Task Memory Hub Station.lnk"),
        (Join-Path $desktopDir "Task Memory Hub Web UI.url")
    )) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force
            Write-Host "Removed desktop shortcut: $path"
        }
    }
}
