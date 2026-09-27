#!/usr/bin/env python3
"""Test actual immutable-atlas opacity pruning against independent pixels.

The production opacity bitset, coverage marking and new tile-span helper are
compiled unchanged. Only record registration and ordinary-RAM buffers are test
adapters. Pixel equivalence uses the independent descriptor reference, not DUT
blend/coverage arithmetic. No hardware or game data is used.
"""
from __future__ import annotations
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest
from test_unified_bridge import extract

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools"))
import am2r_gpu_reference as gpu
from test_gpu_targets import command

SOURCE=0x24000000
TEX_WIDTH,TEX_HEIGHT=384,256
OPAQUE=struct.pack(f"<{TEX_WIDTH*TEX_HEIGHT}I",*(0xFF000000|((i*7919)^0x153779)&0xFFFFFF for i in range(TEX_WIDTH*TEX_HEIGHT)))
INITIAL=[((i*0x19B52731)^0x739415A7)&0xFFFFFFFF for i in range(gpu.PIXELS)]


def axis(x,y,width,height,*,source=SOURCE,u=0,v=0,du=65536,dv=65536,tint=0xFFFFFFFF,flags=0,**extra):
    return command(2|flags,width,height,base=source,stride=TEX_WIDTH*4,x=x,y=y,
                   w4=(u&0xFFFFFFFF)|((v&0xFFFFFFFF)<<32),w5=(du&0xFFFFFFFF)|((dv&0xFFFFFFFF)<<32),w6=tint,**extra)


def scene(width,height):
    return [command(14,width,height,base=0x73041239),
            command(3,width,height,base=0x83123456),axis(0,0,width,height)]


