#!/usr/bin/env python3
"""Bounded caller/removal tracing: actual helper, no game assets or hardware."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
SRC=ROOT / "third_party/Butterscotch/src"

class SurfaceLifetime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory(prefix="am2r-surface-lifetime-")
        cls.temp=Path(cls.directory.name)
        cls.source=(SRC/"vm_builtins.c").read_text()
        begin=cls.source.index("// SURFACE_LIFETIME_DIAGNOSTICS_BEGIN")
        end=cls.source.index("// SURFACE_LIFETIME_DIAGNOSTICS_END",begin)
        cls.helper=cls.source[begin:end]
        (cls.temp/"surface_lifetime_production.inc").write_text(cls.helper)
        cls.env=dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/"data/build/zig-global-cache"),
                     ZIG_LOCAL_CACHE_DIR=str(cls.temp/"zig-cache"))
        cls.compiler=[str(ROOT/"third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"),"cc",
                      "-std=gnu99","-O2","-Wall","-Wextra","-Werror","-DUSE_MISTER",
                      "-DENABLE_WAD14","-DMISTER_RENDER_DIAGNOSTICS","-I",str(SRC),
                      "-I",str(cls.temp),"-I",str(ROOT/"third_party/Butterscotch/vendor/stb/ds")]
        cls.binary=cls.temp/"surface-lifetime.exe"
        subprocess.run([*cls.compiler,str(Path(__file__).with_name("surface_lifetime_regression.c")),
                        "-o",str(cls.binary)],env=cls.env,check=True)

    @classmethod
    def tearDownClass(cls): cls.directory.cleanup()

    def run_case(self,setting,case):
        self.assertIn("PASS",subprocess.check_output([str(self.binary),setting,case],text=True))

    def test_disabled_invalid_values(self):
        for value in ("unset","","0","-1","+1","1025","10000","1 ","/private/path"):
            with self.subTest(value=value): self.run_case(value,"disabled")

    def test_live_creator_removal_no_lifetime_mutation(self): self.run_case("1024","lifetime")
    def test_record_capacity_and_reuse(self): self.run_case("1024","capacity")
    def test_fast_restore_forgets_identity_but_keeps_budget(self): self.run_case("1024","restore")
    def test_hard_log_and_metadata_work_limit(self): self.run_case("1024","bounded")
    def test_sanitized_bounded_code_names(self): self.run_case("1024","sanitize")

    def test_diagnostic_only_hooks_and_release_preprocessing(self):
        for operation,call in (("create","Renderer_createSurface"),("free","surfaceFree"),
                               ("resize","surfaceResize")):
            begin=self.source.index("static RValue builtin_surface_"+operation+"(")
            end=self.source.index("\n}",begin)
            section=self.source[begin:end]
            self.assertLess(section.index(call),section.index('VM_surfaceTrace(ctx, "'+operation+'"'))
        runner=(SRC/"runner.c").read_text()
        begin=runner.index("static Instance** takePersistentInstances(")
        end=runner.index("\n}",begin)
        section=runner[begin:end]
        self.assertLess(section.index("EVENT_CLEANUP"),section.index("VM_surfaceTraceRoomRemove"))
        self.assertLess(section.index("VM_surfaceTraceRoomRemove"),section.index("Instance_free(inst)"))
        sw=(SRC/"sw_renderer.c").read_text()
        begin=sw.index("void SWRenderer_fastStateResetSurfaces(")
        end=sw.index("\n}",begin)
        self.assertIn("VM_surfaceTraceResetRecords();",sw[begin:end])
        probe=self.temp/"release_probe.c"
        probe.write_text('#include "vm_surface_trace.h"\n#if defined(USE_MISTER) && defined(MISTER_RENDER_DIAGNOSTICS)\n'+
                         self.helper+'\n#endif\nvoid probe(void){VM_surfaceTrace(unknown(),"create",1,2,3);'
                         'VM_surfaceTraceRoomRemove(unknown(),unknown());VM_surfaceTraceResetRecords();}\n')
        command=[v for v in self.compiler if v!="-DMISTER_RENDER_DIAGNOSTICS"]
        output=subprocess.check_output([*command,"-E","-P",str(probe)],env=self.env,text=True)
        for absent in ("VM_surfaceTrace","AM2R_SURFACE_TRACE_LIMIT","MiSTer surface lifetime","unknown"):
            self.assertNotIn(absent,output)

if __name__=="__main__": unittest.main()
