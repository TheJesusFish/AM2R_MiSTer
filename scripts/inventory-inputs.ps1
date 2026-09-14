[CmdletBinding()]
param(
    [string]$InputRoot = (Join-Path $PSScriptRoot '..\data\inputs'),
    [string]$OutputPath = (Join-Path $PSScriptRoot '..\.local\input-manifest.json')
)

$ErrorActionPreference = 'Stop'

$resolvedInputRoot = (Resolve-Path -LiteralPath $InputRoot).Path
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$resolvedOutputPath = [IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $resolvedOutputPath

if (-not (Test-Path -LiteralPath $outputDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $outputDirectory | Out-Null
}

$files = @(
    Get-ChildItem -LiteralPath $resolvedInputRoot -Recurse -File |
        Where-Object { $_.Name -ne 'README.md' } |
        Sort-Object FullName |
        ForEach-Object {
            $relativePath = [IO.Path]::GetRelativePath($projectRoot, $_.FullName).Replace('\', '/')
            [ordered]@{
                path   = $relativePath
                bytes  = $_.Length
                sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
            }
        }
)

[long]$totalBytes = 0
foreach ($file in $files) {
    $totalBytes += [long]$file.bytes
}

$manifest = [ordered]@{
    schema_version = 1
    generated_utc  = [DateTime]::UtcNow.ToString('o')
    file_count     = $files.Count
    total_bytes    = $totalBytes
    files          = $files
}

$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $resolvedOutputPath -Encoding utf8
Write-Host "Wrote $($files.Count) input records to $resolvedOutputPath"
