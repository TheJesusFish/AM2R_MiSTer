#!/usr/bin/env python3
"""Regression for the characterized shared-edge mismatch.

Actual production setup and CPU rasterization are compared against independent
exactly-one edge ownership. This is host arithmetic proof, not RTL/native proof.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import tempfile
from test_unified_bridge import extract

ROOT=Path(__file__).resolve().parents[2]
SRC=ROOT/"third_party/Butterscotch/src"


def signed(x):
    return x-(1<<64) if x>>63 else x


def main():
    bridge=(SRC/"sw_unified_renderer.h").read_text()
    sw=(SRC/"sw_renderer.c").read_text()
    names=("swUnifiedPlane","swUnifiedPacketState","swUnifiedBounds","swUnifiedComponent","swUnifiedBuildTrianglePacket")
    cpu=("clampByte","divideBy255U32","blendFactorByte","blendPixel","sampleTexture","edgeFunction","rasterizeTriangle")
    with tempfile.TemporaryDirectory(prefix="am2r-shared-edges-") as directory:
        temp=Path(directory)
        (temp/"unified_packet_production.inc").write_text("\n".join(extract(bridge,name) for name in names))
        (temp/"unified_triangle_cpu.inc").write_text("\n".join(extract(sw,name) for name in cpu))
        env=dict(os.environ);env["ZIG_GLOBAL_CACHE_DIR"]=str(ROOT/"data/build/zig-global-cache");env["ZIG_LOCAL_CACHE_DIR"]=str(temp/"zig-cache")
        binary=temp/("edges.exe" if os.name=="nt" else "edges")
        subprocess.run([str(ROOT/"third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"),"cc","-O2","-std=gnu99","-DUSE_MISTER","-DENABLE_WAD14","-I",str(SRC),"-I",str(temp),"-I",str(ROOT/"third_party/Butterscotch/vendor/stb/ds"),str(Path(__file__).with_name("unified_shared_edges.c")),"-o",str(binary)],env=env,check=True)
        results=[json.loads(line) for line in subprocess.check_output([str(binary)],text=True).splitlines()]
    def edge(a,b,p):
        return (b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0])
    def expected_covered(vertices,x,y):
        vertices=list(vertices)
        if edge(*vertices)<0:vertices[1],vertices[2]=vertices[2],vertices[1]
        for a,b in zip(vertices,vertices[1:]+vertices[:1]):
            value=edge(a,b,(x+.5,y+.5))
            owns=b[1]<a[1] or (b[1]==a[1] and b[0]>a[0])
            if value<0 or (value==0 and not owns):return False
        return True
    totals=[]
    for result in results:
        counts=[0]*256;alpha=[0]*256;expected=[0]*256
        for triangle in result["triangles"]:
            x0,y0,w,h=triangle["box"];p=triangle["packet"]
            for y in range(16):
                for x in range(16):
                    expected[y*16+x]+=expected_covered(triangle["vertices"],x,y)
            for y in range(h):
                for x in range(w):
                    if all(signed(p[e])+signed(p[e+1])*x+signed(p[e+2])*y>=0 for e in (8,11,14)):
                        at=(y0+y)*16+x0+x;counts[at]+=1
                        a=max(0,min(255,(signed(p[35])+signed(p[36])*x+signed(p[37])*y)*255//(1<<32)))
                        alpha[at]=a+alpha[at]*(255-a)//255
        assert counts==expected,(result["kind"],"independent geometric coverage mismatch")
        assert max(counts)==1,(result["kind"],"shared edge drawn twice")
        assert all(alpha[i]==127*count for i,count in enumerate(expected)),"packet attributes changed by coverage bias"
        assert result["cpu_alpha"]==alpha,(result["kind"],"CPU reference ownership or interpolation mismatch")
        totals.append(sum(counts))
        if result["kind"] in (0,1):
            assert counts[1*16+2]==counts[4*16+3]==1,"original diagonal fixture coverage"
        if result["kind"] in (2,3):
            assert sum(counts)==64 and counts[4*16+4]==1,"four fan spokes must own center once"
            assert all(counts[y*16+x]==(x<8 and y<8) for y in range(16) for x in range(16)),"outer top/left ownership"
    assert totals[0]==totals[1] and totals[2]==totals[3],"winding changed coverage"
    print("Shared-edge regression passed: 5 actual-setup/CPU fixtures, both windings, general quad diagonal, four-way fan center, outer boundaries; each shared sample owned once. Covered samples:",totals)
    print("Independent basis: OpenGL2.1 section3.5.1 https://registry.khronos.org/OpenGL/specs/gl/glspec21.pdf . No hardware/native room claim.")


if __name__=="__main__":main()
