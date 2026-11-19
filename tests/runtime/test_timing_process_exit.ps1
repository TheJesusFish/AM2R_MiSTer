[CmdletBinding()]
param()

# Regression for Windows PowerShell 5.1 Start-Process exit-code retention.
# Run with both Windows PowerShell and pwsh; no Quartus or hardware access.
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$source = Get-Content -LiteralPath (Join-Path $projectRoot 'scripts/check-fpga-timing.ps1') -Raw
if ($source.IndexOf('$auditHandle = $auditProcess.Handle') -lt 0 -or
    $source.IndexOf('$auditHandle = $auditProcess.Handle') -gt $source.IndexOf('$auditProcess.WaitForExit(1000)') -or
    -not $source.Contains("if (`$null -eq `$auditExit) { throw 'Timing audit process exit code unavailable.' }")) {
    throw 'Production audit must retain the handle before waiting and fail closed on an unavailable exit code.'
}
$run = Join-Path $projectRoot ('data/work/timing-exit-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run | Out-Null
$childShell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
foreach ($code in @(0,2)) {
    $process = Start-Process -FilePath $childShell -ArgumentList @('-NoProfile','-Command',("exit " + $code)) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $run "$code.stdout.txt") -RedirectStandardError (Join-Path $run "$code.stderr.txt")
    $handle = $process.Handle
    if ($handle -eq [IntPtr]::Zero) { throw 'Child process handle unavailable.' }
    $created = $process.StartTime
    if (-not $process.WaitForExit(10000)) { throw 'Child did not exit; timeout is not a passing result.' }
    $process.WaitForExit()
    if ($null -eq $process.ExitCode -or $process.ExitCode -ne $code) { throw "Lost or incorrect actual exit code $code." }
    Write-Output "PASS: actual process exit code $code retained ($($PSVersionTable.PSVersion)); child started $created"
    $process.Dispose()
}
