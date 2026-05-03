param(
    [string]$TaskName = "TaskMemoryHubStation",
    [int]$Port = 8787,
    [string]$GlobalDb = "",
    [string]$WorkerChannel = "toast-fallback",
    [ValidateSet("auto", "scheduled-task", "startup-folder")]
    [string]$InstallMode = "auto",
    [switch]$StartNow,
    [switch]$SkipEditableInstall
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
$StartScript = Join-Path $ScriptDir "start-tmh-hub-station.ps1"

if (-not $SkipEditableInstall -and -not (Get-Command tmh-tray -ErrorAction SilentlyContinue)) {
    Write-Host "tmh-tray was not found. Installing editable package with tray extras..."
    $editableTarget = "$RepoRoot[tray]"
    python -m pip install -e $editableTarget
}

if (-not (Get-Command tmh-tray -ErrorAction SilentlyContinue)) {
    throw "tmh-tray is still not available on PATH. Run: python -m pip install -e `"$RepoRoot[tray]`""
}

$argumentParts = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-WindowStyle", "Hidden",
    "-File", "`"$StartScript`"",
    "-Port", [string]$Port,
    "-WorkerChannel", $WorkerChannel
)

if ($GlobalDb) {
    $argumentParts += @("-GlobalDb", "`"$GlobalDb`"")
}

$installed = $false

if ($InstallMode -ne "startup-folder") {
    try {
        $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ($argumentParts -join " ")
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1)

        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $action `
            -Trigger $trigger `
            -Principal $principal `
            -Settings $settings `
            -Description "Start Task Memory Hub Station tray on user logon." `
            -Force | Out-Null

        Write-Host "Installed TMH Hub Station startup task."
        Write-Host "TaskName: $TaskName"
        Write-Host "Trigger: user logon"
        Write-Host "Action: powershell.exe $($argumentParts -join ' ')"
        $installed = $true
    } catch {
        if ($InstallMode -eq "scheduled-task") {
            throw
        }
        Write-Warning "Scheduled Task registration failed: $($_.Exception.Message)"
        Write-Warning "Falling back to the current user's Startup folder."
    }
}

if (-not $installed) {
    $startupDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
    $startupFile = Join-Path $startupDir "$TaskName.vbs"
    $startupArguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden",
        "-File", "`"$StartScript`"",
        "-Port", [string]$Port,
        "-WorkerChannel", $WorkerChannel
    )
    if ($GlobalDb) {
        $startupArguments += @("-GlobalDb", "`"$GlobalDb`"")
    }
    $startupCommand = "powershell.exe " + ($startupArguments -join " ")
    $escapedCommand = $startupCommand.Replace('"', '""')
    @"
Set shell = CreateObject("WScript.Shell")
shell.Run "$escapedCommand", 0, False
"@ | Set-Content -LiteralPath $startupFile -Encoding ASCII
    Write-Host "Installed TMH Hub Station Startup folder launcher."
    Write-Host "StartupFile: $startupFile"
    Write-Host "Action: $startupCommand"
}

if ($StartNow) {
    $startNowArguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $StartScript,
        "-Port", [string]$Port,
        "-WorkerChannel", $WorkerChannel
    )
    if ($GlobalDb) {
        $startNowArguments += @("-GlobalDb", $GlobalDb)
    }
    powershell.exe @startNowArguments
}
