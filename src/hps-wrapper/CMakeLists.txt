cmake_minimum_required(VERSION 3.20)
project(MiSTer_AM2R C CXX ASM)

option(AM2R_KEEP_DEBUG_SYMBOLS
    "Keep DWARF and static symbols in Release frontend builds" OFF)

if(NOT MAIN_MISTER_SOURCE)
    message(FATAL_ERROR "MAIN_MISTER_SOURCE must point at the pinned current-upstream Main_MiSTer checkout")
endif()
if(NOT BUTTERSCOTCH_SOURCE)
    message(FATAL_ERROR "BUTTERSCOTCH_SOURCE must point at the patched Butterscotch checkout")
endif()

file(GLOB MAIN_C CONFIGURE_DEPENDS
    "${MAIN_MISTER_SOURCE}/*.c"
    "${MAIN_MISTER_SOURCE}/lib/miniz/*.c"
    "${MAIN_MISTER_SOURCE}/lib/md5/*.c"
    "${MAIN_MISTER_SOURCE}/lib/lzma/*.c"
    "${MAIN_MISTER_SOURCE}/lib/zstd/lib/common/*.c"
    "${MAIN_MISTER_SOURCE}/lib/zstd/lib/decompress/*.c"
    "${MAIN_MISTER_SOURCE}/lib/libchdr/*.c"
)
list(APPEND MAIN_C "${MAIN_MISTER_SOURCE}/lib/libco/arm.c")
file(GLOB MAIN_CXX CONFIGURE_DEPENDS
    "${MAIN_MISTER_SOURCE}/*.cpp"
    "${MAIN_MISTER_SOURCE}/lib/serial_server/library/*.cpp"
    "${MAIN_MISTER_SOURCE}/support/*/*.cpp"
)
list(REMOVE_ITEM MAIN_CXX "${MAIN_MISTER_SOURCE}/main.cpp")

# MiSTer's standard T[...] menu entries pulse their status bit high and low
# within a single HandleUI() call.  An HPS hybrid frontend therefore cannot
# discover the command by polling the status cache after HandleUI() returns.
# Keep the pinned upstream checkout pristine and add a narrow notification at
# the status-update boundary in a generated build-tree copy.
set(USER_IO "${MAIN_MISTER_SOURCE}/user_io.cpp")
list(REMOVE_ITEM MAIN_CXX "${USER_IO}")
file(READ "${USER_IO}" USER_IO_SOURCE)
set(USER_IO_SET_SIGNATURE
    "void user_io_status_set(const char *opt, uint32_t value, int ex)\n{")
set(USER_IO_SET_HOOK
    "extern \"C\" void am2r_user_io_status_event(const char *opt, uint32_t value, int ex);\n\nvoid user_io_status_set(const char *opt, uint32_t value, int ex)\n{\n\tam2r_user_io_status_event(opt, value, ex);")
string(FIND "${USER_IO_SOURCE}" "${USER_IO_SET_SIGNATURE}" USER_IO_SET_OFFSET)
if(USER_IO_SET_OFFSET EQUAL -1)
    message(FATAL_ERROR "Pinned Main_MiSTer user_io_status_set signature changed")
endif()
string(REPLACE "${USER_IO_SET_SIGNATURE}" "${USER_IO_SET_HOOK}"
    USER_IO_SOURCE "${USER_IO_SOURCE}")
set(PATCHED_USER_IO "${CMAKE_CURRENT_BINARY_DIR}/user_io.cpp")
file(WRITE "${PATCHED_USER_IO}" "${USER_IO_SOURCE}")

