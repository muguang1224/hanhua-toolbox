"""最小 PE 头解析：判断 32/64 位、是否有 .NET CLR 头、子系统类型。

只读文件头，不加载程序，不执行任何代码。
"""

from __future__ import annotations

import struct

MACHINE_NAMES = {0x14C: "x86", 0x8664: "x64", 0xAA64: "ARM64", 0x1C0: "ARM"}
SUBSYSTEM_GUI = 2
SUBSYSTEM_CONSOLE = 3

MAX_HEADER_SCAN = 4 * 1024 * 1024


def read_pe(path: str) -> dict | None:
    """不是 PE 文件就返回 None；解析失败也返回 None（调用方按未知处理）。"""
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return None
            f.seek(0x3C)
            raw = f.read(4)
            if len(raw) < 4:
                return None
            pe_off = struct.unpack("<I", raw)[0]
            if pe_off <= 0 or pe_off > MAX_HEADER_SCAN:
                return None
            f.seek(pe_off)
            if f.read(4) != b"PE\0\0":
                return None
            coff = f.read(20)
            if len(coff) < 20:
                return None
            machine, nsect = struct.unpack_from("<HH", coff, 0)
            opt_size = struct.unpack_from("<H", coff, 16)[0]
            opt = f.read(opt_size)
    except (OSError, struct.error):
        return None

    if len(opt) < 70:
        return None
    magic = struct.unpack_from("<H", opt, 0)[0]
    if magic not in (0x10B, 0x20B):
        return None
    is64 = magic == 0x20B
    subsystem = struct.unpack_from("<H", opt, 68)[0]

    nrva_off = 108 if is64 else 92
    dirs_off = 112 if is64 else 96
    n_dirs = 0
    if len(opt) >= nrva_off + 4:
        n_dirs = struct.unpack_from("<I", opt, nrva_off)[0]

    clr_rva = 0
    if n_dirs > 14 and len(opt) >= dirs_off + 15 * 8:
        clr_rva = struct.unpack_from("<I", opt, dirs_off + 14 * 8)[0]

    return {
        "machine": MACHINE_NAMES.get(machine, hex(machine)),
        "is64": is64,
        "subsystem": subsystem,
        "managed": clr_rva != 0,
        "sections": nsect,
    }
