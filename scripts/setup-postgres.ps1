<#
.SYNOPSIS
  Task Memory Hub PostgreSQL slow-track preparation checklist.

.DESCRIPTION
  This script is intentionally dry-run first. It does not install PostgreSQL,
  create Windows services, create database users, write credentials, or modify
  pg_hba.conf. Use it to inspect local readiness and print the commands that a
  future approved setup may run.

  Keep secrets out of repository files. Do not paste real passwords into this
  script.
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$DatabaseName = "task_memory_hub",
    [string]$AppUser = "tmh_app",
    [string]$SchemaName = "tmh",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 5432,
    [switch]$ShowExampleCommands
)

$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "== $Title =="
}

function Find-CommandPath {
    param([string]$Name)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $cmd) {
        return $null
    }
    return $cmd.Source
}

Write-Section "Task Memory Hub PostgreSQL slow-track dry run"
Write-Host "No installation or credential write will be performed."
Write-Host "DatabaseName: $DatabaseName"
Write-Host "AppUser:      $AppUser"
Write-Host "SchemaName:   $SchemaName"
Write-Host "Host:         $HostName"
Write-Host "Port:         $Port"

Write-Section "Local command availability"
$tools = @("psql", "pg_dump", "pg_restore", "pg_ctl", "initdb", "winget")
foreach ($tool in $tools) {
    $path = Find-CommandPath $tool
    if ($path) {
        Write-Host ("[found]   {0}: {1}" -f $tool, $path)
    } else {
        Write-Host ("[missing] {0}" -f $tool)
    }
}

Write-Section "PostgreSQL Windows services"
$services = Get-Service -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "*postgres*" -or $_.DisplayName -like "*PostgreSQL*" } |
    Sort-Object Name

if ($services) {
    foreach ($svc in $services) {
        Write-Host ("[service] {0} ({1}) - {2}" -f $svc.Name, $svc.DisplayName, $svc.Status)
    }
} else {
    Write-Host "[info] No PostgreSQL-looking Windows service found."
}

Write-Section "Environment variable plan"
Write-Host "Current TMH_DATABASE_URL:"
if ($env:TMH_DATABASE_URL) {
    $redacted = $env:TMH_DATABASE_URL -replace "(postgresql://[^:/?]+:)[^@]+@", '$1***@'
    Write-Host $redacted
} else {
    Write-Host "[unset]"
}

Write-Host ""
Write-Host "Example local PostgreSQL URL without embedded password:"
Write-Host ('  postgresql://{0}@{1}:{2}/{3}?sslmode=disable' -f $AppUser, $HostName, $Port, $DatabaseName)

Write-Section "Pre-install checklist"
$checklist = @(
    "SQLite CLI/API/MCP/worker smoke tests are still passing.",
    "SQLite export or backup path is documented.",
    "Repository abstraction is planned before adding a Postgres adapter.",
    "TMH_DATABASE_URL config resolver is implemented or scheduled.",
    "SQLite and PostgreSQL migrations are separated by backend.",
    "No password, webhook URL, or API key will be committed to the repository.",
    "The app user will not connect as PostgreSQL superuser.",
    "pg_hba.conf will not use trust authentication for app access.",
    "Backup and restore commands will be tested before cutover."
)

foreach ($item in $checklist) {
    Write-Host "[ ] $item"
}

if ($ShowExampleCommands) {
    Write-Section "Example commands for a future approved setup"
    Write-Host "These are examples only. Review paths, version, auth method, and policy before running."
    Write-Host ""
    Write-Host "# Install candidate discovery:"
    Write-Host "winget search PostgreSQL"
    Write-Host ""
    Write-Host "# Example psql administration session after PostgreSQL is already installed:"
    Write-Host "psql -h $HostName -p $Port -U postgres"
    Write-Host ""
    Write-Host "# Example SQL to adapt manually inside psql. Do not store real passwords in files:"
    Write-Host "CREATE DATABASE $DatabaseName;"
    Write-Host "CREATE ROLE $AppUser LOGIN;"
    Write-Host "\c $DatabaseName"
    Write-Host "CREATE SCHEMA IF NOT EXISTS $SchemaName AUTHORIZATION $AppUser;"
    Write-Host "GRANT USAGE, CREATE ON SCHEMA $SchemaName TO $AppUser;"
    Write-Host ""
    Write-Host "# Example backup commands after a database exists:"
    Write-Host "pg_dump -Fc -d $DatabaseName -f .tmh/backups/$DatabaseName.dump"
    Write-Host "pg_restore --list .tmh/backups/$DatabaseName.dump"
}

Write-Section "Result"
Write-Host "Dry run complete. No PostgreSQL installation or database changes were made."
