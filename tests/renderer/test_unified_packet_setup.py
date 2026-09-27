#!/usr/bin/env python3
"""Compile actual constant-size packet setup and independently check its planes.

No hardware claims: this validates setup math/state packing and quantization.
Full CPU-float/native and RTL pixel comparisons remain separate acceptance.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[2]
SRC=ROOT/"third_party/Butterscotch/src"

def extract(source,name):
    match=re.search(r"^static (?:bool|double) "+name+r"\([^;{}]*\)\s*\{",source,re.M)
    if not match: raise AssertionError("missing production helper "+name)
    pos=source.index("{",match.start())+1;depth=1
    while depth:
        depth+=(source[pos]=="{")-(source[pos]=="}");pos+=1
    return source[match.start():pos]

def signed(word):
    return word-(1<<64) if word>>63 else word

def plane(packet,start,x,y,cross=False):
    value=signed(packet[start])+signed(packet[start+1])*x+signed(packet[start+2])*y
    if cross:value+=signed(packet[start+3])*x*y
    return value/(1<<32)

def verify(case):
    verts=case["vertices"];box=case["box"];packet=case["packet"]
    width=packet[2]&65535;height=packet[2]>>16&65535
    if case["kind"]==1:
        a,b,c=verts
        area=(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        def attributes(x,y):
            weights=[]
            for p,q in ((b,c),(c,a),(a,b)):
                weights.append(((q[0]-p[0])*(y-p[1])-(q[1]-p[1])*(x-p[0]))/area)
            return [sum(weights[i]*verts[i][j] for i in range(3)) for j in range(2,8)]
    else:
        def attributes(x,y):
            tx=(x-verts[0][0])/(verts[1][0]-verts[0][0]);ty=(y-verts[0][1])/(verts[3][1]-verts[0][1])
            return [verts[0][2]+tx*(verts[1][2]-verts[0][2]),verts[0][3]+ty*(verts[3][3]-verts[0][3])]+[
                (1-ty)*((1-tx)*verts[0][j]+tx*verts[1][j])+ty*((1-tx)*verts[3][j]+tx*verts[2][j]) for j in range(4,8)]
    tested=0;max_error=0.0;byte_differences=0
    for y in range(box[3]):
        for x in range(box[2]):
            values=attributes(box[0]+x+.5,box[1]+y+.5)
            actual=[plane(packet,17,x,y)/width,plane(packet,20,x,y)/height]+[plane(packet,23+4*i,x,y,True) for i in range(4)]
            # Independent worst-case coefficient rounding bound: .5 ULP each.
            bound=.5/(1<<32)*(1+x+y+x*y)+2e-14
            for expected,got in zip(values,actual):
                error=abs(expected-got);max_error=max(max_error,error)
                if error>bound:raise AssertionError((case["kind"],x,y,expected,got,error,bound))
            for expected,got in zip(values[2:],actual[2:]):
                byte_differences += int(math.floor(max(0,min(1,expected))*255)!=math.floor(max(0,min(1,got))*255))
            tested+=1
    if case["kind"] in (0,1) and byte_differences:
        raise AssertionError("binary-exact fixture changed byte quantization")
    return tested,max_error,byte_differences

def main():
    source=(SRC/"sw_unified_renderer.h").read_text()
    names=("swUnifiedAxisGeometry","swUnifiedSeparableUv","swUnifiedAffineUvSupported","swUnifiedAffineFixedInputs","swUnifiedAffineAccumulationFits","swUnifiedPlane","swUnifiedPacketState","swUnifiedBounds","swUnifiedComponent","swUnifiedBuildAxisPacket","swUnifiedBuildTrianglePacket")
    with tempfile.TemporaryDirectory(prefix="am2r-unified-packet-") as folder:
        temp=Path(folder)
        (temp/"unified_packet_production.inc").write_text("\n\n".join(extract(source,n) for n in names))
        env=dict(os.environ);env["ZIG_GLOBAL_CACHE_DIR"]=str(ROOT/"data/build/zig-global-cache");env["ZIG_LOCAL_CACHE_DIR"]=str(temp/"zig-cache")
        binary=temp/("packet.exe" if os.name=="nt" else "packet")
        command=[str(ROOT/"third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"),"cc","-O2","-std=gnu99","-DUSE_MISTER","-DENABLE_WAD14","-I",str(SRC),"-I",str(temp),"-I",str(ROOT/"third_party/Butterscotch/vendor/stb/ds"),str(Path(__file__).with_name("unified_packet_regression.c")),"-o",str(binary)]
        subprocess.run(command,env=env,check=True)
        output=subprocess.check_output([str(binary)],text=True)
    cases=[json.loads(line) for line in output.splitlines()]
    results=[verify(case) for case in cases]
    print("Production packet setup passed:",len(cases),"fixtures;",sum(x[0] for x in results),"sample locations; max normalized coefficient error",max(x[1] for x in results))
    print("Fractional large-gradient byte quantization differences against double formula:",sum(x[2] for x in results),"(reported, not hidden; no native equivalence claim)")

if __name__=="__main__":main()
