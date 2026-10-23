param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('init','start','start-app','status','client','backup','restore-check')]
    [string]$Action,
    [string]$StateDir,
    [string]$Archive
)
$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $PSCommandPath
$launcher = Join-Path $scriptRoot 'self_hosted_postgres.py'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw 'Setup script is missing.' }
$arguments = @($launcher, $Action)
if ($StateDir) { $arguments += @('--state-dir', $StateDir) }
if ($Archive) { $arguments += @('--archive', $Archive) }
& python @arguments
exit $LASTEXITCODE
