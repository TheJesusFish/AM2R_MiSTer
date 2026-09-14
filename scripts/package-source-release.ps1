param(
    [string]$OutputPath = (Join-Path $PSScriptRoot '..\releases\AM2R_MiSTer_GitHub_Source_v24.zip')
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$buildRoot = Join-Path $projectRoot 'data\build'
$stageBase = Join-Path $buildRoot 'public-source-package'
$stageRoot = Join-Path $stageBase 'AM2R_MiSTer'
$resolvedBuildRoot = [IO.Path]::GetFullPath($buildRoot).TrimEnd('\') + '\'
$resolvedStageBase = [IO.Path]::GetFullPath($stageBase)

if (-not $resolvedStageBase.StartsWith($resolvedBuildRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a staging path outside data/build/: $resolvedStageBase"
}

if (Test-Path -LiteralPath $stageBase) {
    Remove-Item -LiteralPath $stageBase -Recurse -Force
}
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

function Copy-PublicFile {
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [string]$DestinationPath = $RelativePath
    )

    $source = Join-Path $projectRoot $RelativePath
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required public-source file is missing: $RelativePath"
    }
    $destination = Join-Path $stageRoot $DestinationPath
    $parent = Split-Path -Parent $destination
    if ($parent) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Copy-Item -LiteralPath $source -Destination $destination -Force
}

function Copy-PublicTree {
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [string[]]$Exclude = @()
    )

    $sourceRoot = Join-Path $projectRoot $RelativePath
    if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
        throw "Required public-source directory is missing: $RelativePath"
    }

    foreach ($file in Get-ChildItem -LiteralPath $sourceRoot -Recurse -File) {
        $relative = $file.FullName.Substring($projectRoot.Length).TrimStart([char[]]'\/')
        $unixRelative = $relative.Replace('\', '/')
        if ($Exclude -contains $unixRelative) {
            continue
        }
        if ($unixRelative -match '(^|/)__pycache__(/|$)' -or $unixRelative -match '\.pyc$') {
            continue
        }
        Copy-PublicFile -RelativePath $relative
    }
}

$rootFiles = @(
    '.gitattributes',
    '.gitignore',
    'AM2R.qpf',
    'AM2R.qsf',
    'AM2R.sdc',
    'AM2R.sv',
    'clean.bat',
    'CONTRIBUTING.md',
	'COPYRIGHT_REVIEW.md',
    'files.qip',
    'GAME_DATA.md',
    'LICENSE',
    'LICENSES.md',
    'README.md',
    'SECURITY.md'
)

foreach ($file in $rootFiles) {
    Copy-PublicFile -RelativePath $file
}

foreach ($directory in @(
    'LICENSES',
    'patches',
    'references',
    'rtl',
    'src',
    'sys',
    'tests',
    'tools'
)) {
    Copy-PublicTree -RelativePath $directory
}

Copy-PublicTree -RelativePath 'docs' -Exclude @(
    'docs/environment.md',
    'docs/hardware.md',
    'docs/state.md',
    'docs/user-test.md'
)

Copy-PublicTree -RelativePath 'scripts' -Exclude @('scripts/mister_ssh.py')

foreach ($guide in @(
    'releases/README.md',
    'third_party/README.md'
)) {
    Copy-PublicFile -RelativePath $guide
}

$bannedExtensions = @(
    '.a', '.bmp', '.dll', '.dmtcp', '.exe', '.jpg', '.jpeg', '.mkv', '.mp3',
    '.o', '.obj', '.ogg', '.pdb', '.png', '.rbf', '.rgb', '.so', '.wav', '.zip'
)
$bannedNames = @('data.win', 'mister.json')
$textExtensions = @(
    '.bat', '.c', '.cc', '.cfg', '.cmd', '.cpp', '.gdb', '.h', '.ini', '.json',
    '.md', '.ps1', '.py', '.qip', '.qpf', '.qsf', '.sdc', '.sh', '.srf', '.sv',
    '.tcl', '.txt', '.v'
)

