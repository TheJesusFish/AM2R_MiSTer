"""Make the current MiSTer GPU command list ineligible for opaque-prefix culling.

Run this only after breaking at platformSwapBuffers, when the frame command list
is complete but before cullFullyCoveredPrefix executes.  Physical texture data
and commands are left untouched; clearing the diagnostic shadow-valid bits
causes the conservative culler to retain the entire command prefix.
"""

import gdb


count = int(gdb.parse_and_eval("g_gpu_texture_record_count"))
for index in range(count):
    gdb.execute("set g_gpu_texture_records[%d].shadow_valid=0" % index)
    gdb.execute("set g_gpu_texture_records[%d].opaque_generation=0" % index)

print("OPAQUE_CULLING_DISABLED_FOR_FRAME texture_records=%d" % count)
