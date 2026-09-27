#!/usr/bin/env python3
"""Regress accumulated-Q16 coverage with actual affine/general host dispatch.

This probe does not change production code or launch hardware. Exact rational
inverse-quad geometry is independent of the GPU model's wrapped arithmetic.
"""
from fractions import Fraction as F
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from test_unified_bridge import extract
from test_gpu_targets import write_fixture

ROOT=Path(__file__).resolve().parents[2]
SRC=ROOT/"third_party/Butterscotch/src"
sys.path.insert(0,str(ROOT/"tools"))
import am2r_gpu_reference as gpu


def main():
    sw=(SRC/"sw_renderer.c").read_text()
    unified=(SRC/"sw_unified_renderer.h").read_text()
    with tempfile.TemporaryDirectory(prefix="affine-wrap-") as folder:
        temp=Path(folder)
        (temp/"unified_affine_bounds_production.inc").write_text(
            extract(sw,"clampByte")+"\n"+extract(sw,"floatColorByte")+"\n"+
            "\n".join(extract(unified,name) for name in (
                "swUnifiedAffineFixedInputs","swUnifiedAffineAccumulationFits",
                "swUnifiedStateSupported","swUnifiedAxisGeometry","swUnifiedSeparableUv",
                "swUnifiedAffineUvSupported","swUnifiedPlane","swUnifiedPacketState",
                "swUnifiedBounds","swUnifiedComponent","swUnifiedBuildAxisPacket",
                "swUnifiedBuildTrianglePacket","swUnifiedEmitGeneric","swUnifiedTryGenericQuad",
                "swUnifiedTryQuad"))+"\n"+extract(sw,"swGpuTryAffineQuad"))
        env=os.environ.copy()
        env["ZIG_GLOBAL_CACHE_DIR"]=str(ROOT/"data/build/zig-global-cache")
        env["ZIG_LOCAL_CACHE_DIR"]=str(temp/"zig-cache")
        binary=temp/"probe.exe"
        subprocess.run([str(ROOT/"third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"),"cc",
            "-std=gnu99","-O2","-DUSE_MISTER","-DENABLE_WAD14","-I",str(SRC),"-I",str(temp),
            "-I",str(ROOT/"third_party/Butterscotch/vendor/stb/ds"),
            str(Path(__file__).with_name("unified_affine_bounds_probe.c")),str(SRC/"mister_offscreen.c"),"-o",str(binary)],check=True,env=env)
        result=subprocess.run([str(binary)],text=True,capture_output=True,check=True)
        legacy=subprocess.run([str(binary),"legacy"],text=True,capture_output=True,check=True)
        ordinary=subprocess.run([str(binary),"unified","ordinary"],text=True,capture_output=True,check=True)
        ordinary_legacy=subprocess.run([str(binary),"legacy","ordinary"],text=True,capture_output=True,check=True)
        assert ordinary.stdout==ordinary_legacy.stdout and ordinary.stdout.startswith("AFFINE\n")
    def replay(output):
        lines=iter(output.splitlines());regions={0x24000000:b"\xff"*128}
        commands=struct.pack("<8Q",1,0xff000000,0,0,0,0,0,0)
        for tag in lines:
            words=[int(next(lines),16) for _ in range(16 if tag=="AFFINE" else 8)]
            commands+=struct.pack(f"<{len(words)}Q",*words)
            if tag=="GENERIC":regions[words[1]]=struct.pack("<64Q",*[int(next(lines),16) for _ in range(64)])
        commands+=struct.pack("<8Q",12,0,0,0,0,0,0,0)
        return gpu.render(commands,regions),commands,regions
    assert "actual_affine_helper_accepted=0" in result.stderr
    assert result.stdout.count("GENERIC\n")==2 and "AFFINE" not in result.stdout
    assert "actual_affine_helper_accepted=1" in legacy.stderr
    rendered,commands,regions=replay(result.stdout);legacy_rendered,_,_=replay(legacy.stdout)
    # A=(100,100), B=(100,100+1/1024); exact determinant is100/1024.
    false_samples=[];legacy_false_samples=[];expected_samples=0;actual_samples=0;differences=0;expected_pixels=[]
    for y in range(240):
        for x in range(320):
            px,py=F(2*x+1,2),F(2*y+1,2)+F(1,4096)
            tx=(px*F(102401,1024)-py*100)/F(100,1024)
            ty=(-px*100+py*100)/F(100,1024)
            expected=0<=tx<1 and 0<=ty<1
            expected_pixels.append(0xffffffff if expected else 0xff000000)
            actual=rendered.pixels[y*320+x]!=0xff000000
            expected_samples+=expected;actual_samples+=actual
            if actual and not expected:false_samples.append([x,y])
            differences+=actual!=expected
            if legacy_rendered.pixels[y*320+x]!=0xff000000 and not expected:legacy_false_samples.append([x,y])
    assert differences==0 and not false_samples
    assert len(legacy_false_samples)>1000
    if os.getenv("AM2R_TEST_RTL")=="1":
        parent=ROOT/"data/work/unified-renderer-20260925"
        directory=Path(tempfile.mkdtemp(prefix="affine-bounds-reference-",dir=parent))
        manifest_path=write_fixture(directory,commands,regions)
        expected_bytes=struct.pack("<76800I",*expected_pixels)
        (directory/"expected.rgba").write_bytes(expected_bytes)
        manifest=json.loads(manifest_path.read_text())
        manifest["expected"]={"file":"expected.rgba","format":"rgba8888"}
        manifest_path.write_text(json.dumps(manifest))
        for latency,stall,gap in ((2,11,0),(5,7,3)):
            replay_result=subprocess.run([sys.executable,str(ROOT/"tools/replay_gpu_capture.py"),str(manifest_path),
                "--output",str(directory),"--read-latency",str(latency),"--stall-period",str(stall),
                "--response-gap",str(gap)],capture_output=True,text=True,timeout=240)
            assert replay_result.returncode==0,replay_result.stdout+replay_result.stderr
        print("Exact rational coverage matches both RTL DDR schedules:",directory)
    print(json.dumps({"actual_affine_helper_rejected":True,"routed_generic_triangles":2,"ordinary_affine_unchanged":True,
        "expected_covered_pixels":expected_samples,
        "actual_covered_pixels":actual_samples,"unexpected_wrapped_coverage":len(false_samples),
        "legacy_repro_false_pixels":len(legacy_false_samples),"legacy_first_false_samples":legacy_false_samples[:8]},indent=2))


if __name__=="__main__":main()
