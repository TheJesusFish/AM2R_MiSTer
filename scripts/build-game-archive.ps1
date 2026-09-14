[CmdletBinding()]
param(
    [string]$SourceDirectory = (Join-Path $PSScriptRoot '..\data\inputs\windows'),
    [string]$OutputPath = (Join-Path $PSScriptRoot '..\data\build\game-data\AM2R.zip')
)

$ErrorActionPreference = 'Stop'

$requiredFiles = @(
    'data.win',
    'lang/english.ini',
    'lang/languages.txt',
    'musAlphaFight.ogg',
    'musAncientGuardian.ogg',
    'musArachnus.ogg',
    'musArea1A.ogg',
    'musArea1B.ogg',
    'musArea2A.ogg',
    'musArea2B.ogg',
    'musArea3A.ogg',
    'musArea3B.ogg',
    'musArea4A.ogg',
    'musArea4B.ogg',
    'musArea5A.ogg',
    'musArea5B.ogg',
    'musArea6A.ogg',
    'musArea7A.ogg',
    'musArea7B.ogg',
    'musArea7C.ogg',
    'musArea7D.ogg',
    'musArea8.ogg',
    'musCaveAmbience.ogg',
    'musCaveAmbienceA4.ogg',
    'musCredits.ogg',
    'musEris.ogg',
    'musFanfare.ogg',
    'musGammaFight.ogg',
    'musGenesis.ogg',
    'musHatchling.ogg',
    'musIntroSeq.ogg',
    'musItemAmb.ogg',
    'musItemGet.ogg',
    'musLabAmbience.ogg',
    'musMainCave.ogg',
    'musMainCave2.ogg',
    'musMetroidAppear.ogg',
    'musOmegaFight.ogg',
    'musQueen.ogg',
    'musQueenIntro.ogg',
    'musReactor.ogg',
    'musTitle.ogg',
    'musTorizoA.ogg',
    'musTorizoB.ogg',
    'musZetaFight.ogg'
)

$source = (Resolve-Path -LiteralPath $SourceDirectory).Path
$output = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = [System.IO.Path]::GetDirectoryName($output)
[System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null

$missing = @($requiredFiles | Where-Object {
    -not (Test-Path -LiteralPath (Join-Path $source $_) -PathType Leaf)
})
if ($missing.Count) {
    throw "The game folder is missing required archive members: $($missing -join ', ')"
}

$expectedDataHash = '36E4A251D7B687F2D742A8E911CB1E1185AEA99E36529FCF32CD18D445A355E3'
$actualDataHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $source 'data.win')).Hash
if ($actualDataHash -ne $expectedDataHash) {
    throw "Unsupported data.win SHA-256 $actualDataHash; this build is validated for AM2R 1.1 ($expectedDataHash)."
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$temporary = "$output.new"
if (Test-Path -LiteralPath $temporary) {
    Remove-Item -LiteralPath $temporary -Force
}

$stream = [System.IO.File]::Open($temporary, [System.IO.FileMode]::CreateNew)
try {
    $zip = [System.IO.Compression.ZipArchive]::new(
        $stream,
        [System.IO.Compression.ZipArchiveMode]::Create,
        $false
    )
    try {
        foreach ($relative in $requiredFiles) {
            $input = Join-Path $source $relative
            $entryName = $relative.Replace('\', '/')
            $entry = $zip.CreateEntry($entryName, [System.IO.Compression.CompressionLevel]::Optimal)
            $entry.LastWriteTime = [DateTimeOffset]::new(2026, 9, 6, 0, 0, 0, [TimeSpan]::Zero)
            $entryStream = $entry.Open()
            try {
                $inputStream = [System.IO.File]::OpenRead($input)
                try { $inputStream.CopyTo($entryStream) }
                finally { $inputStream.Dispose() }
            }
            finally { $entryStream.Dispose() }
        }
    }
    finally { $zip.Dispose() }
}
finally { $stream.Dispose() }

Move-Item -LiteralPath $temporary -Destination $output -Force

$archive = [System.IO.Compression.ZipFile]::OpenRead($output)
try {
    $entryCount = $archive.Entries.Count
    $uncompressedBytes = ($archive.Entries | Measure-Object -Property Length -Sum).Sum
}
finally { $archive.Dispose() }

if ($entryCount -ne $requiredFiles.Count -or $uncompressedBytes -ne 87581625) {
    throw "Archive verification failed: entries=$entryCount bytes=$uncompressedBytes"
}

[pscustomobject]@{
    path = $output
    entries = $entryCount
    uncompressed_bytes = $uncompressedBytes
    archive_bytes = (Get-Item -LiteralPath $output).Length
    sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $output).Hash
}