class OpaqueCull(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory(prefix="am2r-unified-opaque-")
        cls.directory=Path(cls.folder.name)
        source=(ROOT/"third_party/Butterscotch/src/backends/mister.c").read_text()
        types=source[source.index("typedef struct { uint64_t word[8]; }"):source.index("static Runner *g_runner")]
        defines="\n".join(line for line in source.splitlines() if line.startswith("#define ") and line.split()[1] in
                           {"MISTER_WIDTH","MISTER_HEIGHT","GPU_COMMAND_BYTES","GPU_COMMAND_CAPACITY","GPU_COMMAND_BUFFER_COUNT"})
        (cls.directory/"opaque_types.inc").write_text(defines+"\n"+types)
        names=("gpuFixedFloor","textureRecordForPhysical","prepareTextureOpacity","textureOpacityRangeIsSet",
               "axisCommandCanOverwrite","markAxisCommandCoverage","axisCommandSourceIsOpaque",
               "gpuCoverageIncludesTile","gpuSurfaceCullOpaquePrefix")
        (cls.directory/"opaque_production.inc").write_text("\n\n".join(extract(source,name) for name in names))
        cls.binary=cls.directory/("opaque.exe" if os.name=="nt" else "opaque")
        env=dict(os.environ);env["ZIG_GLOBAL_CACHE_DIR"]=str(ROOT/"data/build/zig-global-cache");env["ZIG_LOCAL_CACHE_DIR"]=str(cls.directory/"zig-cache")
        subprocess.run([str(ROOT/"third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"),"cc","-O2","-std=c11",
            "-Wall","-Wextra","-Werror","-I",str(cls.directory),str(Path(__file__).with_name("unified_opaque_cull_regression.c")),
            "-o",str(cls.binary)],env=env,check=True)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def cull(self,items,width=320,height=240,*,start=0,flags=0,generation=1,warm=False,
             mutation=None,source=OPAQUE,record_bytes=None,compare=True):
        mutation_index,mutation_pixel=(0xFFFFFFFF,0) if mutation is None else mutation
        header=struct.pack("<12I",len(items),start,width,height,flags,generation,warm,mutation_index,mutation_pixel,
                           len(source),len(source) if record_bytes is None else record_bytes,0)
        result=subprocess.run([str(self.binary)],input=header+b"".join(items)+source,capture_output=True,check=True)
        removed,count=struct.unpack_from("<2I",result.stdout)
        after=result.stdout[8:]
        self.assertEqual(len(after),count*64)
        self.assertEqual(removed,len(items)-count)
        self.assertEqual(after[:start*64],b"".join(items[:start]),"already-published dependency prefix changed")
        if compare:
            if mutation is not None:
                source=bytearray(source);struct.pack_into("<I",source,mutation_index*4,mutation_pixel);source=bytes(source)
            regions={SOURCE:source}
            before_pixels=gpu.render(b"".join(items)+command(12),regions,INITIAL)
            after_pixels=gpu.render(after+command(12),regions,INITIAL)
            self.assertEqual(before_pixels.pixels,after_pixels.pixels,"pruning changed independently rendered pixels")
            self.assertEqual(before_pixels.exports,after_pixels.exports,"pruning changed an observable export")
        return removed,after

    def test_full_partial_odd_and_bitset_boundaries(self):
        for width,height in ((320,240),(319,239),(65,7),(64,1),(63,3),(1,1),(129,11)):
            with self.subTest(width=width,height=height):
                removed,_=self.cull(scene(width,height),width,height)
                self.assertEqual(removed,2)

    def test_last_wrapped_suffix_and_safe_prior_dependencies(self):
        prefix=[command(1,base=0xF7312914),command(11,2,2,base=0x28000000,stride=8),
                command(10,2,2,base=SOURCE,stride=8)]
        width,height=319,17
        items=prefix+[command(14,width,height,base=0),axis(0,0,width,height),
                      axis(0,0,129,height,u=11*65536),axis(129,0,190,height,u=160*65536),
                      command(3,3,3,base=0x80112233,x=7,y=7)]
        removed,after=self.cull(items,width,height,start=len(prefix))
        self.assertEqual(removed,2)
        self.assertEqual(after[-64:],items[-1],"draw suffix must retain original order")

    def test_pinhole_near_full_gap_and_partial_alpha(self):
        width,height=65,9
        for value in (0x00112233,0xFE112233):
            removed,_=self.cull(scene(width,height),width,height,mutation=(4*TEX_WIDTH+31,value))
            self.assertEqual(removed,0)
        items=scene(width,height);items[-1]=axis(0,0,width-1,height)
        self.assertEqual(self.cull(items,width,height)[0],0)
        items[-1]=axis(0,0,width,height-1)
        self.assertEqual(self.cull(items,width,height)[0],0)

    def test_unknown_dynamic_stale_null_and_released_shadows(self):
        for flags in (1,2,4,8,16):
            with self.subTest(flags=flags):
                self.assertEqual(self.cull(scene(65,9),65,9,flags=flags)[0],0)
                self.assertEqual(self.cull(scene(65,9),65,9,flags=flags,warm=True)[0],0)
        unknown=scene(65,9);unknown[-1]=axis(0,0,65,9,source=0x29000000)
        self.assertEqual(self.cull(unknown,65,9,compare=False)[0],0)

    def test_opacity_cache_generation_refresh(self):
        items=scene(65,9);at=4*TEX_WIDTH+31
        self.assertEqual(self.cull(items,65,9,warm=True,generation=2,mutation=(at,0x00112233))[0],0)
        transparent=bytearray(OPAQUE);struct.pack_into("<I",transparent,at*4,0x00112233)
        self.assertEqual(self.cull(items,65,9,warm=True,generation=2,source=bytes(transparent),mutation=(at,0xFF556677))[0],2)

    def test_nonoverwriting_blends_tint_and_steps_are_preserved(self):
        for kwargs in ({"flags":256},{"flags":512},{"tint":0xFEFFFFFF},{"du":0},{"dv":0},
                       {"du":32768},{"dv":32768},{"du":-65536,"u":64*65536}):
            items=scene(65,9);items[-1]=axis(0,0,65,9,**kwargs)
            self.assertEqual(self.cull(items,65,9)[0],0)
        items=scene(65,9);items[-1]=axis(0,0,65,9,flags=1024,tint=0xFF513799)
        self.assertEqual(self.cull(items,65,9)[0],2,"floor tint retains exact opaque alpha")

    def test_clipped_geometry_fractional_source_and_record_offsets(self):
        items=scene(65,9);items[-1]=axis(-3,-2,68,11,u=0,v=0)
        self.assertEqual(self.cull(items,65,9)[0],2)
        items[-1]=axis(0,0,65,9,u=32768,v=32768)
        self.assertEqual(self.cull(items,65,9)[0],2)
        items[-1]=axis(0,0,65,9,source=SOURCE+12,u=2*65536,v=65536)
        self.assertEqual(self.cull(items,65,9)[0],2)
        for bad in (axis(320,0,65,9),axis(0,240,65,9),axis(0,0,65,9,u=-65536),
                    axis(0,0,65,9,v=255*65536),axis(0,0,65,9,source=SOURCE+1)):
            items[-1]=bad
            self.assertEqual(self.cull(items,65,9,compare=False)[0],0)

    def test_state_payload_and_observable_opcodes_block_whole_span(self):
        for op in (0,4,5,6,7,8,9,10,11,12,13,15,255):
            items=scene(65,9);items.insert(1,command(op))
            removed,after=self.cull(items,65,9,compare=False)
            self.assertEqual(removed,0)
            self.assertEqual(after,b"".join(items))

    def test_reserved_flags_and_fields_are_not_silently_elided(self):
        valid=scene(65,9)
        mutations=[(0,1<<bit) for bit in range(11,16)]+[(0,1<<48),(2,1<<32),(3,1),(6,1<<32),(7,1)]
        for word,flag in mutations:
            items=list(valid);words=list(struct.unpack("<8Q",items[-1]));words[word]|=flag
            items[-1]=struct.pack("<8Q",*words)
            self.assertEqual(self.cull(items,65,9,compare=False)[0],0)
            # Unknown prefix variants must also block the whole optimization,
            # even if a later ordinary opaque rectangle would hide their pixels.
            items=[valid[0],items[-1],valid[-1]]
            self.assertEqual(self.cull(items,65,9,compare=False)[0],0)
        for index in (0,1):
            items=list(valid);words=list(struct.unpack("<8Q",items[index]));words[1]|=1<<32
            items[index]=struct.pack("<8Q",*words)
            self.assertEqual(self.cull(items,65,9,compare=False)[0],0)

    def test_last_partial_opacity_word_and_row_padding(self):
        texture=struct.pack("<65I",*([0xFF357913]*65))
        self.assertEqual(self.cull(scene(65,1),65,1,source=texture)[0],2)
        self.assertEqual(self.cull(scene(65,1),65,1,source=texture,mutation=(64,0x00793135))[0],0)
        padded=bytearray(len(OPAQUE))
        for y in range(9):
            padded[y*TEX_WIDTH*4:(y*TEX_WIDTH+65)*4]=texture
        self.assertEqual(self.cull(scene(65,9),65,9,source=bytes(padded))[0],2,
                         "unused transparent row padding must not defeat exact sampled opacity")

    def test_invalid_tile_arguments_and_no_full_overwrite(self):
        for width,height,start in ((0,9,0),(65,0,0),(321,9,0),(65,241,0),(65,9,3),(65,9,0xFFFFFFFF)):
            self.assertEqual(self.cull(scene(65,9),width,height,start=start,compare=False)[0],0)
        for items in ([],[command(14,65,9,base=1)],[axis(0,0,65,9)]):
            self.assertEqual(self.cull(items,65,9)[0],0)

    def test_deterministic_random_split_coverage(self):
        rng=random.Random(0xC0110)
        for _ in range(30):
            width=rng.randint(2,320);height=rng.randint(1,40);split=rng.randint(1,width-1)
            items=scene(width,height)[:-1]+[axis(0,0,split,height),axis(split,0,width-split,height,u=split*65536)]
            self.assertEqual(self.cull(items,width,height)[0],2)


if __name__=="__main__":unittest.main()
