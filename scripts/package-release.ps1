[CmdletBinding()]
param(
    [string]$RbfPath = (Join-Path $PSScriptRoot '..\output_files\AM2R.rbf'),
    [string]$FrontendPath = (Join-Path $PSScriptRoot '..\data\build\hps-wrapper\MiSTer_AM2R'),
    [string]$RunnerPath = (Join-Path $PSScriptRoot '..\data\build\butterscotch-mister-pipeline-final\butterscotch'),
    [string]$DmtcpDirectory = (Join-Path $PSScriptRoot '..\data\build\dmtcp-package\dmtcp'),
    [string]$ReleaseDirectory = (Join-Path $PSScriptRoot '..\releases')
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$rbf = (Resolve-Path -LiteralPath $RbfPath).Path
$frontend = (Resolve-Path -LiteralPath $FrontendPath).Path
$runner = (Resolve-Path -LiteralPath $RunnerPath).Path
$dmtcp = (Resolve-Path -LiteralPath $DmtcpDirectory).Path
$release = [IO.Path]::GetFullPath($ReleaseDirectory)

foreach ($required in @($rbf, $frontend, $runner)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required release input is missing: $required"
    }
}
if (-not (Test-Path -LiteralPath $dmtcp -PathType Container)) {
    throw "DMTCP runtime directory is missing: $dmtcp"
}
if (-not $release.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar,
                            [StringComparison]::OrdinalIgnoreCase)) {
    throw 'ReleaseDirectory must remain inside the project workspace.'
}

New-Item -ItemType Directory -Force -Path $release | Out-Null
$standaloneRbf = Join-Path $release 'AM2R.rbf'
Copy-Item -LiteralPath $rbf -Destination $standaloneRbf -Force

$stage = Join-Path (Join-Path $projectRoot 'build') ('release-stage-' + [guid]::NewGuid().ToString('N'))
$archivePath = Join-Path $release 'AM2R_MiSTer_runtime.zip'

try {
    $stageOther = Join-Path $stage '_Other'
    $stageGame = Join-Path $stage 'games\am2r'
    $stageBin = Join-Path $stageGame 'bin'
    $stageLicenses = Join-Path $stage 'LICENSES'
    New-Item -ItemType Directory -Force -Path $stageOther, $stageBin, $stageLicenses | Out-Null

    Copy-Item -LiteralPath $rbf -Destination (Join-Path $stageOther 'AM2R.rbf')
    Copy-Item -LiteralPath $frontend -Destination (Join-Path $stage 'MiSTer_AM2R')
    Copy-Item -LiteralPath $runner -Destination (Join-Path $stageBin 'butterscotch')
    Copy-Item -LiteralPath $dmtcp -Destination (Join-Path $stageGame 'dmtcp') -Recurse
    Copy-Item -LiteralPath (Join-Path $projectRoot 'GAME_DATA.md') -Destination (Join-Path $stage 'AM2R_GAME_DATA.txt')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'third_party\Main_MiSTer-upstream\LICENSE') -Destination (Join-Path $stageLicenses 'Main_MiSTer-GPL-3.0.txt')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'third_party\Butterscotch\LICENSE') -Destination (Join-Path $stageLicenses 'Butterscotch-AGPL-3.0.txt')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'third_party\dmtcp-3.2.0-lf\COPYING') -Destination (Join-Path $stageLicenses 'DMTCP-GPL-3.0.txt')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'third_party\dmtcp-3.2.0-lf\COPYING.LESSER') -Destination (Join-Path $stageLicenses 'DMTCP-LGPL-3.0.txt')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'third_party\Template_MiSTer\LICENSE') -Destination (Join-Path $stageLicenses 'Template_MiSTer-GPL-2.0.txt')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'third_party\3s-mister-arm\LICENSE') -Destination (Join-Path $stageLicenses '3s-mister-arm-AGPL-3.0.txt')

    $install = @'
AM2R MiSTer hybrid-core tester package

AM2R game data is not included. You need your own complete, unmodified
Windows copy of AM2R 1.1 extracted into a folder.

1. Extract this entire ZIP into the root of the MiSTer SD card. In MiSTer
   paths, that root is /media/fat.

2. Create an ordinary ZIP named AM2R.zip from your AM2R 1.1 folder. Include
   exactly the 45 paths listed in AM2R_GAME_DATA.txt, with those paths at the
   root of the ZIP. Keep their spelling and capitalization unchanged. Any
   normal ZIP program may be used; compression level does not matter.

   Copy it to /media/fat/games/am2r/AM2R.zip. The 45 files total 87,581,625
   uncompressed bytes. Do not share the generated AM2R.zip.

