"""Dump AM2R's live application/snapshot surfaces for renderer QA.

This is read-only with respect to the attached game process.  Raw images are
written under /tmp on the MiSTer so a failed application-surface handoff can be
distinguished from a later compositing error.
"""

import binascii
import gdb


RVALUE_REAL = 5


def hash_map_length(pointer):
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int(((pointer - 1).cast(header_type) - 1).dereference()["length"] - 1)


def array_length(pointer):
    if not int(pointer):
        return 0
    header_type = gdb.lookup_type("stbds_array_header").pointer()
    return int((pointer.cast(header_type) - 1).dereference()["length"])


def variable_names(vm):
    result = {}
    name_map = vm["varNameMap"]
    for index in range(hash_map_length(name_map)):
        entry = name_map[index]
        if not int(entry["key"]):
            continue
        try:
            result[int(entry["value"])] = entry["key"].string()
        except (gdb.MemoryError, UnicodeError):
            pass
    return result


def global_real(globals_instance, vm, wanted):
    names = variable_names(vm)
    table = globals_instance["selfVars"]
    for index in range(int(table["capacity"])):
        entry = table["entries"][index]
        if names.get(int(entry["key"])) != wanted:
            continue
        value = entry["value"]
        if int(value["type"]) != RVALUE_REAL:
            return None
        return int(value["real"])
    return None


def scope_real(scope_instance, names, wanted):
    table = scope_instance["selfVars"]
    for index in range(int(table["capacity"])):
        entry = table["entries"][index]
        if names.get(int(entry["key"])) != wanted:
            continue
        value = entry["value"]
        if int(value["type"]) != RVALUE_REAL:
            return None
        return int(value["real"])
    return None


def object_name(data_win, object_index):
    if object_index < 0 or object_index >= int(data_win["objt"]["count"]):
        return "<?>"
    pointer = data_win["objt"]["objects"][object_index]["name"]
    return pointer.string() if int(pointer) else "<?>"


def dump_bytes(inferior, address, size, path, label, width, height, encoding):
    payload = bytes(inferior.read_memory(address, size))
    with open(path, "wb") as output:
        output.write(payload)
    nonzero = sum(1 for byte in payload if byte)
    print(
        "RENDER_SURFACE label=%s path=%s address=0x%x bytes=%d size=%dx%d "
        "encoding=%s crc32=%08x nonzero=%d"
        % (
            label,
            path,
            address,
            size,
            width,
            height,
            encoding,
            binascii.crc32(payload) & 0xFFFFFFFF,
            nonzero,
        )
    )


runner = gdb.parse_and_eval("g_runner").dereference()
renderer = runner["renderer"].cast(gdb.lookup_type("SWRenderer").pointer()).dereference()
inferior = gdb.selected_inferior()

host_width = int(renderer["hostWidth"])
host_height = int(renderer["hostHeight"])
host_address = int(renderer["hostFramebuffer"])
if host_address and host_width > 0 and host_height > 0:
    dump_bytes(
        inferior,
        host_address,
        host_width * host_height * 4,
        "/tmp/am2r-host-framebuffer.rgba",
        "host",
        host_width,
        host_height,
        "rgba",
    )

app = int(runner["applicationSurfaceId"])
if app >= 0 and app < int(renderer["surfaceCount"]):
    width = int(renderer["surfaceWidths"][app])
    height = int(renderer["surfaceHeights"][app])
    address = int(renderer["surfacePixels"][app])
    dump_bytes(
        inferior,
        address,
        width * height * 4,
        "/tmp/am2r-application-surface.rgba",
        "application",
        width,
        height,
        "rgba",
    )

vm = runner["vmContext"].dereference()
globals_instance = vm["globalScopeInstance"].dereference()
names = variable_names(vm)
screen = global_real(globals_instance, vm, "screen_surface")
if screen is None:
    data_win = runner["dataWin"].dereference()
    for index in range(array_length(runner["instances"])):
        pointer = runner["instances"][index]
        if not int(pointer):
            continue
        instance = pointer.dereference()
        if object_name(data_win, int(instance["objectIndex"])) == "oControl":
            screen = scope_real(instance, names, "screen_surface")
            if screen is not None:
                break
if (screen is not None and screen >= 0 and
        screen < int(renderer["surfaceCount"]) and
        bool(renderer["surfaceExistsFlags"][screen]) and
        int(renderer["surfacePixels"][screen]) and
        int(renderer["surfaceWidths"][screen]) > 0 and
        int(renderer["surfaceHeights"][screen]) > 0):
    width = int(renderer["surfaceWidths"][screen])
    height = int(renderer["surfaceHeights"][screen])
    address = int(renderer["surfacePixels"][screen])
    dump_bytes(
        inferior,
        address,
        width * height * 4,
        "/tmp/am2r-screen-surface.rgba",
        "screen",
        width,
        height,
        "rgba",
    )
else:
    print("RENDER_SURFACE label=screen missing id=%s" % str(screen))

last_valid = bool(gdb.parse_and_eval("g_gpu_last_presented_valid"))
last_index = int(gdb.parse_and_eval("g_gpu_last_presented_buffer"))
native_pointer = gdb.parse_and_eval("g_gpu_native_buffers[%d]" % last_index)
if last_valid and int(native_pointer):
    try:
        dump_bytes(
            inferior,
            int(native_pointer),
            320 * 240 * 4,
            "/tmp/am2r-last-presented.xrgb",
            "last-presented",
            320,
            240,
            "xrgb8888-le",
        )
    except gdb.MemoryError as error:
        print(
            "RENDER_SURFACE label=last-presented unreadable index=%d address=0x%x error=%s"
            % (last_index, int(native_pointer), str(error))
        )
else:
    print("RENDER_SURFACE label=last-presented missing index=%d" % last_index)
