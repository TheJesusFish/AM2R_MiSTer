[CmdletBinding()]
param(
    [ValidateRange(1, 4)]
    [int]$Slot = 4
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$buildDirectory = Join-Path $projectRoot 'data\build'
$helper = Join-Path $buildDirectory 'am2r_state_request'
$compiler = Join-Path $projectRoot 'scripts\zig-cc-arm-static.cmd'
$source = Join-Path $projectRoot 'tools\am2r_state_request.c'
$test = Join-Path $PSScriptRoot 'savestate_hardware_regression.py'

New-Item -ItemType Directory -Force -Path $buildDirectory | Out-Null
& $compiler -std=c17 -Os $source -o $helper
if ($LASTEXITCODE -ne 0) {
    throw "ARM state-request helper build failed with exit code $LASTEXITCODE."
}

python $test --slot $Slot --helper $helper
if ($LASTEXITCODE -ne 0) {
    throw "USB-1 save-state regression failed with exit code $LASTEXITCODE."
}
