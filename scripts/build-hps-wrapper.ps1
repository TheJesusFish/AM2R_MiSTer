[CmdletBinding()]
param(
    [string]$MainMiSTerSource = (Join-Path $PSScriptRoot '..\third_party\Main_MiSTer-upstream'),
    [string]$ButterscotchSource = (Join-Path $PSScriptRoot '..\third_party\Butterscotch'),
    [string]$BuildDirectory = (Join-Path $PSScriptRoot '..\data\build\hps-wrapper')
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$source = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'src\hps-wrapper')).Path
$mainMiSTer = (Resolve-Path -LiteralPath $MainMiSTerSource).Path
$mainMiSTerCMake = $mainMiSTer.Replace('\', '/')
$butterscotch = (Resolve-Path -LiteralPath $ButterscotchSource).Path
$butterscotchCMake = $butterscotch.Replace('\', '/')
$cmake = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'third_party\cmake\cmake-4.4.3-windows-x86_64\bin\cmake.exe')).Path
$ninja = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'third_party\ninja\ninja.exe')).Path
$cCompiler = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'scripts\zig-cc-arm-linux.cmd')).Path
$cxxCompiler = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'scripts\zig-cxx-arm-linux.cmd')).Path
$archiver = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'scripts\zig-ar.cmd')).Path
$ranlib = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'scripts\zig-ranlib.cmd')).Path

$actualCommit = (& git -c "safe.directory=$($mainMiSTer.Replace('\', '/'))" -C $mainMiSTer rev-parse HEAD).Trim()
$expectedCommit = '915ca3395aa5a26322007974faa757299a56b856'
if ($actualCommit -ne $expectedCommit) {
    throw "Main_MiSTer checkout is $actualCommit; expected $expectedCommit."
}

$env:ZIG_GLOBAL_CACHE_DIR = Join-Path $projectRoot 'data\build\zig-global-cache'
$env:ZIG_LOCAL_CACHE_DIR = Join-Path $projectRoot 'data\build\zig-local-cache'

& $cmake -S $source -B $BuildDirectory -G Ninja `
    "-DCMAKE_MAKE_PROGRAM=$ninja" `
    '-DCMAKE_SYSTEM_NAME=Linux' `
    '-DCMAKE_SYSTEM_PROCESSOR=armv7' `
    '-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY' `
    "-DCMAKE_C_COMPILER=$cCompiler" `
    "-DCMAKE_CXX_COMPILER=$cxxCompiler" `
    "-DCMAKE_ASM_COMPILER=$cCompiler" `
    "-DCMAKE_AR=$archiver" `
    "-DCMAKE_RANLIB=$ranlib" `
    '-DCMAKE_BUILD_TYPE=Release' `
    "-DMAIN_MISTER_SOURCE=$mainMiSTerCMake" `
    "-DBUTTERSCOTCH_SOURCE=$butterscotchCMake"
if ($LASTEXITCODE -ne 0) { throw "HPS wrapper configure failed with exit code $LASTEXITCODE." }

& $cmake --build $BuildDirectory --parallel 4
if ($LASTEXITCODE -ne 0) { throw "HPS wrapper build failed with exit code $LASTEXITCODE." }

$output = Join-Path $BuildDirectory 'MiSTer_AM2R'
if (-not (Test-Path -LiteralPath $output -PathType Leaf)) {
    throw 'HPS wrapper build returned success but MiSTer_AM2R is missing.'
}

[pscustomobject]@{
    path = (Resolve-Path -LiteralPath $output).Path
    bytes = (Get-Item -LiteralPath $output).Length
    sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $output).Hash
    main_mister_commit = $actualCommit
}