# The pinned current-upstream Main_MiSTer source is normally compiled with GCC, whose libstdc++
# accepts hypotf() in this C++14 constant initializer.  Zig's Clang frontend
# correctly rejects it.  Keep the upstream checkout pristine and generate the
# one-line portability fix in the build tree.
set(N64_JOY_EMU "${MAIN_MISTER_SOURCE}/support/n64/n64_joy_emu.cpp")
list(REMOVE_ITEM MAIN_CXX "${N64_JOY_EMU}")
file(READ "${N64_JOY_EMU}" N64_JOY_EMU_SOURCE)
string(REPLACE
    "static constexpr float MAX_DIST = hypotf(MAX_DIAG, MAX_DIAG);"
    "static constexpr float MAX_DIST = MAX_DIAG * 1.4142135623730951f;"
    N64_JOY_EMU_SOURCE
    "${N64_JOY_EMU_SOURCE}"
)
set(PATCHED_N64_JOY_EMU "${CMAKE_CURRENT_BINARY_DIR}/n64_joy_emu.cpp")
file(WRITE "${PATCHED_N64_JOY_EMU}" "${N64_JOY_EMU_SOURCE}")

file(TO_CMAKE_PATH "${MAIN_MISTER_SOURCE}/logo.png" MAIN_MISTER_LOGO)
configure_file(logo_blob.S.in "${CMAKE_CURRENT_BINARY_DIR}/logo_blob.S" @ONLY)

add_executable(MiSTer_AM2R
    ${MAIN_C}
    ${MAIN_CXX}
    "${PATCHED_N64_JOY_EMU}"
    "${PATCHED_USER_IO}"
    am2r_main.cpp
    am2r_wrapper.cpp
    "${CMAKE_CURRENT_BINARY_DIR}/logo_blob.S"
)

target_include_directories(MiSTer_AM2R PRIVATE
    "${BUTTERSCOTCH_SOURCE}/src"
    "${MAIN_MISTER_SOURCE}"
    "${MAIN_MISTER_SOURCE}/lib/libco"
    "${MAIN_MISTER_SOURCE}/lib/miniz"
    "${MAIN_MISTER_SOURCE}/lib/md5"
    "${MAIN_MISTER_SOURCE}/lib/lzma"
    "${MAIN_MISTER_SOURCE}/lib/zstd/lib"
    "${MAIN_MISTER_SOURCE}/lib/libchdr/include"
    "${MAIN_MISTER_SOURCE}/lib/bluetooth"
    "${MAIN_MISTER_SOURCE}/lib/serial_server/library"
)
target_compile_definitions(MiSTer_AM2R PRIVATE
    _7ZIP_ST
    PACKAGE_VERSION="1.3.3"
    HAVE_LROUND
    HAVE_STDINT_H
    HAVE_STDLIB_H
    HAVE_SYS_PARAM_H
    ENABLE_64_BIT_WORDS=0
    _FILE_OFFSET_BITS=64
    _LARGEFILE64_SOURCE
    VDATE="260906"
)
target_compile_options(MiSTer_AM2R PRIVATE
    -O3
    -Wall
    -Wextra
    -Wno-strict-aliasing
    -Wno-psabi
)
if(NOT AM2R_KEEP_DEBUG_SYMBOLS)
    # The Zig cross compiler emits DWARF even for CMake Release builds unless
    # told otherwise. Keep release/test binaries small like stock MiSTer while
    # allowing an explicitly requested local developer build to retain symbols.
    target_compile_options(MiSTer_AM2R PRIVATE $<$<CONFIG:Release>:-g0>)
    target_link_options(MiSTer_AM2R PRIVATE $<$<CONFIG:Release>:-Wl,-s>)
endif()
set_property(TARGET MiSTer_AM2R PROPERTY C_STANDARD 99)
set_property(TARGET MiSTer_AM2R PROPERTY CXX_STANDARD 14)

target_link_libraries(MiSTer_AM2R PRIVATE
    m
    rt
    pthread
    dl
    "${MAIN_MISTER_SOURCE}/lib/imlib2/libfreetype.so"
    "${MAIN_MISTER_SOURCE}/lib/imlib2/libbz2.so"
    "${MAIN_MISTER_SOURCE}/lib/imlib2/libpng16.so"
    "${MAIN_MISTER_SOURCE}/lib/imlib2/libz.so"
    "${MAIN_MISTER_SOURCE}/lib/imlib2/libImlib2.so"
    "${MAIN_MISTER_SOURCE}/lib/bluetooth/libbluetooth.so"
)

set_target_properties(MiSTer_AM2R PROPERTIES
    BUILD_RPATH ""
    INSTALL_RPATH ""
)
