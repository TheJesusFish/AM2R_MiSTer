#!/usr/bin/env python3
"""Production header-only atlas budget/fseek/dispatch regression, no hardware."""
from pathlib import Path
import os, subprocess, tempfile
from test_unified_bridge import extract

ROOT = Path(__file__).resolve().parents[2]

def main():
    source = (ROOT/'third_party/Butterscotch/src/sw_renderer.c').read_text()
    with tempfile.TemporaryDirectory(prefix='am2r-prewarm-') as folder:
        temp = Path(folder)
        (temp/'texture_prewarm_actual.inc').write_text(extract(source, 'swPrewarmTexturePages'))
        binary = temp/'prewarm.exe'
        env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'data/build/zig-global-cache'),
                   ZIG_LOCAL_CACHE_DIR=str(temp/'zig-cache'))
        subprocess.run([str(ROOT/'third_party/zig/zig-x86_64-windows-0.16.0/zig.exe'), 'cc',
                        '-std=gnu99','-O2','-Wall','-Wextra','-Werror','-I',str(temp),
                        str(Path(__file__).with_name('texture_prewarm_regression.c')),
                        '-o',str(binary)], check=True, env=env)
        subprocess.run([str(binary)], check=True)

if __name__ == '__main__': main()
