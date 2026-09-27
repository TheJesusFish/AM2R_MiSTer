#!/usr/bin/env python3
"""Compile actual fast/general draw selectors and observe which packets leave.

The axis sampling planner is production code. The affine backend/device sinks
are mocks; this test establishes dispatch preconditions, not native/RTL pixels.
"""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import tempfile
from test_unified_bridge import extract

ROOT=Path(__file__).resolve().parents[2]
SRC=ROOT/"third_party/Butterscotch/src"


def main():
    bridge=(SRC/"sw_unified_renderer.h").read_text()
    sw=(SRC/"sw_renderer.c").read_text()
    names=("swUnifiedStateSupported","swUnifiedAxisGeometry","swUnifiedSeparableUv",
           "swUnifiedAffineUvSupported","swUnifiedPlane","swUnifiedPacketState",
           "swUnifiedBounds","swUnifiedComponent","swUnifiedBuildAxisPacket",
           "swUnifiedBuildTrianglePacket","swUnifiedEmitGeneric","swUnifiedTryGenericQuad",
           "swUnifiedTryQuad")
    with tempfile.TemporaryDirectory(prefix="am2r-unified-dispatch-") as directory:
        temp=Path(directory)
        (temp/"unified_dispatch_production.inc").write_text(extract(sw,"clampByte")+"\n"+extract(sw,"floatColorByte")+"\n"+
            "\n\n".join(extract(bridge,name) for name in names))
        env=dict(os.environ);env["ZIG_GLOBAL_CACHE_DIR"]=str(ROOT/"data/build/zig-global-cache");env["ZIG_LOCAL_CACHE_DIR"]=str(temp/"zig-cache")
        binary=temp/("dispatch.exe" if os.name=="nt" else "dispatch")
        subprocess.run([str(ROOT/"third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"),"cc","-O2","-std=gnu99",
            "-DUSE_MISTER","-DENABLE_WAD14","-I",str(SRC),"-I",str(temp),
            "-I",str(ROOT/"third_party/Butterscotch/vendor/stb/ds"),
            str(Path(__file__).with_name("unified_dispatch_regression.c")),str(SRC/"mister_offscreen.c"),"-o",str(binary)],env=env,check=True)
        subprocess.run([str(binary)],check=True)


if __name__=="__main__":main()
