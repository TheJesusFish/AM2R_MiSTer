[CmdletBinding()]
param(
    # Focused video/scanout checks; does not require the ARM runtime checkout.
    [switch]$VideoOnly
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runId = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
$library = Join-Path $projectRoot "data\build\sim-am2r-video-$runId"
$env:ZIG_GLOBAL_CACHE_DIR = Join-Path $projectRoot 'data\build\zig-global-cache'

function Invoke-VideoSimulation {
    param([string]$Name, [string[]]$Parameters = @(), [string[]]$Libraries = @())
    $label = ($Name + '-' + ($Parameters -join '-')) -replace '[^A-Za-z0-9_-]', '_'
    $log = Join-Path $library "$label.log"
    $output = & vsim -c -l $log -lib $library @Libraries $Name @Parameters -do 'run -all; quit -code 0' 2>&1
    $code = $LASTEXITCODE
    $output | Write-Output
    $text = $output -join "`n"
    # A trailing quit can mask a Verilog $fatal on older ModelSim versions.
    if ($code -ne 0 -or $text -notmatch 'PASS' -or
        $text -match '\*\*\s+(Fatal|Error):|Errors:\s*[1-9]') {
        throw "Video simulation $label failed (exit $code); see $log."
    }
}

Push-Location $projectRoot
try {
    if (-not $VideoOnly) {
    & python tests\renderer\audit_sw_renderer.py
    if ($LASTEXITCODE -ne 0) { throw "renderer operation audit failed with exit code $LASTEXITCODE." }

    & python tests\renderer\test_gpu_sample.py
    if ($LASTEXITCODE -ne 0) { throw "GPU diagnostic sampler regression failed with exit code $LASTEXITCODE." }

    & python tests\renderer\test_subtractive_blend.py
    if ($LASTEXITCODE -ne 0) { throw "native blend regression failed with exit code $LASTEXITCODE." }

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
	if ($topSource.Contains('Video source') -or
		$topSource.Contains('Diagnostic pattern') -or
		$topSource.Contains('.diagnostic(') -or
		$topSource.Contains('.pattern(')) {
		throw 'AM2R.sv must not retain the removed production diagnostic-video controls.'
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
	& python tests\renderer\test_crt_ui_composition.py
	if ($LASTEXITCODE -ne 0) { throw "CRT UI composition regression failed with exit code $LASTEXITCODE." }

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
    } else {
        & vlib $library
        if ($LASTEXITCODE -ne 0) { throw "vlib failed with exit code $LASTEXITCODE." }
    }

    & vlog -sv -work $library rtl\am2r_native_video.sv tests\rtl\am2r_native_video_tb.sv
    if ($LASTEXITCODE -ne 0) { throw "native-video vlog failed with exit code $LASTEXITCODE." }

    foreach ($standard in 0, 1, 2) {
        Invoke-VideoSimulation -Name am2r_native_video_tb -Parameters "-gSTANDARD=$standard"
    }

	& vlog -sv -work $library rtl\am2r_native_video.sv rtl\am2r_crt_resync.sv rtl\am2r_video_line_ram.sv rtl\am2r_video_hscale.sv rtl\am2r_crt_video.sv tests\rtl\am2r_crt_video_tb.sv
	if ($LASTEXITCODE -ne 0) { throw "CRT-video vlog failed with exit code $LASTEXITCODE." }

	Invoke-VideoSimulation -Name am2r_crt_video_tb

	# Core video output (gamma + video_mixer) with the native pulsed pixel
	# enable and the H Scaler's continuous one. Declaration-only framework
	# simulation copies support ModelSim 10.5b; sys/ stays untouched. HQ2x is
	# stubbed and the generated mixer fails if scandoubler output is selected.
	& python tests\rtl\sim\test_video_sim_adapters.py
	if ($LASTEXITCODE -ne 0) { throw "video simulation adapter regression failed." }
	$simMixer = Join-Path $library 'video_mixer_sim.sv'
	& python tests\rtl\sim\make_sim_video_mixer.py $simMixer
	if ($LASTEXITCODE -ne 0) { throw "video_mixer simulation copy failed with exit code $LASTEXITCODE." }
	$simDoubler = Join-Path $library 'scandoubler_sim.sv'
	$simGamma = Join-Path $library 'gamma_corr_sim.sv'
	& vlog -sv -work $library rtl\am2r_native_video.sv rtl\am2r_crt_resync.sv rtl\am2r_video_line_ram.sv rtl\am2r_video_hscale.sv rtl\am2r_crt_video.sv $simMixer $simDoubler tests\rtl\sim\hq2x_stub.sv $simGamma sys\video_freezer.sv rtl\am2r_video_out.sv tests\rtl\am2r_video_out_tb.sv
	if ($LASTEXITCODE -ne 0) { throw "video-output vlog failed with exit code $LASTEXITCODE." }
	foreach ($hscale in 0, 1) {
		foreach ($gamma in 0, 1) {
			Invoke-VideoSimulation -Name am2r_video_out_tb -Parameters @("-gHSCALE=$hscale", "-gGAMMA_EN=$gamma")
		}
	}
	foreach ($scale in -16, 15) {
		Invoke-VideoSimulation -Name am2r_video_out_tb -Parameters @('-gHSCALE=1', '-gGAMMA_EN=1', "-gHSCALE_VALUE=$scale")
	}

	& vlog -sv -work $library rtl\am2r_hdmi_fb.sv tests\rtl\am2r_hdmi_fb_tb.sv
	if ($LASTEXITCODE -ne 0) { throw "HDMI-framebuffer vlog failed with exit code $LASTEXITCODE." }

	Invoke-VideoSimulation -Name am2r_hdmi_fb_tb

	& vlog -sv -work $library rtl\am2r_ddr_arbiter.sv tests\rtl\am2r_ddr_arbiter_tb.sv
	if ($LASTEXITCODE -ne 0) { throw "DDR-arbiter vlog failed with exit code $LASTEXITCODE." }

	Invoke-VideoSimulation -Name am2r_ddr_arbiter_tb

	& vlog -sv -work $library rtl\am2r_native_video.sv rtl\am2r_native_reader.sv tests\rtl\am2r_native_reader_tb.sv
	if ($LASTEXITCODE -ne 0) { throw "native-reader vlog failed with exit code $LASTEXITCODE." }

	Invoke-VideoSimulation -Name am2r_native_reader_tb -Libraries @('-L', 'altera_mf')

	& vlog -sv -work $library rtl\am2r_native_video.sv rtl\am2r_native_reader.sv tests\rtl\am2r_native_reader_late_frame_tb.sv
	if ($LASTEXITCODE -ne 0) { throw "late-frame native-reader vlog failed with exit code $LASTEXITCODE." }

	foreach ($standard in 0, 2) {
		Invoke-VideoSimulation -Name am2r_native_reader_late_frame_tb -Libraries @('-L', 'altera_mf') -Parameters "-gSTANDARD=$standard"
	}
	if ($VideoOnly) {
		Write-Output "PASS: focused video/scanout suite (scandoubler/HQ2x output not covered). Logs: $library"
		return
	}

    & vlog -sv -work $library rtl\am2r_framebuffer_config.sv tests\rtl\am2r_framebuffer_config_tb.sv
    if ($LASTEXITCODE -ne 0) { throw "framebuffer vlog failed with exit code $LASTEXITCODE." }

    & vsim -c -lib $library am2r_framebuffer_config_tb -do 'onerror {quit -code 1}; run -all; quit -code 0'
    if ($LASTEXITCODE -ne 0) { throw "framebuffer vsim failed with exit code $LASTEXITCODE." }

    & vlog -sv -work $library rtl\am2r_gpu.sv tests\rtl\am2r_gpu_blend_pipeline_tb.sv
    if ($LASTEXITCODE -ne 0) { throw "GPU blend pipeline vlog failed with exit code $LASTEXITCODE." }

    Invoke-VideoSimulation 'am2r_gpu_blend_pipeline_tb'

    & vlog -sv -work $library rtl\am2r_gpu.sv tests\rtl\am2r_gpu_tb.sv
    if ($LASTEXITCODE -ne 0) { throw "GPU vlog failed with exit code $LASTEXITCODE." }

    $gpuOutput = & vsim -c -lib $library am2r_gpu_tb -do 'run -all; quit -code 0' 2>&1
    $gpuExitCode = $LASTEXITCODE
    $gpuOutput | Write-Output
    $gpuText = $gpuOutput -join "`n"
    if ($gpuExitCode -ne 0 -or $gpuText -notmatch 'PASS: GPU rendering' -or
        $gpuText -match '\*\*\s+(Fatal|Error):|Errors:\s*[1-9]') {
        throw "GPU simulation failed (exit $gpuExitCode)."
    }

    & vlog -sv -work $library rtl\am2r_gpu.sv tests\rtl\am2r_gpu_surface_tb.sv
    if ($LASTEXITCODE -ne 0) { throw "GPU surface/generic vlog failed with exit code $LASTEXITCODE." }

    $surfaceOutput = & vsim -c -lib $library am2r_gpu_surface_tb -do 'run -all; quit -code 0' 2>&1
    $surfaceExitCode = $LASTEXITCODE
    $surfaceOutput | Write-Output
    # ModelSim can return0 after a Verilog $fatal when a trailing quit command
    # runs. Require both the positive completion marker and an error-free log.
    $surfaceText = $surfaceOutput -join "`n"
    if ($surfaceExitCode -ne 0 -or $surfaceText -notmatch 'PASS surface DMA:' -or
        $surfaceText -match '\*\*\s+(Fatal|Error):|Errors:\s*[1-9]') {
        throw "GPU surface/generic simulation failed (exit $surfaceExitCode)."
    }

    & vlog -sv -work $library rtl\am2r_gpu.sv tests\rtl\am2r_gpu_restart_tb.sv
    if ($LASTEXITCODE -ne 0) { throw "GPU restart vlog failed with exit code $LASTEXITCODE." }
    foreach ($restartSchedule in @(@('-gREAD_LATENCY=2', '-gSTALL_PERIOD=0'), @('-gREAD_LATENCY=5', '-gSTALL_PERIOD=7'))) {
        $restartOutput = & vsim -c -lib $library am2r_gpu_restart_tb @restartSchedule -do 'run -all; quit -code 0' 2>&1
        $restartExitCode = $LASTEXITCODE
        $restartOutput | Write-Output
        $restartText = $restartOutput -join "`n"
        if ($restartExitCode -ne 0 -or $restartText -notmatch 'PASS GPU restart:' -or
            $restartText -match '\*\*\s+(Fatal|Error):|Errors:\s*[1-9]') {
            throw "GPU restart simulation failed (exit $restartExitCode)."
        }
    }
} finally {
    Pop-Location
}
