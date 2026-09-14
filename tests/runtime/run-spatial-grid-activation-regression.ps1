[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$butterscotch = Join-Path $projectRoot 'third_party\butterscotch'
$zig = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'third_party\zig\zig-x86_64-windows-0.16.0\zig.exe')).Path
$build = Join-Path $projectRoot 'build'
$output = Join-Path $build 'spatial_grid_activation_regression.exe'

$env:ZIG_GLOBAL_CACHE_DIR = Join-Path $build 'zig-global-cache'
$env:ZIG_LOCAL_CACHE_DIR = Join-Path $build 'zig-local-cache'
$env:TEMP = Join-Path $build 'zig-tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null

& $zig cc -std=c23 -DENABLE_WAD14 `
    "-I$(Join-Path $butterscotch 'src')" `
    "-I$(Join-Path $butterscotch 'vendor\stb\ds')" `
    (Join-Path $PSScriptRoot 'spatial_grid_activation_regression.c') `
    (Join-Path $butterscotch 'src\spatial_grid.c') `
    (Join-Path $butterscotch 'src\stb_ds.c') `
    (Join-Path $butterscotch 'src\log.c') `
    -o $output
if ($LASTEXITCODE -ne 0) {
    throw "Regression test build failed with exit code $LASTEXITCODE."
}

& $output
if ($LASTEXITCODE -ne 0) {
    throw "Regression test failed with exit code $LASTEXITCODE."
}
