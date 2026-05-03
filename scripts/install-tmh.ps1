param(
    [int]$Port = 8787,
    [string]$GlobalDb = "",
    [string]$WorkerChannel = "toast-fallback",
    [switch]$SkipPackageInstall,
    [switch]$SkipToastInstall,
    [switch]$RequireToast,
    [switch]$RegisterHubStation,
    [switch]$StartNow,
    [switch]$SkipShortcuts,
    [switch]$DesktopShortcuts,
    [string]$ShortcutFolderName = "Task Memory Hub"
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")

function Quote-ShortcutArgument {
    param([string]$Value)
    return '"' + $Value.Replace('"', '""') + '"'
}

function Test-PowerShellCommand {
    param([string]$CommandName)

    $script = "if (Get-Command $CommandName -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
    $result = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $script) `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    return $result.ExitCode -eq 0
}

function Install-BurntToast {
    Write-Host "Checking BurntToast notification module..."
    if (Test-PowerShellCommand "New-BurntToastNotification") {
        Write-Host "BurntToast is already available."
        return $true
    }

    Write-Host "BurntToast is not available. Installing for current user..."
    $installScript = @"
`$ErrorActionPreference = 'Stop'
Install-PackageProvider -Name NuGet -Force -Scope CurrentUser | Out-Null
Install-Module -Name BurntToast -Scope CurrentUser -Force -AllowClobber
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($installScript))
    $result = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded) `
        -Wait `
        -PassThru `
        -WindowStyle Hidden

    if ($result.ExitCode -eq 0 -and (Test-PowerShellCommand "New-BurntToastNotification")) {
        Write-Host "BurntToast installed."
        return $true
    }
    Write-Warning "BurntToast install did not complete. TMH will still use local notification log fallback."
    return $false
}

function New-TmhWindowsShortcut {
    param(
        [string]$Path,
        [string]$TargetPath,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [string]$Description,
        [string]$IconLocation = "",
        [int]$WindowStyle = 1
    )

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $TargetPath
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.Description = $Description
    $shortcut.WindowStyle = $WindowStyle
    if ($IconLocation) {
        $shortcut.IconLocation = $IconLocation
    }
    $shortcut.Save()
}

function New-TmhInternetShortcut {
    param(
        [string]$Path,
        [string]$Url
    )

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    @"
[InternetShortcut]
URL=$Url
"@ | Set-Content -LiteralPath $Path -Encoding ASCII
}

function Install-TmhShortcuts {
    param(
        [int]$Port,
        [string]$GlobalDb,
        [string]$WorkerChannel,
        [string]$ShortcutFolderName,
        [switch]$DesktopShortcuts
    )

    $programsDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
    $shortcutDir = Join-Path $programsDir $ShortcutFolderName
    $desktopDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
    $baseUrl = "http://127.0.0.1:$Port"
    $repoRootText = [string]$RepoRoot

    $tmhTray = Get-Command tmh-tray -ErrorAction SilentlyContinue
    $iconLocation = if ($tmhTray) { "$($tmhTray.Source),0" } else { "powershell.exe,0" }

    $startScript = Join-Path $ScriptDir "start-tmh-hub-station.ps1"
    $statusScript = Join-Path $ScriptDir "status-tmh-hub-station.ps1"
    $stopScript = Join-Path $ScriptDir "stop-tmh-hub-station.ps1"

    $startArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Quote-ShortcutArgument $startScript),
        "-Port", [string]$Port,
        "-WorkerChannel", $WorkerChannel
    )
    if ($GlobalDb) {
        $startArgs += @("-GlobalDb", (Quote-ShortcutArgument $GlobalDb))
    }

    New-TmhWindowsShortcut `
        -Path (Join-Path $shortcutDir "Start TMH Hub Station.lnk") `
        -TargetPath "powershell.exe" `
        -Arguments ($startArgs -join " ") `
        -WorkingDirectory $repoRootText `
        -Description "Start the TMH tray Hub Station, loopback Web UI, and worker dispatcher." `
        -IconLocation $iconLocation `
        -WindowStyle 7

    New-TmhWindowsShortcut `
        -Path (Join-Path $shortcutDir "Hub Station Status.lnk") `
        -TargetPath "powershell.exe" `
        -Arguments ("-NoExit -NoProfile -ExecutionPolicy Bypass -File {0} -Port {1}" -f (Quote-ShortcutArgument $statusScript), $Port) `
        -WorkingDirectory $repoRootText `
        -Description "Show TMH Hub Station startup, process, port, shortcut, and API health status." `
        -IconLocation "powershell.exe,0"

    New-TmhWindowsShortcut `
        -Path (Join-Path $shortcutDir "Stop TMH Hub Station.lnk") `
        -TargetPath "powershell.exe" `
        -Arguments ("-NoProfile -ExecutionPolicy Bypass -File {0}" -f (Quote-ShortcutArgument $stopScript)) `
        -WorkingDirectory $repoRootText `
        -Description "Stop the TMH tray Hub Station process." `
        -IconLocation "powershell.exe,0"

    New-TmhInternetShortcut -Path (Join-Path $shortcutDir "Open TMH Web UI.url") -Url "$baseUrl/"
    New-TmhInternetShortcut -Path (Join-Path $shortcutDir "Quick Add Task.url") -Url "$baseUrl/quick-add"
    New-TmhInternetShortcut -Path (Join-Path $shortcutDir "API Docs.url") -Url "$baseUrl/docs"

    if ($DesktopShortcuts) {
        New-TmhWindowsShortcut `
            -Path (Join-Path $desktopDir "Task Memory Hub Station.lnk") `
            -TargetPath "powershell.exe" `
            -Arguments ($startArgs -join " ") `
            -WorkingDirectory $repoRootText `
            -Description "Start the TMH tray Hub Station." `
            -IconLocation $iconLocation `
            -WindowStyle 7
        New-TmhInternetShortcut -Path (Join-Path $desktopDir "Task Memory Hub Web UI.url") -Url "$baseUrl/"
    }

    return @{
        start_menu = $shortcutDir
        desktop = if ($DesktopShortcuts) { $desktopDir } else { "" }
    }
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found on PATH. Install Python 3.12 or activate the environment that should host TMH."
}

$pythonVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([version]$pythonVersion -lt [version]"3.12") {
    throw "TMH requires Python >= 3.12. Current Python is $pythonVersion."
}

if (-not $SkipPackageInstall) {
    Write-Host "Installing TMH package with tray extras..."
    $editableTarget = "$RepoRoot[tray]"
    python -m pip install -e $editableTarget
}

$requiredCommands = @("tmh", "tmh-web", "tmh-worker", "tmh-mcp", "tmh-tray")
$missingCommands = @($requiredCommands | Where-Object { -not (Get-Command $_ -ErrorAction SilentlyContinue) })
if ($missingCommands.Count -gt 0) {
    throw "Missing TMH command(s): $($missingCommands -join ', '). Check Python Scripts PATH or reinstall with tray extras."
}

$toastReady = $false
if (-not $SkipToastInstall) {
    $toastReady = Install-BurntToast
    if ($RequireToast -and -not $toastReady) {
        throw "BurntToast is required but could not be installed."
    }
} else {
    $toastReady = Test-PowerShellCommand "New-BurntToastNotification"
}

$shortcutResult = $null
if (-not $SkipShortcuts) {
    $shortcutResult = Install-TmhShortcuts `
        -Port $Port `
        -GlobalDb $GlobalDb `
        -WorkerChannel $WorkerChannel `
        -ShortcutFolderName $ShortcutFolderName `
        -DesktopShortcuts:$DesktopShortcuts
}

if ($RegisterHubStation -or $StartNow) {
    $installArgs = @{
        Port = $Port
        WorkerChannel = $WorkerChannel
        SkipEditableInstall = $true
    }
    if ($GlobalDb) {
        $installArgs.GlobalDb = $GlobalDb
    }
    if ($StartNow) {
        $installArgs.StartNow = $true
    }
    & (Join-Path $ScriptDir "install-tmh-hub-station.ps1") @installArgs
}

Write-Host ""
Write-Host "TMH install check completed."
Write-Host "Commands:"
foreach ($command in $requiredCommands) {
    $resolved = Get-Command $command -ErrorAction Stop
    Write-Host "  $command -> $($resolved.Source)"
}
Write-Host "Toast: $toastReady"
if ($shortcutResult) {
    Write-Host "Start Menu shortcuts: $($shortcutResult.start_menu)"
    if ($shortcutResult.desktop) {
        Write-Host "Desktop shortcuts: $($shortcutResult.desktop)"
    }
}
Write-Host "Web UI: http://127.0.0.1:$Port/"
Write-Host "Status: powershell -ExecutionPolicy Bypass -File scripts\status-tmh-hub-station.ps1 -Port $Port"
