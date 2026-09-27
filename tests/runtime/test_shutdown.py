"""Shutdown helper contracts; --arm-output builds the real POSIX fixture."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def source_contracts():
    wrapper = (ROOT / "src/hps-wrapper/am2r_wrapper.cpp").read_text()
    cmake = (ROOT / "src/hps-wrapper/CMakeLists.txt").read_text()
    loop = (ROOT / "third_party/Butterscotch/src/loop.c").read_text()
    backend = (ROOT / "third_party/Butterscotch/src/backends/mister.c").read_text()
    fast = (ROOT / "third_party/Butterscotch/src/am2r_fast_state.c").read_text()
    for name in ("fpga_load_rbf", "app_restart", "reboot"):
        assert name in cmake
    assert 'list(REMOVE_ITEM MAIN_CXX "${FPGA_IO}")' in cmake
    assert 'if (!am2r_before_fpga_reconfigure()) ${FAILURE_RETURN}' in cmake
    assert 'if (am2r_user_io_status_event(opt, value, ex)) return;' in cmake
    status_hook = wrapper.split('extern "C" bool am2r_user_io_status_event', 1)[1].split('namespace {', 1)[0]
    assert 'if (!strncmp(opt, "[0],", 4))' in status_hook
    assert 'if (value == 1) gOsdResetRequested = 1;' in status_hook
    assert 'return true;' in status_hook
    assert 'g_gpu_sequence = g_gpu_control[6];' in backend
    assert 'uint32_t next = g_gpu_sequence + 1;' in backend
    assert 'if (am2rShutdownSignal) shouldWindowClose = true;' in loop
    assert loop.index('am2rShutdownSignalInstall()') < loop.index('am2rStateInit();')
    assert 'if (am2rShutdownSignal) continue;' in loop
    assert fast.index('fsync(fd)') < fast.index('rename(temporary, path)')
    assert 'SIGKILL' not in wrapper
    assert 'if (!gCoreChangeDenied) fpga_load_rbf("menu.rbf");' in wrapper
    assert 'state.freshRestartRequested && !gSignal' in wrapper
    assert 'state.loadSlot >= 0 && !gSignal' in wrapper
    assert 'state.activeRestoreSlot >= 0 && !gSignal' in wrapper


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm-output', type=Path)
    args = parser.parse_args()
    source_contracts()
    zig = ROOT / 'third_party/zig/zig-x86_64-windows-0.16.0/zig.exe'
    compiler = [str(zig), 'c++'] if zig.exists() else [shutil.which('c++') or 'c++']
    if args.arm_output:
        compiler += ['-target', 'arm-linux-gnueabihf.2.30', '-mcpu=cortex_a9']
    with tempfile.TemporaryDirectory(prefix='am2r-shutdown-') as folder:
        folder = Path(folder)
        binary = args.arm_output or folder / ('test.exe' if os.name == 'nt' else 'test')
        env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT / 'data/build/zig-global-cache'),
                   ZIG_LOCAL_CACHE_DIR=str(folder / 'zig-cache'))
        subprocess.run([*compiler, '-std=c++14', '-O2', '-Wall', '-Wextra', '-Werror',
                        str(ROOT / 'tests/runtime/shutdown_regression.cpp'), '-o', str(binary)],
                       env=env, check=True)
        if args.arm_output:
            print('Built ARM real-process shutdown test:', binary)
        else:
            subprocess.run([str(binary)], check=True)
    print('PASS: loader/status/loop/save/sequence source contracts')


if __name__ == '__main__':
    main()
