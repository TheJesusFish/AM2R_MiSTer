[CmdletBinding()]
param(
    [string]$QuartusSta = 'C:\intelFPGA_lite\17.0\quartus\bin64\quartus_sta.exe',
    [string]$OutputDirectory,
    [ValidateRange(60, 1800)]
    [int]$TimeoutSeconds = 900
)

# Run only after compilation exits. This is a post-fit timing gate, not a
# compiler or provenance sealer. It can be called before artifact_provenance.py
# seal. A stand-alone run does not establish that a stale DB matches the source;
# the normal build wrapper's begin/compile/check/seal chain establishes that.
# Quartus may update db/ delay caches; source, RBF and normal report contents are
# checked for changes. No assignments/report database are saved by our Tcl.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$staPath = (Resolve-Path -LiteralPath $QuartusSta).Path
$tclPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'check-fpga-timing.tcl')).Path
$utf8 = [Text.UTF8Encoding]::new($false)
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $runName = 'timing-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $OutputDirectory = Join-Path $projectRoot "data\build\$runName"
}
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $outputRoot) { throw "Refusing to replace existing timing audit: $outputRoot" }
$reportsRoot = Join-Path $outputRoot 'reports'

function Write-TimingJson([string]$Path, $Value) {
    [IO.File]::WriteAllText($Path, (($Value | ConvertTo-Json -Depth 12) + "`n"), $utf8)
}

