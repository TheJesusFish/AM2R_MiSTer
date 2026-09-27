"""Compile the real collector and check its machine-readable contract."""
import csv
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RenderDiagnosticsTest(unittest.TestCase):
    def test_collector(self):
        zig = ROOT / 'third_party/zig/zig-x86_64-windows-0.16.0/zig.exe'
        cc = [str(zig), 'cc'] if zig.exists() else [shutil.which('cc') or 'cc']
        with tempfile.TemporaryDirectory(prefix='am2r-render-diag-') as folder:
            folder = Path(folder)
            binary = folder / ('test.exe' if os.name == 'nt' else 'test')
            output = folder / 'timing.csv'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT / 'data/build/zig-global-cache'),
                       ZIG_LOCAL_CACHE_DIR=str(folder / 'zig-cache'))
            subprocess.run([*cc, '-std=c23', '-O2', '-DMISTER_RENDER_DIAGNOSTICS',
                            str(ROOT / 'tests/runtime/render_diagnostics_test.c'), '-o', str(binary)],
                           env=env, check=True)
            subprocess.run([str(binary), str(output)], check=True)
            with output.open(newline='') as stream:
                rows = list(csv.DictReader(stream))
            first = {r['operation']: r for r in rows if r['frame'] == '101'}
            self.assertEqual(first['upload']['calls'], '1')
            self.assertEqual(first['upload']['uploaded_bytes'], '100')
            self.assertEqual(first['user_axis']['pixels'], '1024')
            self.assertEqual(first['subtract_scaled']['pixels'], '1024')
            self.assertEqual(first['capture_perturbation']['calls'], '1')
            for name in ('flush', 'fence', 'batch', 'spill', 'source', 'release',
                         'copy', 'readback', 'select', 'cpu_write', 'register', 'queue_flush'):
                row = first['surface_' + name]
                self.assertEqual(row['calls'], '1')
                self.assertEqual(row['surface_id'], str(0x123405))
                self.assertEqual(row['pixels'], '131072')
            self.assertTrue(all(r['room'] == '160' for r in rows))
            self.assertTrue(all(r['dropped_scopes'] == '0' for r in first.values()))
            second = [r for r in rows if r['frame'] == '102']
            self.assertEqual(len(second), 64)
            self.assertTrue(all(r['dropped_scopes'] == '17' for r in second))
            self.assertEqual(sum(r['operation'] == 'cpu_work' for r in second), 1)

    def test_release_macros_have_no_side_effects(self):
        zig = ROOT / 'third_party/zig/zig-x86_64-windows-0.16.0/zig.exe'
        cc = [str(zig), 'cc'] if zig.exists() else [shutil.which('cc') or 'cc']
        with tempfile.TemporaryDirectory(prefix='am2r-release-diag-') as folder:
            folder = Path(folder)
            source = folder / 'release.c'
            source.write_text('#include "render_diagnostics.h"\nint main(void){'
                              'RD_SCOPE(RD_UPLOAD,missing_symbol(),missing_symbol());'
                              'RD_SCOPE(RD_SURFACE_COPY,missing_symbol(),missing_symbol());'
                              'RD_UPLOAD_BYTES(missing_symbol());'
                              'RD_beginFrame(missing_symbol(),missing_symbol(),missing_symbol());'
                              'RD_endFrame();RD_capturePerturbation();return 0;}\n')
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT / 'data/build/zig-global-cache'),
                       ZIG_LOCAL_CACHE_DIR=str(folder / 'zig-cache'))
            subprocess.run([*cc, '-std=c23', '-Werror', '-O2', '-I',
                            str(ROOT / 'third_party/Butterscotch/src'), str(source),
                            '-o', str(folder / 'release.exe')], env=env, check=True)


if __name__ == '__main__':
    unittest.main()
