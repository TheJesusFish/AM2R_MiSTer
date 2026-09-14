[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$GameDirectory,
    [Parameter(Mandatory = $true)][string]$CaptureExecutable,
    [Parameter(Mandatory = $true)][string]$OutputWave,
    [int]$PressDelaySeconds = 42,
    [int]$CaptureSeconds = 52
)

$ErrorActionPreference = 'Stop'
$gameDirectoryPath = (Resolve-Path -LiteralPath $GameDirectory).Path
$gameExecutable = Join-Path $gameDirectoryPath 'AM2R.exe'
$capturePath = (Resolve-Path -LiteralPath $CaptureExecutable).Path
$outputPath = [IO.Path]::GetFullPath($OutputWave)
$captureLog = "$outputPath.capture.log"
if (-not (Test-Path -LiteralPath $gameExecutable -PathType Leaf)) {
    throw "Missing isolated native AM2R executable: $gameExecutable"
}

Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class NativeAudioQaInput {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern void keybd_event(byte key, byte scan, uint flags, UIntPtr extra);
}
'@

$capture = $null
$game = $null
try {
    $capture = Start-Process -FilePath $capturePath `
        -ArgumentList @($outputPath, $CaptureSeconds) -WindowStyle Hidden `
        -RedirectStandardError $captureLog -PassThru
    Start-Sleep -Milliseconds 500
    $game = Start-Process -FilePath $gameExecutable -WorkingDirectory $gameDirectoryPath `
        -WindowStyle Hidden -PassThru

    Start-Sleep -Seconds $PressDelaySeconds
    $game.Refresh()
    $window = $game.MainWindowHandle
    if ($window -eq [IntPtr]::Zero) {
        throw 'Native AM2R did not create a main window.'
    }
    # Old GameMaker polls the foreground keyboard state. Briefly restore the
    # isolated test window, inject Enter, then minimize it again.
    [void][NativeAudioQaInput]::ShowWindowAsync($window, 9)
    [void][NativeAudioQaInput]::SetForegroundWindow($window)
    Start-Sleep -Milliseconds 250
    [NativeAudioQaInput]::keybd_event(0x0D, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 120
    [NativeAudioQaInput]::keybd_event(0x0D, 0, 2, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 250
    [void][NativeAudioQaInput]::ShowWindowAsync($window, 6)

    if (-not $capture.WaitForExit(($CaptureSeconds + 10) * 1000)) {
        throw 'WASAPI capture did not finish on time.'
    }
    if ($capture.ExitCode -ne 0) {
        throw "WASAPI capture failed with exit code $($capture.ExitCode)."
    }
} finally {
    if ($game -and -not $game.HasExited) {
        Stop-Process -Id $game.Id -Force
        $game.WaitForExit()
    }
    if ($capture -and -not $capture.HasExited) {
        Stop-Process -Id $capture.Id -Force
        $capture.WaitForExit()
    }
}

Get-Item -LiteralPath $outputPath
Get-Content -LiteralPath $captureLog