3. Add this section to /media/fat/MiSTer.ini. If [AM2R] already exists,
   update it instead of adding a duplicate:

   [AM2R]
   main=MiSTer_AM2R

4. Boot MiSTer and launch Other -> AM2R. Persistent state slots are stored in
   /media/fat/savestates/AM2R. Normal game saves are stored in
   /media/fat/saves/AM2R.

The first launch validates and extracts AM2R.zip into a generated
/media/fat/games/am2r/.runtime-cache and can take longer than a warm relaunch.
Later launches reuse that cache while the archive is unchanged. Save states
contain executable memory and only load with the exact runtime build which
created them. Keep at least 256 MiB free for a checkpoint; low-space requests
are refused without replacing the previous slot. Weapon Select+Start exits to
the MiSTer menu.

AM2R.zip and all other proprietary game files are intentionally excluded.
'@
    [IO.File]::WriteAllText((Join-Path $stage 'README_FIRST.txt'), $install,
                            [Text.UTF8Encoding]::new($false))

    $payloadFiles = Get-ChildItem -LiteralPath $stage -Recurse -File | Sort-Object FullName
    $manifest = foreach ($file in $payloadFiles) {
        $relative = [IO.Path]::GetRelativePath($stage, $file.FullName).Replace('\', '/')
        '{0}  {1}' -f (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant(), $relative
    }
    [IO.File]::WriteAllLines((Join-Path $stage 'SHA256SUMS.txt'), $manifest,
                             [Text.UTF8Encoding]::new($false))

    Add-Type -AssemblyName System.IO.Compression
    if (Test-Path -LiteralPath $archivePath -PathType Leaf) {
        Remove-Item -LiteralPath $archivePath
    }
    $archive = [IO.Compression.ZipFile]::Open($archivePath, [IO.Compression.ZipArchiveMode]::Create)
    try {
        $fixedTimestamp = [DateTimeOffset]::new(2026, 9, 6, 0, 0, 0, [TimeSpan]::Zero)
        foreach ($file in (Get-ChildItem -LiteralPath $stage -Recurse -File | Sort-Object FullName)) {
            $relative = [IO.Path]::GetRelativePath($stage, $file.FullName).Replace('\', '/')
            $entry = $archive.CreateEntry($relative, [IO.Compression.CompressionLevel]::Optimal)
            $entry.LastWriteTime = $fixedTimestamp
            $input = [IO.File]::OpenRead($file.FullName)
            $output = $entry.Open()
            try { $input.CopyTo($output) } finally { $output.Dispose(); $input.Dispose() }
        }
    } finally {
        $archive.Dispose()
    }

    $zip = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        if ($zip.Entries.FullName -contains 'games/am2r/AM2R.zip') {
            throw 'Private AM2R.zip unexpectedly entered the release bundle.'
        }
        if ($zip.Entries.FullName -contains 'AM2R_Game_Packager.ps1') {
            throw 'Obsolete end-user PowerShell packager entered the release bundle.'
        }
        if ($zip.Entries.FullName -notcontains 'AM2R_GAME_DATA.txt') {
            throw 'Game-data file list is missing from the release bundle.'
        }
    } finally {
        $zip.Dispose()
    }

    $releaseManifest = @(
        ('{0}  AM2R.rbf' -f (Get-FileHash -Algorithm SHA256 -LiteralPath $standaloneRbf).Hash.ToLowerInvariant())
        ('{0}  AM2R_MiSTer_runtime.zip' -f (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant())
    )
    [IO.File]::WriteAllLines((Join-Path $release 'SHA256SUMS.txt'), $releaseManifest,
                             [Text.UTF8Encoding]::new($false))

    [pscustomobject]@{
        rbf = $standaloneRbf
        rbf_bytes = (Get-Item -LiteralPath $standaloneRbf).Length
        rbf_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $standaloneRbf).Hash
        runtime_zip = $archivePath
        runtime_zip_bytes = (Get-Item -LiteralPath $archivePath).Length
        runtime_zip_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash
        private_game_zip_included = $false
    }
} finally {
    if (Test-Path -LiteralPath $stage -PathType Container) {
        $resolvedStage = (Resolve-Path -LiteralPath $stage).Path
        $buildRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'build')).Path
        if ($resolvedStage.StartsWith($buildRoot + [IO.Path]::DirectorySeparatorChar,
                                      [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedStage -Recurse -Force
        }
    }
}