function Assert-NoActiveAm2r {
    # Hidden command lines/access failures are not proof that the DB is idle.
    # Other identified projects may continue using Quartus on this host.
    foreach ($item in @(Get-CimInstance Win32_Process -Filter "Name LIKE 'quartus_%'")) {
        if ([string]::IsNullOrWhiteSpace($item.CommandLine)) {
            throw "Cannot identify Quartus PID $($item.ProcessId); run with permission to inspect it."
        }
        if ($item.CommandLine -match '(?i)(?:^|[\s"/\\])AM2R(?:[.\s"/\\]|$)' -or
            $item.CommandLine.IndexOf($projectRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            throw "AM2R Quartus process is still active: PID $($item.ProcessId), $($item.Name)."
        }
    }
}

function Get-ProtectedManifest {
    $items = [Collections.Generic.List[IO.FileInfo]]::new()
    $extensions = @('.qpf', '.qsf', '.sdc', '.sv', '.v', '.vh', '.svh', '.qip', '.tcl', '.srf')
    foreach ($entry in Get-ChildItem -LiteralPath $projectRoot -Force -File) {
        if ($extensions -contains $entry.Extension.ToLowerInvariant()) { $items.Add($entry) }
    }
    foreach ($name in @('rtl', 'sys')) {
        $queue = [Collections.Generic.Queue[string]]::new()
        $queue.Enqueue((Join-Path $projectRoot $name))
        while ($queue.Count) {
            $directory = $queue.Dequeue()
            if ((Get-Item -LiteralPath $directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Linked input directory refused: $directory"
            }
            foreach ($entry in Get-ChildItem -LiteralPath $directory -Force) {
                if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked input refused: $($entry.FullName)" }
                if ($entry.PSIsContainer) { $queue.Enqueue($entry.FullName) }
                else { $items.Add($entry) }
            }
        }
    }
    # Exclude DB/cache products and new audit subdirectories, but protect every
    # existing normal output/report/provenance file and both checker scripts.
    foreach ($entry in Get-ChildItem -LiteralPath (Join-Path $projectRoot 'output_files') -Force -File) { $items.Add($entry) }
    $items.Add((Get-Item -LiteralPath $tclPath))
    $items.Add((Get-Item -LiteralPath (Join-Path $PSScriptRoot 'check-fpga-timing.ps1')))
    $records = [ordered]@{}
    foreach ($entry in $items | Sort-Object FullName) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked input refused: $($entry.FullName)" }
        $relative = $entry.FullName.Substring($projectRoot.Length + 1)
        $records[$relative] = [ordered]@{
            bytes = $entry.Length
            sha256 = (Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    return $records
}

function Compare-ProtectedManifest($Expected, $Actual) {
    $changes = [Collections.Generic.List[string]]::new()
    foreach ($name in $Expected.Keys) {
        if (-not $Actual.Contains($name)) { $changes.Add("Missing: $name"); continue }
        if ($Expected[$name].bytes -ne $Actual[$name].bytes -or $Expected[$name].sha256 -ne $Actual[$name].sha256) {
            $changes.Add("Changed: $name")
        }
    }
    foreach ($name in $Actual.Keys) { if (-not $Expected.Contains($name)) { $changes.Add("Added: $name") } }
    return @($changes)
}

function Assert-NonemptyReport([string]$Name) {
    $path = Join-Path $reportsRoot $Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -eq 0) {
        throw "Missing or empty timing report: $Name"
    }
}

function Test-TimingTables($Corners, $Rows) {
    if ($Corners.Count -eq 0 -or $Rows.Count -ne 5 * $Corners.Count -or
        @($Corners | Group-Object index | Where-Object Count -ne 1).Count -ne 0 -or
        @($Corners | Group-Object object | Where-Object Count -ne 1).Count -ne 0 -or
        @($Corners | Where-Object selected_by_default -eq '1').Count -eq 0) {
        throw 'Incomplete or duplicate corner/check table.'
    }
    $passed = $true
    foreach ($corner in $Corners) {
        if ($corner.index -notmatch '^[1-9][0-9]*$' -or $corner.selected_by_default -notmatch '^[01]$') {
            throw 'Invalid corner index/default selection flag.'
        }
        foreach ($kind in @('setup', 'hold', 'recovery', 'removal', 'pulse_width')) {
            $check = @($Rows | Where-Object { $_.corner -eq $corner.index -and $_.check -eq $kind })
            if ($check.Count -ne 1) { throw "Missing or duplicate corner $($corner.index)/$kind check." }
            $row = $check[0]
            if ($row.selected_by_default -ne $corner.selected_by_default -or $row.paths_reported -notmatch '^[0-9]+$') {
                throw 'Invalid check count/default selection flag.'
            }
            if ([long]$row.paths_reported -eq 0) { $expected = 'MISSING_CHECK' }
            else {
                if ($row.worst_slack_ns -notmatch '^[+-]?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$') { throw 'Invalid worst slack.' }
                $slack = [double]::Parse($row.worst_slack_ns, [Globalization.CultureInfo]::InvariantCulture)
                if ([double]::IsNaN($slack) -or [double]::IsInfinity($slack)) { throw 'Non-finite worst slack.' }
                $expected = if ($slack -lt 0) { 'FAIL' } else { 'PASS' }
            }
            if ($row.status -ne $expected) { throw 'Timing status disagrees with numeric path/slack evidence.' }
            if ($expected -ne 'PASS') { $passed = $false }
        }
    }
    return $passed
}

Assert-NoActiveAm2r
$fitSummary = Get-Content -LiteralPath (Join-Path $projectRoot 'output_files\AM2R.fit.summary') -Raw
$normalSta = Get-Content -LiteralPath (Join-Path $projectRoot 'output_files\AM2R.sta.rpt') -Raw
$normalSummary = Get-Content -LiteralPath (Join-Path $projectRoot 'output_files\AM2R.sta.summary') -Raw
if ($fitSummary -notmatch '(?m)^Fitter Status\s*:\s*Successful' -or
    $normalSta -notmatch 'TimeQuest Timing Analyzer was successful') {
    throw 'A completed successful fit and completed normal timing analysis are required.'
}
$normalPass = $normalSta -notmatch 'Timing requirements not met' -and
    $normalSummary -notmatch '(?m)^Slack\s*:\s*-' -and
    [regex]::Matches($normalSummary, '(?m)^Slack\s*:\s*[0-9]+\.[0-9]+\s*$').Count -ge 5
$rbfPath = Join-Path $projectRoot 'output_files\AM2R.rbf'
if (-not (Test-Path -LiteralPath $rbfPath -PathType Leaf) -or (Get-Item -LiteralPath $rbfPath).Length -eq 0) { throw 'Completed RBF missing.' }
$before = Get-ProtectedManifest
Assert-NoActiveAm2r
$null = New-Item -ItemType Directory -Path $outputRoot
Write-TimingJson (Join-Path $outputRoot 'protected-inputs-before.json') $before
$startedUtc = [DateTime]::UtcNow
$auditProcess = $null
$auditExit = $null
$auditError = $null
$timedOut = $false
$unchanged = $false
$changes = @()
try {
    $arguments = @('-t', ('"' + $tclPath + '"'), ('"' + $projectRoot + '"'), ('"' + $reportsRoot + '"'))
    $auditProcess = Start-Process -FilePath $staPath -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $outputRoot 'stdout.log') -RedirectStandardError (Join-Path $outputRoot 'stderr.log')
    $auditCreated = $auditProcess.StartTime
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $lastNotice = 0
    while (-not $auditProcess.WaitForExit(1000)) {
        if ($timer.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
            $timedOut = $true
            $identity = Get-CimInstance Win32_Process -Filter "ProcessId=$($auditProcess.Id)"
            if (-not $identity -or $identity.ExecutablePath -ne $staPath -or
                [string]::IsNullOrWhiteSpace($identity.CommandLine) -or
                $identity.CommandLine.IndexOf($reportsRoot, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
                (Get-Process -Id $auditProcess.Id).StartTime -ne $auditCreated) {
                throw 'Timing audit timed out; process identity uncertain, so nothing was terminated.'
            }
            $auditProcess.Kill()
            if (-not $auditProcess.WaitForExit(10000)) { throw 'Identified timing audit did not exit after timeout.' }
            throw "Timing audit exceeded $TimeoutSeconds seconds; only its verified PID was stopped."
        }
        if ($timer.Elapsed.TotalSeconds -ge $lastNotice + 30) {
            $lastNotice = [int]$timer.Elapsed.TotalSeconds
            Write-Output "All-corner timing check active for $lastNotice seconds (PID $($auditProcess.Id))."
        }
    }
    $auditProcess.WaitForExit()
    $auditExit = $auditProcess.ExitCode
} catch {
    $auditError = $_.Exception.Message
} finally {
    try {
        $after = Get-ProtectedManifest
        Write-TimingJson (Join-Path $outputRoot 'protected-inputs-after.json') $after
        $changes = @(Compare-ProtectedManifest $before $after)
        $unchanged = $changes.Count -eq 0
    } catch { $changes = @("Verification incomplete: $($_.Exception.Message)") }
}

$corners = @()
$rows = @()
$complete = $false
$tablePass = $false
$reportError = $null
try {
    foreach ($name in @('metadata.txt', 'operating-conditions.tsv', 'worst-slack.tsv', 'clocks.rpt', 'constraints.rpt', 'ignored-constraints.rpt', 'unconstrained-paths.rpt', 'constraint-checks.rpt')) { Assert-NonemptyReport $name }
    $corners = @(Import-Csv -LiteralPath (Join-Path $reportsRoot 'operating-conditions.tsv') -Delimiter "`t")
    $rows = @(Import-Csv -LiteralPath (Join-Path $reportsRoot 'worst-slack.tsv') -Delimiter "`t")
    $tablePass = Test-TimingTables $corners $rows
    foreach ($corner in $corners) {
        $stem = 'corner-{0:d2}' -f [int]$corner.index
        foreach ($kind in @('setup', 'hold', 'recovery', 'removal', 'pulse_width')) {
            Assert-NonemptyReport ($stem + '-' + $kind.Replace('_', '-') + '.rpt')
        }
        Assert-NonemptyReport "$stem-fmax.rpt"
    }
    if ((Get-Content -LiteralPath (Join-Path $reportsRoot 'metadata.txt') -Raw) -notmatch '(?m)^REPORTS_COMPLETE;') { throw 'Missing audit completion marker.' }
    $complete = $true
} catch { $reportError = $_.Exception.Message }
$checksPass = $complete -and $auditExit -eq 0 -and $tablePass
$constraintFindings = [ordered]@{
    review_status = 'REQUIRED_SEPARATELY'
    no_ignored_constraints_reported = $null
    check_timing_issue_counts = [ordered]@{}
    unconstrained_path_counts = [ordered]@{}
}
if ($complete) {
    $ignoredReport = Get-Content -LiteralPath (Join-Path $reportsRoot 'ignored-constraints.rpt') -Raw
    $constraintFindings.no_ignored_constraints_reported = $ignoredReport -match '(?m)^No constraints were ignored\.\s*$'
    $checkReport = Get-Content -LiteralPath (Join-Path $reportsRoot 'constraint-checks.rpt') -Raw
    foreach ($match in [regex]::Matches($checkReport, '(?m)^;\s*(no_clock|multiple_clock|generated_clock|loops|latches|no_input_delay|no_output_delay|partial_input_delay|partial_output_delay|uncertainty)\s*;\s*([0-9]+)\s*;')) {
        $constraintFindings.check_timing_issue_counts[$match.Groups[1].Value] = [long]$match.Groups[2].Value
    }
    $ucpReport = Get-Content -LiteralPath (Join-Path $reportsRoot 'unconstrained-paths.rpt') -Raw
    foreach ($match in [regex]::Matches($ucpReport, '(?m)^;\s*(Illegal Clocks|Unconstrained [A-Za-z ]+)\s*;\s*([0-9]+)\s*;\s*([0-9]+)\s*;')) {
        $constraintFindings.unconstrained_path_counts[$match.Groups[1].Value.Trim()] = [ordered]@{
            setup = [long]$match.Groups[2].Value; hold = [long]$match.Groups[3].Value
        }
    }
    # Counts are informational, not exemptions. Keep the raw reports as the
    # authority and do not silently treat inherited framework findings as zero.
    Write-Warning 'Timing slack and constraint coverage are separate. Review the ignored/unconstrained/check-timing reports even when all corners pass.'
}
$summary = [ordered]@{
    started_utc = $startedUtc.ToString('o'); completed_utc = [DateTime]::UtcNow.ToString('o')
    project = $projectRoot; report_directory = $reportsRoot
    rbf_sha256 = $before['output_files\AM2R.rbf'].sha256
    normal_timing_pass = $normalPass; all_corner_timing_pass = $checksPass
    reports_complete = $complete; quartus_exit_code = $auditExit; timed_out = $timedOut
    audit_error = $auditError; report_error = $reportError
    protected_inputs_unchanged = $unchanged; protected_input_changes = $changes
    database_cache_changes_permitted = $true
    source_to_fitted_database_provenance = 'Must be established by the enclosing build begin/compile/check/seal workflow.'
    constraint_review = 'REQUIRED_SEPARATELY: inspect ignored-constraints.rpt, unconstrained-paths.rpt and constraint-checks.rpt; timing PASS is not full constraint coverage.'
    constraint_findings = $constraintFindings
    corners = $corners; checks = $rows
}
Write-TimingJson (Join-Path $outputRoot 'summary.json') $summary
Write-Output "Timing evidence: $(Join-Path $outputRoot 'summary.json')"
if (-not $unchanged) { throw 'Timing audit changed protected inputs/reports, or verification failed; inspect summary.json.' }
if ($auditError) { throw $auditError }
if (-not $normalPass -or -not $checksPass) { throw 'Normal or all-corner timing failed/incomplete; inspect summary.json and reports.' }
Write-Output "All-corner timing PASS: $($corners.Count) operating conditions, all five checks each. Constraint review remains separate."
