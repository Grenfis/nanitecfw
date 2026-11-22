#!/usr/bin/env python3
"""Relocate the V2 Now Playing overlay and build a V2 track-deletion image."""

import struct
import subprocess
import sys
from pathlib import Path

from hooks import encode_t32_branch, original_target
from rknano_fw import TABLE_OFF, load, parse_patches, stock_name


def assemble(source, output):
    patch = output / (source + ".patch")
    subprocess.run(
        ["bash", "tools/asm2patch.sh", "src/v2/" + source + ".S", str(patch)],
        check=True, stdout=subprocess.DEVNULL,
    )
    return next(parse_patches(["@" + str(patch)]))


def build(stock, base, output, enable_writes=False):
    if "V2.0.0" not in (stock_name(stock) or ""):
        raise ValueError("the preview requires the official V2.0.0 stock image")
    stock_image = load(stock)
    image = load(base)
    overlay = image.modules[27]
    if (overlay != stock_image.modules[27] or image.load_base != stock_image.load_base or
            overlay["code_exec"] + overlay["code_len"] != 0x0307d9d8):
        raise ValueError("unexpected Now Playing overlay layout")

    output.mkdir(parents=True, exist_ok=True)
    elf = output / "delete-preview.elf"
    binary = output / "delete-preview.bin"
    subprocess.run(
        [
            "arm-none-eabi-gcc", "-mthumb", "-mcpu=cortex-m3", "-Os", "-ffreestanding",
            "-ffunction-sections", "-fdata-sections", "-nostdlib", "-Wall", "-Wextra", "-Werror",
            "-Wl,--gc-sections,--undefined=delete_preview_update,--undefined=delete_preview_input_event,--no-warn-rwx-segments",
            *(["-DDELETE_TRACK_ENABLE_WRITES", "-Wl,--undefined=delete_track_choose_successor",
               "-Wl,--undefined=delete_track_find_successor",
               "-Wl,--undefined=delete_track_refresh_return,--undefined=delete_track_browser_key",
               "-Wl,--undefined=delete_track_launch_successor",
               "src/v2/delete_track_successor.c"]
              if enable_writes else []),
            "-T", "src/v2/delete_track_preview.ld", "-o", str(elf),
            "src/v2/delete_track_preview.c", "src/v2/delete_track_dialog.c",
            "src/v2/delete_track_target.c", "src/v2/delete_track_exfat.c", "src/libc.c",
        ], check=True,
    )
    subprocess.run(
        ["arm-none-eabi-objcopy", "-O", "binary", "-j", ".payload", str(elf), str(binary)], check=True
    )
    symbols = subprocess.check_output(["arm-none-eabi-nm", "-g", str(elf)], text=True)
    (output / "delete-preview.nm").write_text(symbols)
    addresses = {symbol: int(value, 16) for value, _, symbol in map(str.split, symbols.splitlines())}
    if ("delete_track_remove" in addresses) != enable_writes:
        raise ValueError("incorrect removal routines linked into the image")

    key_address = 0x0307ce38
    if original_target(image, {"mod": 27, "addr": key_address, "line": 0}) != 0x030632da:
        raise ValueError("unexpected original key handler")
    update_slot = 0x0300e5c4
    update_offset, _ = image.region(27, update_slot)
    if image.u32(update_offset) != 0x0307cb4f:
        raise ValueError("unexpected original update handler")
    image.patch(27, key_address, encode_t32_branch(key_address, addresses["delete_preview_get_key"], True))
    image.patch(27, update_slot, struct.pack("<I", addresses["delete_preview_update"] | 1))
    event_call = 0x0307b6b8
    if original_target(image, {"mod": 27, "addr": event_call, "line": 0}) != 0x030515a4:
        raise ValueError("unexpected original playback input event handler")
    image.patch(27, event_call, encode_t32_branch(event_call, addresses["delete_preview_input_event"], True))
    version_address = 0x0307e0cc
    version, _ = image.region(32, version_address)
    if image.d[version:version + 12] != "6.0.58".encode("utf-16le"):
        raise ValueError("unexpected version string in the base image")
    stock_version, _ = stock_image.region(32, version_address)
    image.patch(32, version_address, stock_image.d[stock_version:stock_version + 12])

    original_offset = image.l2f(overlay["code_load"])
    relocated = image.d[original_offset:original_offset + overlay["code_len"]] + binary.read_bytes()
    open_module, open_address, open_code = assemble("delete_track_open", output)
    if (open_module, open_address, len(open_code)) != (34, 0x0309ff80, 0x60):
        raise ValueError("unexpected playback-open wrapper placement")
    if image.modules[34] != stock_image.modules[34] or image.modules[34]["data_len"]:
        raise ValueError("unexpected playback-open overlay layout")
    data_address = open_address
    data = open_code
    if enable_writes:
        successor = output / "delete-successor.bin"
        subprocess.run(
            ["arm-none-eabi-objcopy", "-O", "binary", "-j", ".successor", str(elf), str(successor)],
            check=True,
        )
        data_address = 0x0309f000
        data = successor.read_bytes().ljust(open_address - data_address, b"\0") + open_code
        (output / "delete_resume_syms.inc").write_text("".join(
            f"    .equ {symbol}, {addresses[symbol]:#010x}\n"
            for symbol in ("delete_track_refresh_return", "delete_track_browser_key",
                           "delete_track_launch_successor")
        ))
        refresh_module, refresh_tail, refresh_code = assemble("delete_track_refresh_tail", output)
        if (refresh_module, refresh_tail, len(refresh_code)) != (5, 0x0307a958, 0x1c):
            raise ValueError("unexpected refresh tail placement")
        stock_tail, _ = stock_image.region(5, refresh_tail)
        current_tail, _ = image.region(5, refresh_tail)
        if image.d[current_tail:current_tail + 0x1c] != stock_image.d[stock_tail:stock_tail + 0x1c]:
            raise ValueError("refresh tail was modified by the base image")
        image.patch(5, refresh_tail, refresh_code)
        callbacks, _ = image.region(5, 0x0300e5c0)
        if struct.unpack_from("<II", image.d, callbacks) != (0x0307a96d, 0x0307a971):
            raise ValueError("unexpected refresh callbacks")
        # The tail uses the space occupied by the two stock no-op callbacks.
        for module, address, code in ((0, 0x03071d3e, b"\x00\x20\x70\x47"),
                                      (5, 0x0307a6d0, b"\x70\x47")):
            offset, _ = image.region(module, address)
            if image.d[offset:offset + len(code)] != code:
                raise ValueError("unexpected replacement refresh callback")
        image.patch(5, 0x0300e5c0, struct.pack("<II", 0x03071d3f, 0x0307a6d1))
        browser_module, browser_address, browser_code = assemble("delete_track_browser_key", output)
        if ((browser_module, browser_address) != (23, 0x0307b180) or len(browser_code) > 0x40 or
                original_target(image, {"mod": 23, "addr": 0x0307cad6, "line": 0}) != browser_address):
            raise ValueError("unexpected Shuffle All hook placement")
        image.patch(23, browser_address, browser_code)
        launch_call = 0x0307d9a4
        if original_target(image, {"mod": 24, "addr": launch_call, "line": 0}) != 0x0306b302:
            raise ValueError("unexpected playback launch call")
        image.patch(24, launch_call, encode_t32_branch(launch_call, browser_address + 0x24, True))
    open_call = 0x03085c46
    if original_target(image, {"mod": 34, "addr": open_call, "line": 0}) != 0x0305be72:
        raise ValueError("unexpected playback-open call")
    image.patch(34, open_call, encode_t32_branch(open_call, open_address, True))

    reset_module, reset_address, reset_code = assemble("delete_track_fs_reset", output)
    if (reset_module, reset_address) != (0, 0x0305be3c) or len(reset_code) > 0x36:
        raise ValueError("filesystem reset exceeds its original function")
    reset_offset, _ = image.region(0, reset_address)
    if image.d[reset_offset:reset_offset + 0x36] != stock_image.d[reset_offset:reset_offset + 0x36]:
        raise ValueError("filesystem reset was modified by the base image")
    image.patch(0, reset_address, reset_code)

    scratch = 0x0309dfa0 if enable_writes else data_address
    for module in image.modules:
        for kind in ("code", "data", "bss"):
            start = module[kind + "_exec"]
            length = module[kind + "_len"]
            if length and start < 0x0309ffec and start + length > scratch:
                raise ValueError(f"playback provenance overlaps module {module['index']} {kind}")

    stage, capacity = image.region(40, 0x03090000)
    wrapper_offset = (stage + len(relocated) + 3) & ~3
    end = wrapper_offset + len(data)
    if end - stage > capacity or any(image.d[stage:end]):
        raise ValueError("reserved staging range is occupied")
    image.d[stage:stage + len(relocated)] = relocated
    image.d[wrapper_offset:end] = data
    table_entry = TABLE_OFF + 8 + 27 * 32
    struct.pack_into("<I", image.d, table_entry, image.load_base + stage - TABLE_OFF)
    struct.pack_into("<I", image.d, table_entry + 8, len(relocated))
    data_entry = TABLE_OFF + 8 + 34 * 32 + 12
    struct.pack_into("<III", image.d, data_entry,
                     image.load_base + wrapper_offset - TABLE_OFF, data_address, len(data))
    image.finalize()
    suffix = "write-test" if enable_writes else "preview"
    (output / f"NANOV200-delete-emulator-{suffix}.IMG").write_bytes(image.d)


if __name__ == "__main__":
    args = sys.argv[1:]
    enable_writes = bool(args) and args[-1] in ("--enable-writes", "--emulate-writes")
    if enable_writes:
        args.pop()
    if len(args) != 3:
        sys.exit("usage: build_delete_preview.py STOCK_IMG BASE_IMG OUTPUT_DIR [--enable-writes]")
    build(*map(Path, args), enable_writes=enable_writes)
