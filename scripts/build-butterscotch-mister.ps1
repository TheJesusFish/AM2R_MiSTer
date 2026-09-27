[CmdletBinding()]
param(
    [string]$SourceDirectory = (Join-Path $PSScriptRoot '..\third_party\Butterscotch'),
    [string]$BuildDirectory = (Join-Path $PSScriptRoot '..\data\build\butterscotch-mister-pipeline-final'),
    [switch]$KeepSymbols,
    [switch]$RenderDiagnostics
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$source = (Resolve-Path -LiteralPath $SourceDirectory).Path
$cmake = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'third_party\cmake\cmake-4.4.3-windows-x86_64\bin\cmake.exe')).Path
$ninja = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'third_party\ninja\ninja.exe')).Path
$compiler = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'scripts\zig-cc-arm-linux.cmd')).Path
$archiver = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'scripts\zig-ar.cmd')).Path
$ranlib = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'scripts\zig-ranlib.cmd')).Path
$linkerFlags = if ($KeepSymbols) { '' } else { '-s' }
$diagnostics = if ($RenderDiagnostics) { 'ON' } else { 'OFF' }
$provenance = Join-Path $PSScriptRoot 'artifact_provenance.py'
$runnerPath = Join-Path $BuildDirectory 'butterscotch'
$token = "$runnerPath.provenance.pending.json"

if (-not (Test-Path -LiteralPath (Join-Path $source 'src\backends\mister.c'))) {
    throw 'Butterscotch MiSTer patches are not applied. See patches\README.md.'
}

& python $provenance begin runner $runnerPath $token
if ($LASTEXITCODE -ne 0) { throw 'Runner pre-build provenance capture failed.' }

$env:ZIG_GLOBAL_CACHE_DIR = Join-Path $projectRoot 'data\build\zig-global-cache'
$env:ZIG_LOCAL_CACHE_DIR = Join-Path $projectRoot 'data\build\zig-local-cache'

& $cmake -S $source -B $BuildDirectory -G Ninja `
    "-DCMAKE_MAKE_PROGRAM=$ninja" `
    '-DCMAKE_SYSTEM_NAME=Linux' `
    '-DCMAKE_SYSTEM_PROCESSOR=armv7' `
    '-DCMAKE_BUILD_TYPE=Release' `
    "-DCMAKE_EXE_LINKER_FLAGS=$linkerFlags" `
    "-DCMAKE_C_COMPILER=$compiler" `
    "-DCMAKE_AR=$archiver" `
    "-DCMAKE_RANLIB=$ranlib" `
    "-DCMAKE_C_COMPILER_AR=$archiver" `
    "-DCMAKE_C_COMPILER_RANLIB=$ranlib" `
    '-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=ON' `
    '-DPLATFORM=cli' `
    '-DBACKEND=mister' `
    "-DMISTER_RENDER_DIAGNOSTICS=$diagnostics" `
    '-DAUDIO_BACKEND=miniaudio' `
    '-DENABLE_WAD14=ON' `
    '-DENABLE_WAD16=OFF' `
    '-DENABLE_WAD17=OFF' `
    '-DENABLE_VM_GML_PROFILER=OFF' `
    '-DENABLE_VM_OPCODE_PROFILER=OFF' `
    '-DENABLE_VM_TRACING=OFF' `
    '-DENABLE_VM_STUB_LOGS=ON'
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed with exit code $LASTEXITCODE." }

& $cmake --build $BuildDirectory --parallel 4
if ($LASTEXITCODE -ne 0) { throw "ARM build failed with exit code $LASTEXITCODE." }

if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
    throw 'Build returned success but the butterscotch executable is missing.'
}

& python $provenance seal $token $runnerPath "$runnerPath.provenance.json"
if ($LASTEXITCODE -ne 0) { throw 'Runner artifact provenance could not be sealed.' }

$runner = Get-Item -LiteralPath $runnerPath
[pscustomobject]@{
    path   = $runner.FullName
    bytes  = $runner.Length
    sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $runnerPath).Hash
}
