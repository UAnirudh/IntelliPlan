param(
    [string]$StateDir = 'C:\IntelliPlan-PostgreSQL'
)

$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'self_hosted_postgres.py'
$statusPath = Join-Path $StateDir 'backup-last-result.txt'
$python = (Get-Command python.exe -ErrorAction Stop).Source

try {
    $output = & $python $launcher backup --state-dir $StateDir 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "PostgreSQL backup launcher exited with code $exitCode."
    }

    $status = @(
        "SUCCESS $(Get-Date -Format o)"
        ($output | ForEach-Object { $_.ToString() })
    ) -join [Environment]::NewLine
    [System.IO.File]::WriteAllText($statusPath, $status + [Environment]::NewLine)
    $output | ForEach-Object { Write-Output $_ }
    exit 0
}
catch {
    $status = "FAILURE $(Get-Date -Format o)" + [Environment]::NewLine + $_.Exception.Message
    [System.IO.File]::WriteAllText($statusPath, $status + [Environment]::NewLine)
    Write-Error $status
    exit 1
}
