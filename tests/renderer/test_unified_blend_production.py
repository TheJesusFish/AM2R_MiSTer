#!/usr/bin/env python3
"""Actual production C pixel helper vs independent byte-equation contract."""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess
import tempfile
from test_unified_blend_contract import blend_rgba

ROOT=Path(__file__).resolve().parents[2]
SRC=ROOT/"third_party/Butterscotch/src"

def extract(source,name):
    match=re.search(r"^static (?:inline )?(?:uint8_t|uint32_t|void) "+name+r"\([^;{}]*\)\s*\{",source,re.M)
    if not match:raise AssertionError("Missing production helper "+name)
    pos=source.index("{",match.start())+1;depth=1
    while depth:depth+=(source[pos]=="{")-(source[pos]=="}");pos+=1
    return source[match.start():pos]

def main():
    source=(SRC/"sw_renderer.c").read_text()
    with tempfile.TemporaryDirectory(prefix="am2r-unified-blend-") as folder:
        temp=Path(folder);binary=temp/"blend.exe"
        (temp/"unified_blend_production.inc").write_text("\n\n".join(extract(source,n) for n in ("clampByte","divideBy255U32","blendFactorByte","blendPixel")))
        env=dict(os.environ);env["ZIG_GLOBAL_CACHE_DIR"]=str(ROOT/"data/build/zig-global-cache");env["ZIG_LOCAL_CACHE_DIR"]=str(temp/"zig-cache")
        subprocess.run([str(ROOT/"third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"),"cc","-O3","-std=gnu99","-DENABLE_WAD14","-I",str(SRC),"-I",str(temp),"-I",str(ROOT/"third_party/Butterscotch/vendor/stb/ds"),str(Path(__file__).with_name("unified_blend_production.c")),"-o",str(binary)],env=env,check=True)
        output=subprocess.check_output([str(binary)],text=True)
    lanes=lambda pixel:tuple(pixel>>(8*i)&255 for i in range(4))
    modes=("normal","add","max","subtract","min","reverse_subtract","custom")
    count=0
    for line in output.splitlines():
        mode,src,dst,enabled,at,ref,fog,color,mask,factors,result=map(int,line.split())
        expected=blend_rgba(lanes(src),lanes(dst),modes[mode],factors=lanes(factors),enabled=bool(enabled),alpha_test=bool(at),alpha_ref=ref,fog=lanes(color)[:3] if fog else None,write_mask=tuple(bool(mask&(1<<i)) for i in range(4)))
        if lanes(result)!=expected:raise AssertionError((count,line,lanes(result),expected))
        count+=1
    assert count==6000
    print("Production C blend passed 6000 independent randomized state/factor/fog/alpha/mask cases, including transparent RGB.")

if __name__=="__main__":main()
