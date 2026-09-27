[CmdletBinding()]
param(
    [string]$QuartusSh = 'C:\intelFPGA_lite\17.0\quartus\bin64\quartus_sh.exe'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$quartusPath = (Resolve-Path -LiteralPath $QuartusSh).Path
$provenance = Join-Path $PSScriptRoot 'artifact_provenance.py'
$rbfPath = Join-Path $projectRoot 'output_files\AM2R.rbf'
$token = "$rbfPath.provenance.pending.json"
& python $provenance begin fpga $rbfPath $token
if ($LASTEXITCODE -ne 0) { throw 'FPGA pre-build provenance capture failed.' }

Push-Location $projectRoot
try {
    & $quartusPath --flow compile AM2R
    if ($LASTEXITCODE -ne 0) {
        throw "Quartus compilation failed with exit code $LASTEXITCODE."
    }

    if (-not (Test-Path -LiteralPath $rbfPath -PathType Leaf)) {
        throw 'Quartus returned success but output_files\AM2R.rbf is missing.'
    }

    $timingReport = Get-Content -Raw -LiteralPath 'output_files\AM2R.sta.rpt'
    if ($timingReport.Contains('Timing requirements not met')) {
        throw 'Quartus produced an RBF, but TimeQuest reports unmet timing requirements.'
    }

    & python $provenance seal $token $rbfPath "$rbfPath.provenance.json"
    if ($LASTEXITCODE -ne 0) { throw 'FPGA artifact provenance could not be sealed.' }

    $rbf = Get-Item -LiteralPath $rbfPath
    [pscustomobject]@{
        path   = $rbf.FullName
        bytes  = $rbf.Length
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $rbfPath).Hash
        fit    = (Get-Content -LiteralPath 'output_files\AM2R.fit.summary' -TotalCount 1)
    }
} finally {
    Pop-Location
}
