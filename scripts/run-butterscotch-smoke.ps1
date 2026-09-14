[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Runner,

    [string]$DataWin = (Join-Path $PSScriptRoot '..\data\inputs\windows\data.win'),
    [string]$Playback = (Join-Path $PSScriptRoot '..\tests\am2r-1.1\smoke-inputs.json'),
    [string]$RunId = ([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))
)

$ErrorActionPreference = 'Stop'

$runnerPath = (Resolve-Path -LiteralPath $Runner).Path
$dataPath = (Resolve-Path -LiteralPath $DataWin).Path
$playbackPath = (Resolve-Path -LiteralPath $Playback).Path
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$artifactRoot = Join-Path $projectRoot "data\artifacts\butterscotch-smoke-$RunId"
$saveRoot = Join-Path $projectRoot "data\work\saves\butterscotch-smoke-$RunId"

if ((Test-Path -LiteralPath $artifactRoot) -or (Test-Path -LiteralPath $saveRoot)) {
    throw "RunId '$RunId' already exists. Choose a new RunId; this script never overwrites a prior run."
}

New-Item -ItemType Directory -Path $artifactRoot, $saveRoot | Out-Null

function Invoke-LoggedRunner {
    param(
        [string]$LogName,
        [string[]]$Arguments
    )

    $logPath = Join-Path $artifactRoot $LogName
    & $runnerPath $dataPath @Arguments 2>&1 | Set-Content -LiteralPath $logPath -Encoding utf8
    return [ordered]@{
        exit_code = $LASTEXITCODE
        log       = [IO.Path]::GetRelativePath($projectRoot, $logPath).Replace('\', '/')
        log_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $logPath).Hash
    }
}

$unknownResult = Invoke-LoggedRunner -LogName 'unknown-functions.log' -Arguments @(
    '--print-unknown-functions',
    '--disable-log-colours'
)

$createArguments = @(
    '--headless',
    '--seed', '1',
    '--playback-inputs', $playbackPath,
    '--save-folder', $saveRoot,
    '--disable-log-colours',
    '--screenshot', (Join-Path $artifactRoot 'movement-8000.png'), '--screenshot-at-frame', '8000',
    '--exit-at-frame', '9000'
)
$createResult = Invoke-LoggedRunner -LogName 'persistence-create.log' -Arguments $createArguments

$savePath = Join-Path $saveRoot 'sav1'
$saveBeforeReload = if (Test-Path -LiteralPath $savePath -PathType Leaf) {
    [ordered]@{
        present = $true
        bytes   = (Get-Item -LiteralPath $savePath).Length
        sha256  = (Get-FileHash -Algorithm SHA256 -LiteralPath $savePath).Hash
    }
} else {
    [ordered]@{ present = $false }
}

$reloadResult = Invoke-LoggedRunner -LogName 'persistence-reload.log' -Arguments @(
    '--headless',
    '--seed', '1',
    '--playback-inputs', $playbackPath,
    '--save-folder', $saveRoot,
    '--disable-log-colours',
    '--exit-at-frame', '3300'
)

$saveAfterReload = if (Test-Path -LiteralPath $savePath -PathType Leaf) {
    [ordered]@{
        present = $true
        bytes   = (Get-Item -LiteralPath $savePath).Length
        sha256  = (Get-FileHash -Algorithm SHA256 -LiteralPath $savePath).Hash
    }
} else {
    [ordered]@{ present = $false }
}

$screenshots = @(
    Get-ChildItem -LiteralPath $artifactRoot -Filter '*.png' -File |
        Sort-Object Name |
        ForEach-Object {
            [ordered]@{
                path   = [IO.Path]::GetRelativePath($projectRoot, $_.FullName).Replace('\', '/')
                bytes  = $_.Length
                sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
            }
        }
)

$summary = [ordered]@{
    schema_version     = 1
    run_id             = $RunId
    runner             = [ordered]@{
        path   = [IO.Path]::GetRelativePath($projectRoot, $runnerPath).Replace('\', '/')
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $runnerPath).Hash
    }
    data_win           = [ordered]@{
        path   = [IO.Path]::GetRelativePath($projectRoot, $dataPath).Replace('\', '/')
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $dataPath).Hash
    }
    playback_sha256    = (Get-FileHash -Algorithm SHA256 -LiteralPath $playbackPath).Hash
    unknown_functions  = $unknownResult
    create             = $createResult
    reload             = $reloadResult
    save_before_reload = $saveBeforeReload
    save_after_reload  = $saveAfterReload
    screenshots        = $screenshots
    limitations        = @(
        'Headless mode disables audio and frame pacing.',
        'This scripted path is a smoke test, not the Milestone 1 representative-scene gate.'
    )
}

$summaryPath = Join-Path $artifactRoot 'summary.json'
$summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $summaryPath -Encoding utf8
$summary | ConvertTo-Json -Depth 6

if ($unknownResult.exit_code -ne 0 -or $createResult.exit_code -ne 0 -or $reloadResult.exit_code -ne 0) {
    exit 1
}