# Preserve exact lab details in the private working tree while removing its
# two RFC1918 target addresses from the publication copy.
foreach ($file in Get-ChildItem -LiteralPath $stageRoot -Recurse -File) {
    if ($textExtensions -notcontains $file.Extension.ToLowerInvariant()) {
        continue
    }
    $content = Get-Content -Raw -LiteralPath $file.FullName
    $sanitized = [regex]::Replace(
        $content,
        '\b10\.4\.20\.123\b',
        '[private USB-1 address]'
    )
    $sanitized = [regex]::Replace(
        $sanitized,
        '\b10\.4\.20\.111\b',
        '[private USB-2 address]'
    )
    if ($sanitized -cne $content) {
        [IO.File]::WriteAllText($file.FullName, $sanitized, [Text.UTF8Encoding]::new($false))
    }
}

$publicFiles = @(Get-ChildItem -LiteralPath $stageRoot -Recurse -File -Force | Sort-Object FullName)
foreach ($file in $publicFiles) {
    if ($bannedNames -contains $file.Name.ToLowerInvariant()) {
        throw "Private/game file entered source package: $($file.FullName)"
    }
    if ($bannedExtensions -contains $file.Extension.ToLowerInvariant()) {
        throw "Binary/game extension entered source package: $($file.FullName)"
    }
    if ($file.Length -gt 20MB) {
        throw "Unexpectedly large source file entered package: $($file.FullName)"
    }

    if ($textExtensions -contains $file.Extension.ToLowerInvariant()) {
        $content = Get-Content -Raw -LiteralPath $file.FullName
        if ($content -match 'C:\\Users\\brend' -or
            $content -match '\b10\.4\.20\.(111|123)\b' -or
            $content -match '-----BEGIN [A-Z ]*PRIVATE KEY-----' -or
            $content -match '"password"\s*:\s*"[^"\r\n]+"' -or
            $content -match '\bgh[pousr]_[A-Za-z0-9_]{20,}\b' -or
            $content -match '\bsk-[A-Za-z0-9_-]{20,}\b') {
            throw "Private host path or credential-like text entered source package: $($file.FullName)"
        }
    }
}

$manifestPath = Join-Path $stageRoot 'SHA256SUMS.txt'
$manifestLines = @(
    '# AM2R MiSTer public source package',
    '# SHA-256 values cover every packaged file except this manifest.'
)
foreach ($file in $publicFiles) {
    $relative = $file.FullName.Substring($stageRoot.Length).TrimStart([char[]]'\/').Replace('\', '/')
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
    $manifestLines += "$hash  $relative"
}
[IO.File]::WriteAllLines($manifestPath, $manifestLines, [Text.UTF8Encoding]::new($false))

if ([IO.Path]::IsPathRooted($OutputPath)) {
    $resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
}
else {
    $resolvedOutput = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputPath))
}
$outputParent = Split-Path -Parent $resolvedOutput
New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
if (Test-Path -LiteralPath $resolvedOutput) {
    Remove-Item -LiteralPath $resolvedOutput -Force
}

Add-Type -AssemblyName System.IO.Compression
$archiveStream = [IO.File]::Open($resolvedOutput, [IO.FileMode]::CreateNew)
try {
    $archive = [IO.Compression.ZipArchive]::new(
        $archiveStream,
        [IO.Compression.ZipArchiveMode]::Create,
        $false
    )
    try {
        $fixedTimestamp = [DateTimeOffset]::Parse('2026-09-11T00:00:00Z')
        foreach ($file in Get-ChildItem -LiteralPath $stageRoot -Recurse -File -Force | Sort-Object FullName) {
            $relative = $file.FullName.Substring($stageBase.Length).TrimStart([char[]]'\/').Replace('\', '/')
            $entry = $archive.CreateEntry($relative, [IO.Compression.CompressionLevel]::Optimal)
            $entry.LastWriteTime = $fixedTimestamp
            $inputStream = $file.OpenRead()
            $entryStream = $entry.Open()
            try {
                $inputStream.CopyTo($entryStream)
            }
            finally {
                $entryStream.Dispose()
                $inputStream.Dispose()
            }
        }
    }
    finally {
        $archive.Dispose()
    }
}
finally {
    $archiveStream.Dispose()
}

$archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedOutput).Hash
$archiveSize = (Get-Item -LiteralPath $resolvedOutput).Length
$fileCount = @(Get-ChildItem -LiteralPath $stageRoot -Recurse -File -Force).Count

[PSCustomObject]@{
    Output = $resolvedOutput
    Bytes = $archiveSize
    Files = $fileCount
    SHA256 = $archiveHash
}
