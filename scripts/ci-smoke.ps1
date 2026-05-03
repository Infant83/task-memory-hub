param(
    [string]$DbPath = ".tmh\ci-smoke.sqlite",
    [int]$Port = 8798
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (Test-Path -LiteralPath $DbPath) {
    Remove-Item -LiteralPath $DbPath -Force
}

python -m compileall task_memory_hub
python -m task_memory_hub.cli --help | Out-Null
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-tmh.ps1 -SkipPackageInstall -SkipToastInstall -SkipShortcuts | Out-Null

$taskJson = python -m task_memory_hub.cli --db $DbPath add "CI 스모크 테스트" --next "compile, CLI, runner, API docs 확인" --json
$task = $taskJson | ConvertFrom-Json

$runnerJson = python -m task_memory_hub.cli --db $DbPath runner once --task-id $task.task_id --backend dry_run --capability tmh-api --capability tmh-cli --json
$runner = $runnerJson | ConvertFrom-Json
if ($runner.result -ne "completed") {
    throw "runner smoke failed: $($runnerJson)"
}

$server = Start-Process -FilePath python -ArgumentList @("-m", "task_memory_hub.api", "--port", "$Port", "--db", $DbPath) -PassThru -WindowStyle Hidden
try {
    Start-Sleep -Seconds 2
    $base = "http://127.0.0.1:$Port"
    $health = Invoke-WebRequest "$base/health/ready" -UseBasicParsing
    if ($health.StatusCode -ne 200) {
        throw "health check failed: $($health.StatusCode)"
    }
    $docs = Invoke-WebRequest "$base/docs" -UseBasicParsing
    if ($docs.StatusCode -ne 200) {
        throw "docs check failed: $($docs.StatusCode)"
    }
    $openapi = Invoke-RestMethod "$base/openapi.json"
    if ($openapi.openapi -ne "3.0.3") {
        throw "openapi version check failed"
    }
}
finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
}

Write-Host "CI smoke passed for task $($task.task_id)"
