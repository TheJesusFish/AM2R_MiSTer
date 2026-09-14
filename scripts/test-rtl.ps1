[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runId = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
$library = Join-Path $projectRoot "data\build\sim-am2r-video-$runId"
$env:ZIG_GLOBAL_CACHE_DIR = Join-Path $projectRoot 'data\build\zig-global-cache'

Push-Location $projectRoot
try {
    & python tests\renderer\audit_sw_renderer.py
    if ($LASTEXITCODE -ne 0) { throw "renderer operation audit failed with exit code $LASTEXITCODE." }

    & vlib $library
    if ($LASTEXITCODE -ne 0) { throw "vlib failed with exit code $LASTEXITCODE." }

    $axisTest = Join-Path $library 'am2r_axis_coverage_test.exe'
    & scripts\zig-cc-windows.cmd tests\renderer\am2r_axis_coverage_test.c -O2 -o $axisTest
    if ($LASTEXITCODE -ne 0) { throw "axis-coverage compile failed with exit code $LASTEXITCODE." }
    & $axisTest
    if ($LASTEXITCODE -ne 0) { throw "axis-coverage test failed with exit code $LASTEXITCODE." }

    $topSource = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'AM2R.sv')
    if ($topSource -notmatch '(?m)^\s*assign\s+VGA_SCALER\s*=\s*0\s*;') {
        throw 'AM2R.sv must keep VGA on the native core video path.'
    }
	if ($topSource -notmatch '(?m)^\s*assign\s+CLK_VIDEO\s*=\s*clk_video\s*;') {
		throw 'AM2R.sv must use the dedicated PLL framework video clock.'
    }
	if ($topSource -notmatch '(?m)^\s*assign\s+clk_sys\s*=\s*clk_video\s*;') {
		throw 'AM2R.sv must keep the framework and video domains synchronous so pristine sys/osd.v infers block RAM.'
	}
    if (-not $topSource.Contains('"O[7:6],Savestate slot,1,2,3,4;"')) {
        throw 'AM2R.sv must expose the standard Savestate slot OSD label.'
    }
    if (-not $topSource.Contains('"J1,Fire,Jump,Missiles,Walk,Aim Up,Aim Down,Weapon Select,Start,Morph,Save State;"')) {
        throw 'AM2R.sv must expose the requested AM2R gameplay and save-state actions.'
    }
    if (-not $topSource.Contains('"jn,X,A,Y,B,R,L,Select,Start,,,;"')) {
        throw 'AM2R.sv must provide the requested defaults with Morph and Save State unbound.'
    }
    if ($topSource.Contains('CRT safe area') -or $topSource.Contains('.safe_area(')) {
        throw 'AM2R.sv must not expose or drive the removed CRT safe-area scaler.'
    }
	if (-not $topSource.Contains('CRT Adjustments') -or
		-not $topSource.Contains('Analog H Position') -or
		-not $topSource.Contains('Analog V Position') -or
		-not $topSource.Contains('Analog H Scale') -or
		-not $topSource.Contains('CRT UI V Inset')) {
		throw 'AM2R.sv must expose the core-local analog CRT controls.'
	}
	$wrapperSource = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'src\hps-wrapper\am2r_wrapper.cpp')
	$wrapperShm = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'src\hps-wrapper\am2r_joy_shm.h')
	$runnerMister = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'third_party\Butterscotch\src\backends\mister.c')
	if (-not $wrapperSource.Contains('user_io_status_get("[26:24]") * 2u') -or
		-not $wrapperShm.Contains('#define AM2R_JOY_SHM_VERSION 2u') -or
		-not $runnerMister.Contains('#define AM2R_JOY_SHM_VERSION 2u') -or
		-not $wrapperShm.Contains('uint32_t crt_ui_inset;') -or
		-not $runnerMister.Contains('uint32_t crt_ui_inset;')) {
		throw 'CRT UI inset wrapper/runner shared-memory ABI is inconsistent.'
	}

	$crtUiTest = Join-Path $library 'am2r_crt_ui_inset_test.exe'
	& scripts\zig-cc-windows.cmd tests\runtime\crt_ui_inset_regression.c -O2 -o $crtUiTest
	if ($LASTEXITCODE -ne 0) { throw "CRT UI inset compile failed with exit code $LASTEXITCODE." }
	& $crtUiTest
	if ($LASTEXITCODE -ne 0) { throw "CRT UI inset test failed with exit code $LASTEXITCODE." }

    $alsaSource = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'sys\alsa.sv')
	if (-not $alsaSource.Contains('if(len[18:14] && (hurryup < 1)) hurryup <= 1;') -or
		-not $alsaSource.Contains('if(len[18:16] && (hurryup < 2)) hurryup <= 2;') -or
		-not $alsaSource.Contains('if(len[18:17] && (hurryup < 4)) hurryup <= 4;')) {
		throw 'sys/alsa.sv must retain the upstream MiSTer Template thresholds.'
	}

	$templateSys = Join-Path $projectRoot 'third_party\Template_MiSTer\sys'
	if (Test-Path -LiteralPath $templateSys) {
		$frameworkDiff = & git -c core.safecrlf=false diff --no-index --name-only -- $templateSys (Join-Path $projectRoot 'sys') 2>$null
		if ($LASTEXITCODE -notin @(0, 1)) {
			throw "Unable to compare sys/ against the Template checkout (git exit $LASTEXITCODE)."
		}
		if ($frameworkDiff) {
			throw "sys/ differs from the pinned MiSTer Template checkout: $($frameworkDiff -join ', ')"
		}
	}

    $misterBackend = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'third_party\Butterscotch\src\backends\mister.c')
    $requiredButtonMappings = @(
        'pad->buttonDown[2]  |= (misterMask & (1u << 4)) != 0; // Fire'
        'pad->buttonDown[0]  |= (misterMask & (1u << 5)) != 0; // Jump'
        'pad->buttonDown[1]  |= (misterMask & (1u << 6)) != 0; // Missiles'
        'pad->buttonDown[5]  |= (misterMask & (1u << 7)) != 0; // Walk'
        'pad->buttonDown[4]  |= (misterMask & (1u << 8)) != 0; // Aim Up'
        'pad->buttonDown[6]  |= (misterMask & (1u << 9)) != 0; // Aim Down'
        'pad->buttonDown[3]  |= (misterMask & (1u << 10)) != 0; // Weapon Select'
        'pad->buttonDown[9]  |= (misterMask & (1u << 11)) != 0; // Start'
        'pad->buttonDown[7]  |= (misterMask & (1u << 12)) != 0; // Morph'
        'return (misterMask & (1u << 10)) && (misterMask & (1u << 11));'
    )
    foreach ($mapping in $requiredButtonMappings) {
        if (-not $misterBackend.Contains($mapping)) {
            throw "MiSTer controller mapping is missing: $mapping"
        }
    }

    $vmSource = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'third_party\Butterscotch\src\vm.c')
    if (-not $vmSource.Contains('BuiltinFunc builtin = ctx->funcCallCache[i].scriptCodeIndex >= 0')) {
        throw 'Butterscotch must prefer SCPT game scripts over same-named newer builtins.'
    }

    & vlog -sv -work $library rtl\am2r_video_test.sv tests\rtl\am2r_video_test_tb.sv
    if ($LASTEXITCODE -ne 0) { throw "vlog failed with exit code $LASTEXITCODE." }

    & vsim -c -lib $library am2r_video_test_tb -do 'onerror {quit -code 1}; run -all; quit -code 0'
    if ($LASTEXITCODE -ne 0) { throw "vsim failed with exit code $LASTEXITCODE." }

    & vlog -sv -work $library rtl\am2r_native_video.sv tests\rtl\am2r_native_video_tb.sv
    if ($LASTEXITCODE -ne 0) { throw "native-video vlog failed with exit code $LASTEXITCODE." }

    & vsim -c -lib $library am2r_native_video_tb -do 'onerror {quit -code 1}; run -all; quit -code 0'
    if ($LASTEXITCODE -ne 0) { throw "native-video vsim failed with exit code $LASTEXITCODE." }

	& vlog -sv -work $library rtl\am2r_native_video.sv rtl\am2r_crt_resync.sv rtl\am2r_video_line_ram.sv rtl\am2r_video_hscale.sv rtl\am2r_crt_video.sv tests\rtl\am2r_crt_video_tb.sv
	if ($LASTEXITCODE -ne 0) { throw "CRT-video vlog failed with exit code $LASTEXITCODE." }

	& vsim -c -lib $library am2r_crt_video_tb -do 'onerror {quit -code 1}; run -all; quit -code 0'
	if ($LASTEXITCODE -ne 0) { throw "CRT-video vsim failed with exit code $LASTEXITCODE." }

	& vlog -sv -work $library rtl\am2r_ddr_arbiter.sv tests\rtl\am2r_ddr_arbiter_tb.sv
	if ($LASTEXITCODE -ne 0) { throw "DDR-arbiter vlog failed with exit code $LASTEXITCODE." }

	& vsim -c -lib $library am2r_ddr_arbiter_tb -do 'onerror {quit -code 1}; run -all; quit -code 0'
	if ($LASTEXITCODE -ne 0) { throw "DDR-arbiter vsim failed with exit code $LASTEXITCODE." }

	& vlog -sv -work $library rtl\am2r_native_video.sv rtl\am2r_native_reader.sv tests\rtl\am2r_native_reader_tb.sv
	if ($LASTEXITCODE -ne 0) { throw "native-reader vlog failed with exit code $LASTEXITCODE." }

	& vsim -c -L altera_mf -lib $library am2r_native_reader_tb -do 'onerror {quit -code 1}; run -all; quit -code 0'
	if ($LASTEXITCODE -ne 0) { throw "native-reader vsim failed with exit code $LASTEXITCODE." }

	& vlog -sv -work $library rtl\am2r_native_video.sv rtl\am2r_native_reader.sv tests\rtl\am2r_native_reader_late_frame_tb.sv
	if ($LASTEXITCODE -ne 0) { throw "late-frame native-reader vlog failed with exit code $LASTEXITCODE." }

	& vsim -c -L altera_mf -lib $library am2r_native_reader_late_frame_tb -do 'onerror {quit -code 1}; run -all; quit -code 0'
	if ($LASTEXITCODE -ne 0) { throw "late-frame native-reader vsim failed with exit code $LASTEXITCODE." }

    & vlog -sv -work $library rtl\am2r_framebuffer_config.sv tests\rtl\am2r_framebuffer_config_tb.sv
    if ($LASTEXITCODE -ne 0) { throw "framebuffer vlog failed with exit code $LASTEXITCODE." }

    & vsim -c -lib $library am2r_framebuffer_config_tb -do 'onerror {quit -code 1}; run -all; quit -code 0'
    if ($LASTEXITCODE -ne 0) { throw "framebuffer vsim failed with exit code $LASTEXITCODE." }

    & vlog -sv -work $library rtl\am2r_gpu.sv tests\rtl\am2r_gpu_tb.sv
    if ($LASTEXITCODE -ne 0) { throw "GPU vlog failed with exit code $LASTEXITCODE." }

    & vsim -c -lib $library am2r_gpu_tb -do 'onerror {quit -code 1}; run -all; quit -code 0'
    if ($LASTEXITCODE -ne 0) { throw "GPU vsim failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}
