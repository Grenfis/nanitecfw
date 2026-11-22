import struct
import subprocess
import unittest
from itertools import product
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_R8, UC_ARM_REG_SP,
)

from tools.rknano_fw import load

ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ROOT / "out/delete-preview"
WRITE_TEST = ROOT / "out/delete-write-test"
BASE = ROOT / "out/nanitecfw-6.0.58-20261104/NANOV200.IMG"
RETURN = 0x03010000
KEY_CALL = 0x0307CE38
UPDATE_SLOT = 0x0300E5C4
PLAYBACK_WIDGET = 0x0300E5B4
CONFIRMATION_WIDGET = 0x03004600
MOUNT_STATE = 0x0309FFE0


class DeletePreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.variants = {}
        for output, suffix, flags in ((PREVIEW, "preview", []), (WRITE_TEST, "write-test", ["--enable-writes"])):
            subprocess.run(
                ["python3", "tools/build_delete_preview.py", "NANOV200.IMG",
                 str(BASE), str(output), *flags],
                cwd=ROOT, check=True,
            )
            image = load(str(output / f"NANOV200-delete-emulator-{suffix}.IMG"))
            symbols = {
                name: int(address, 16)
                for address, _, name in map(str.split, (output / "delete-preview.nm").read_text().splitlines())
            }
            cls.variants[suffix] = image, symbols

    def prepare(self, suffix):
        self.image, self.symbols = self.variants[suffix]
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_MCLASS | UC_MODE_THUMB)
        self.cpu.mem_map(0x03000000, 0x100000)
        self.cpu.mem_map(0xe000e000, 0x1000)
        kernel = self.image.modules[0]
        for kind in ("code", "data"):
            offset = self.image.l2f(kernel[kind + "_load"])
            length = kernel[kind + "_len"]
            self.cpu.mem_write(kernel[kind + "_exec"], bytes(self.image.d[offset:offset + length]))
        self.store(0x0300C33C, 0x200)
        self.key = 0
        self.stock_key_input = False
        self.volume = 2
        self.mount_result = 0
        self.in_browser = False
        self.render_playback = False
        self.m3u = False
        self.favorites = None
        self.sorting = False
        self.flash_reads = []
        self.operations = []
        self.windows = []
        self.library = {}
        self.entries = {(4, 18): (b"SAME    MP3", 13, 4096)}

        def emulate_hardware(machine, address, size, data):
            if address == 0x0306B404:
                r0 = machine.reg_read(UC_ARM_REG_R0)
                offset = machine.reg_read(UC_ARM_REG_R1)
                length = machine.reg_read(UC_ARM_REG_R2)
                dest = machine.reg_read(UC_ARM_REG_R3)
                if r0 == 0x30000:
                    payload = bytes(self.image.d[offset:offset + length])
                    self.assertEqual(len(payload), length)
                    self.flash_reads.append((offset, length))
                    machine.mem_write(dest, payload)
                    result = 0
                else:
                    self.assertEqual(r0, 0x30001)
                    result = -1
                    for base, payload in self.library.items():
                        if base <= offset and offset + length <= base + len(payload):
                            start = offset - base
                            machine.mem_write(dest, payload[start:start + length])
                            result = 0
                            break
            elif address == 0x030632DA and not self.stock_key_input:
                result = self.key
            elif address == 0x0306B7F6 and self.sorting:
                self.assertEqual(machine.reg_read(UC_ARM_REG_R0), 0x30001)
                offset = machine.reg_read(UC_ARM_REG_R1) * 512
                length = machine.reg_read(UC_ARM_REG_R2) * 512
                source = machine.reg_read(UC_ARM_REG_R3)
                self.library[offset] = bytes(machine.mem_read(source, length))
                result = 0
            elif address == 0x03057A08 and self.sorting:
                dest = machine.reg_read(UC_ARM_REG_R0)
                length = machine.reg_read(UC_ARM_REG_R1)
                machine.mem_write(dest, bytes(length))
                result = 0
            elif address == 0x0305B44A:
                dest = machine.reg_read(UC_ARM_REG_R0)
                directory = machine.reg_read(UC_ARM_REG_R1)
                index = machine.reg_read(UC_ARM_REG_R2)
                entry = self.entries.get((directory, index))
                result = 1
                if entry:
                    name, cluster, length = entry
                    record = bytearray(32)
                    record[:11] = name
                    struct.pack_into("<H", record, 20, cluster >> 16)
                    struct.pack_into("<HI", record, 26, cluster & 0xffff, length)
                    machine.mem_write(dest, bytes(record))
                    result = 0
            elif address == 0x0305B302:
                dest = machine.reg_read(UC_ARM_REG_R0)
                descriptor = machine.reg_read(UC_ARM_REG_R1)
                index = machine.reg_read(UC_ARM_REG_R2)
                self.assertEqual(self.word(descriptor), 7)
                record = bytearray(32)
                if index == 23:
                    record[0] = 0x85
                else:
                    self.assertEqual(index, 24)
                    record[0] = 0xc0
                    _, cluster, length = self.entries[7, 23]
                    struct.pack_into("<I", record, 8, length)
                    struct.pack_into("<I", record, 20, cluster)
                machine.mem_write(dest, bytes(record))
                result = 0
            elif address == 0x0305FAF4:
                machine.mem_write(machine.reg_read(UC_ARM_REG_R0), self.entries[7, 23][0])
                result = 0
            elif address == 0x03071466 and self.favorites is not None:
                self.assertEqual(machine.reg_read(UC_ARM_REG_R0), 2 if self.volume == 2 else 3)
                self.assertEqual(machine.reg_read(UC_ARM_REG_R2), 0x1000)
                dest = machine.reg_read(UC_ARM_REG_R1)
                machine.mem_write(dest, self.favorites)
                result = 0
            elif address == 0x0305E3E8 and (self.in_browser or self.m3u or self.favorites is not None):
                self.assertEqual((machine.reg_read(UC_ARM_REG_R0), machine.reg_read(UC_ARM_REG_R1)), (7, 23))
                dest = machine.reg_read(UC_ARM_REG_R3)
                machine.mem_write(dest, "NEXT.MP3\0".encode("utf-16le"))
                result = 0
            elif address in {
                0x0306A4A6, 0x0306A4FE, 0x03068F66, 0x03068F78, 0x03069002,
                0x03068F92, 0x03068F3E, 0x03065B62, 0x030500F6,
                0x0306AF7C, 0x0306AFC8, 0x03050138, 0x0307C34C,
                0x03059A18, 0x03059970, 0x03065898,
            } or (address == 0x0307CB4E and not (self.in_browser or self.render_playback)) or (
                address == 0x0307AB88 and self.in_browser
            ):
                result = 0
            elif address in (0x0308331C, 0x0305F392, 0x0305F41E, 0x0305E75C, 0x0305ADD6, 0x0306B302):
                self.assertEqual(suffix, "write-test")
                self.operations.append(address)
                r0 = machine.reg_read(UC_ARM_REG_R0)
                r1 = machine.reg_read(UC_ARM_REG_R1)
                result = 0
                if address == 0x0308331C:
                    self.assertEqual(r0, 1)
                    self.store(0x030045AC, 0xffffffff)
                    machine.mem_write(0x03078844, b"\0")
                    result = 1
                elif address == 0x0305F392:
                    self.assertEqual((r0, bytes(machine.mem_read(r1, 11))), (4, b"SAME    MP3"))
                elif address == 0x0305E75C:
                    self.assertEqual(r0, 13)
                elif address == 0x0305ADD6:
                    self.assertEqual(r0, self.volume)
                    result = self.mount_result
                elif address == 0x0306B302:
                    descriptor = bytes(machine.mem_read(r1, 32))
                    self.windows.append((r0, descriptor))
                    machine.mem_write(0x03004b10, descriptor)
                else:
                    self.fail("FAT target was sent to the exFAT backend")
            else:
                return
            machine.reg_write(UC_ARM_REG_R0, result)
            machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

        self.cpu.hook_add(UC_HOOK_CODE, emulate_hardware)

    def store(self, address, value):
        self.cpu.mem_write(address, struct.pack("<I", value))

    def word(self, address):
        return struct.unpack("<I", self.cpu.mem_read(address, 4))[0]

    def call(self, address, stop=RETURN, *, r0=0, r1=0, r2=0, r3=0, steps=350000):
        self.cpu.reg_write(UC_ARM_REG_SP, 0x03040000)
        self.cpu.reg_write(UC_ARM_REG_LR, RETURN | 1)
        self.cpu.reg_write(UC_ARM_REG_R0, r0)
        self.cpu.reg_write(UC_ARM_REG_R1, r1)
        self.cpu.reg_write(UC_ARM_REG_R2, r2)
        self.cpu.reg_write(UC_ARM_REG_R3, r3)
        self.cpu.emu_start(address | 1, stop, count=steps)
        self.assertEqual(self.cpu.reg_read(UC_ARM_REG_PC), stop)
        return self.cpu.reg_read(UC_ARM_REG_R0)

    def load_playback(self):
        self.assertTrue(self.image.header_copy_ok())
        self.assertEqual(self.image.crc_calc(), self.image.crc_stored())
        self.call(0x0306B754, r0=27, r1=7)
        overlay = self.image.modules[27]
        load_offset = self.image.l2f(overlay["code_load"])
        self.assertGreater(overlay["code_len"], 0x33e4)
        self.assertIn((load_offset & ~0x1FF, 0x200), self.flash_reads)
        self.assertEqual(self.word(UPDATE_SLOT), self.symbols["delete_preview_update"] | 1)

        self.store(0x03077D08, 0x30002 if self.volume == 2 else 0x40000)
        self.cpu.mem_write(0x03077D00, b"\x02")
        self.call(0x0306B754, r0=34, r1=7)
        self.store(0x0300C778, 4)
        self.store(0x0300C77C, 19)
        self.cpu.mem_write(0x0300C836, b"\x63\0")
        self.store(0x03077AF4, 42)
        self.cpu.mem_write(0x03077AF0, b"\x01")
        self.store(0x03077D40, 23)
        self.store(0x03077F44, 24)
        for handle in range(8):
            self.cpu.mem_write(0x03078844 + handle * 0x27e, b"\x01")
        self.call(0x0305BE3C)
        self.assertEqual(self.word(0x03077AF4), 0)
        self.assertEqual(self.cpu.mem_read(0x03077AF0, 1), b"\0")
        self.assertEqual(self.word(0x03077D40), 0xffffffff)
        self.assertEqual(self.word(0x03077F44), 0xffffffff)
        for handle in range(8):
            self.assertEqual(self.cpu.mem_read(0x03078844 + handle * 0x27e, 1), b"\0")
        self.assertEqual(self.word(MOUNT_STATE), 1)
        self.assertEqual(self.call(0x03085C08, 0x03085C4C), 0)
        self.assertEqual(self.word(MOUNT_STATE + 4), 0x30002 if self.volume == 2 else 0x40000)
        self.assertEqual(self.word(MOUNT_STATE + 8), 1)

    def test_stock_loader_loads_relocated_prompt_and_preserves_unrelated_keys(self):
        self.prepare("preview")
        self.load_playback()
        self.key = 0x40000008
        self.assertEqual(self.call(KEY_CALL, KEY_CALL + 4), self.key)
        self.assertEqual(self.word(PLAYBACK_WIDGET), 0)
        self.press_play_twice()
        self.assertEqual(self.word(PLAYBACK_WIDGET), CONFIRMATION_WIDGET)
        self.assertFalse(self.word(0x03005EEC) & (1 << 9))

        self.key = 0x40000004
        self.call(0x03058A44)
        self.key = 0x40000040
        self.call(0x03058A44)
        self.assertEqual(self.word(PLAYBACK_WIDGET), 0)
        self.call(self.word(UPDATE_SLOT) & ~1)
        self.assertEqual(self.word(self.symbols["delete_preview_answer"]), 1)
        self.assertEqual(self.word(0x030045AC), 0)

        self.press_play_twice()
        self.assertEqual(self.word(PLAYBACK_WIDGET), CONFIRMATION_WIDGET)
        self.call(0x0305BE3C)
        self.assertEqual(self.call(0x03085C08, 0x03085C4C), 0)
        self.assertEqual(self.word(MOUNT_STATE), 2)
        self.assertEqual(self.word(MOUNT_STATE + 8), 2)
        self.key = 0x40000004
        self.call(0x03058A44)
        self.key = 0x40000040
        self.call(0x03058A44)
        self.call(self.word(UPDATE_SLOT) & ~1)
        self.assertEqual(self.word(self.symbols["delete_preview_answer"]), 0)

    def test_single_play_press_keeps_its_stock_action(self):
        self.prepare("preview")
        self.load_playback()
        self.stock_key_input = True
        self.play_release(40)
        self.store(0x0300C02C, 76)
        self.assertEqual(self.call(KEY_CALL, KEY_CALL + 4), 0x40000040)
        self.assertEqual(self.word(PLAYBACK_WIDGET), 0)

    def test_yes_and_no_clear_the_dialog_from_the_playback_frame(self):
        layouts = (
            (0, 0, 0x0307CC1A),
            (1, 0, 0x0307C1EE),
            (2, 0, 0x0307C43C),
            (2, 1, 0x0307C7C6),
        )
        for (layout, detail, after_clear), accepted in product(layouts, (False, True)):
            with self.subTest(layout=layout, detail=detail, accepted=accepted):
                self.prepare("preview")
                self.load_playback()
                self.cpu.mem_write(0x0300E5DB, bytes([layout]))
                self.cpu.mem_write(0x0300C29E, bytes([detail]))
                self.press_play_twice()
                self.assertEqual(self.word(PLAYBACK_WIDGET), CONFIRMATION_WIDGET)
                if accepted:
                    self.key = 0x40000004
                    self.call(0x03058A44)
                self.key = 0x40000040
                self.call(0x03058A44)
                self.assertEqual(self.word(PLAYBACK_WIDGET), 0)

                self.render_playback = True
                framebuffer = 0x03006C70
                self.cpu.mem_write(framebuffer, b"\xff" * 0x280)
                # Run the stock update through its full-frame clear.
                self.call(self.word(UPDATE_SLOT) & ~1, after_clear)
                self.assertEqual(bytes(self.cpu.mem_read(framebuffer, 0x280)), bytes(0x280))
                self.assertEqual(self.word(self.symbols["delete_preview_answer"]), int(accepted))

    def test_two_individual_play_releases_reach_confirmation_in_both_images(self):
        for variant in ("preview", "write-test"):
            with self.subTest(variant=variant):
                self.prepare(variant)
                self.load_playback()
                self.stock_key_input = True
                for tick, expected in ((40, 0x40000040), (52, 0)):
                    self.store(0x0300C02C, tick)
                    self.store(0x0300BDEC, 0x40000040)
                    self.store(0x0300BDF0, 1)
                    self.assertEqual(self.call(KEY_CALL, KEY_CALL + 4), expected)
                self.assertEqual(self.word(PLAYBACK_WIDGET), CONFIRMATION_WIDGET)
                self.assertEqual(self.operations, [])

                self.stock_key_input = False
                self.key = 0x40000004
                self.call(0x03058A44)
                self.key = 0x40000040
                self.call(0x03058A44)
                self.call(self.word(UPDATE_SLOT) & ~1)
                self.assertEqual(self.word(self.symbols["delete_preview_answer"]), 4 if variant == "write-test" else 1)

    def test_preview_reports_when_the_playing_file_cannot_be_verified(self):
        self.prepare("preview")
        self.load_playback()
        self.store(MOUNT_STATE + 8, 0)
        self.press_play_twice()
        self.assertEqual(self.word(PLAYBACK_WIDGET), 0x03004654)
        self.assertEqual(self.word(0x030045AC), 0)

    def test_write_fixture_handles_cancel_and_uses_stock_refresh_after_yes(self):
        self.prepare("write-test")
        self.load_playback()
        for accepted in (False, True):
            self.press_play_twice()
            if accepted:
                self.key = 0x40000004
                self.call(0x03058A44)
            self.key = 0x40000040
            self.call(0x03058A44)
            self.call(self.word(UPDATE_SLOT) & ~1)
            self.assertEqual(self.word(self.symbols["delete_preview_answer"]), 4 if accepted else 0)
            if not accepted:
                self.assertEqual(self.operations, [])
        self.assertEqual(self.operations, [0x0308331C, 0x0305F392, 0x0305E75C, 0x0305ADD6, 0x0306B302])
        self.assertEqual(self.windows, [(0x0d, struct.pack("<H28xH", 1, 0x6a00))])
        self.assertEqual(self.cpu.mem_read(0x0300C08E, 2), b"\x01\0")
        self.assertEqual(self.cpu.mem_read(0x0300C0B8, 1), b"\0")

    def test_refresh_targets_the_card_and_skips_scanning_if_it_cannot_mount(self):
        for mount_result in (0, -1):
            with self.subTest(mount_result=mount_result):
                self.prepare("write-test")
                self.volume = 4
                self.mount_result = mount_result
                self.load_playback()
                self.press_play_twice()
                self.key = 0x40000004
                self.call(0x03058A44)
                self.key = 0x40000040
                self.call(0x03058A44)
                self.call(self.word(UPDATE_SLOT) & ~1)
                self.assertEqual(self.word(self.symbols["delete_preview_answer"]), 4)
                self.assertEqual(self.operations[-1], 0x0306B302 if not mount_result else 0x0305ADD6)
                self.assertEqual(self.cpu.mem_read(0x0300C08E, 1), bytes([not mount_result]))
                if not mount_result:
                    self.assertEqual(self.cpu.mem_read(0x0300C05A, 1), b"\x04")

    def all_songs_queue(self, shuffle=False):
        queue = bytearray(0x120)
        struct.pack_into("<HH", queue, 0, 4 if shuffle else 2, 1)
        struct.pack_into("<H", queue, 0xc, int(shuffle))
        struct.pack_into("<II", queue, 0x34, 4, 19)
        struct.pack_into("<H", queue, 0xf2, 1)
        struct.pack_into("<II", queue, 0xf8, 24, 24 + 0x52000)
        self.cpu.mem_write(0x0300C744, bytes(queue))
        self.cpu.mem_write(0x0300C05B, bytes([self.volume]))
        self.entries[7, 23] = (b"NEXT    MP3", 34, 5120)
        if shuffle:
            self.call(0x0306B754, r0=33, r1=1)
            self.cpu.mem_write(0x03035400, struct.pack("<5H", 1, 0, 1, 2, 3))
            self.cpu.mem_write(0x03039402, struct.pack("<2H", 4, 0))
            self.store(0x0300C02C, 0)
            self.store(0xE000E018, 2)
        self.library = {
            (24 + 0x52000) << 9: struct.pack("<4H", 6, 8, 9, 10) if shuffle else struct.pack("<2H", 6, 9),
            (24 << 9) + (9 << 11): struct.pack("<II", 7, 24) + bytes(0x7f8),
        }
        return queue

    def replace_index(self, volume, *, keep_successor=True):
        self.store(0x0300C030, 30)
        self.store(0x0300C07C, 3)
        self.cpu.mem_write(0x0300C05B, bytes([volume]))
        self.library = {
            (30 + 0x52000) << 9: struct.pack("<HHH", 1, 12, 3),
            (30 << 9) + (12 << 11): struct.pack("<IIIII", 7, 24 if keep_successor else 25, 0, 0, 0),
            (30 << 9) + (1 << 11): struct.pack("<IIIII", 11, 24, 0, 0, 0),
            (30 << 9) + (3 << 11): struct.pack("<IIIII", 7, 24, 0, 100, 200),
        }

    def play_release(self, tick):
        self.store(0x0300C02C, tick)
        self.store(0x0300BF4C, 0x40000040)
        self.store(0x0300BF68, 1)
        self.assertEqual(self.call(KEY_CALL, KEY_CALL + 4), 0)

    def press_play_twice(self):
        self.stock_key_input = True
        try:
            start = self.word(0x0300C02C) + 40
            self.play_release(start)
            self.play_release(start + 12)
            self.store(0x0300C02C, start + 36)
            self.assertEqual(self.call(KEY_CALL, KEY_CALL + 4), 0)
            self.assertTrue(self.word(0x03005EEC) & (1 << 9))
            self.assertEqual(self.call(0x0307B6B8, 0x0307B6BC, r0=9), 0)
        finally:
            self.stock_key_input = False

    def confirm_removal(self):
        self.press_play_twice()
        self.key = 0x40000004
        self.call(0x03058A44)
        self.key = 0x40000040
        self.call(0x03058A44)
        self.call(self.word(UPDATE_SLOT) & ~1)
        self.assertEqual(self.word(self.symbols["delete_preview_answer"]), 4)

    def finish_refresh(self):
        self.store(0x0300E5D4, 1)
        self.cpu.reg_write(UC_ARM_REG_R8, 0x0300E5D0)
        # The tail restores seven saved registers and the caller's PC.
        self.store(0x03040000 + 0x20 + 7 * 4, RETURN | 1)
        self.call(0x0307A958)

    def load_resumed_browser(self):
        self.call(0x0306B754, r0=23, r1=7)
        self.in_browser = True
        self.call(0x0307C47C)
        self.call(0x0307A88E)
        self.call(0x0307C536, r0=0x03004B10)
        count = self.call(0x0307A5F4)
        self.cpu.mem_write(0x0300E968, struct.pack("<H", count))
        self.key = 0
        return count

    def test_stock_choice_finds_the_same_file_after_reindexing(self):
        for volume, shuffle, exfat in ((2, False, False), (4, False, False), (4, True, True)):
            with self.subTest(volume=volume, shuffle=shuffle, exfat=exfat):
                self.prepare("write-test")
                self.volume = volume
                self.load_playback()
                self.store(0x030045AC, 0xffffffff)
                self.cpu.mem_write(0x03078844, b"\0")
                if exfat:
                    self.cpu.mem_write(0x03077D00, b"\x03")
                device = 0x30002 if volume == 2 else 0x40000
                song = 0x03011000
                queue = self.all_songs_queue(shuffle)
                choose = self.symbols["delete_track_choose_successor"]
                find = self.symbols["delete_track_find_successor"]
                self.assertEqual(self.call(choose, r0=song, r1=device, r2=4, r3=18), 1)
                if shuffle:
                    self.assertEqual(self.cpu.mem_read(0x03035400, 2), b"\x02\0")
                self.assertEqual(self.cpu.mem_read(0x0300C744, 0x120), queue)
                self.assertEqual(bytes(self.cpu.mem_read(song, 31)),
                                 struct.pack("<IIIII", device, 7, 23, 34, 5120) + b"NEXT    MP3")
                self.assertEqual(self.cpu.mem_read(0x03078844, 1), b"\0")
                self.replace_index(volume)
                self.assertEqual(self.call(find, r0=song), 2)
                self.store(0x03077D08, 0x30002 if volume == 4 else 0x40000)
                self.assertEqual(self.call(find, r0=song), 0)
                self.store(0x03077D08, device)
                self.entries[7, 23] = (b"NEXT    MP3", 35, 5120)
                self.assertEqual(self.call(find, r0=song), 0)

    def test_confirmed_removal_routes_reindexed_row_to_the_browser_once(self):
        for keep_successor, remounted in ((True, False), (False, False), (True, True)):
            with self.subTest(keep_successor=keep_successor, remounted=remounted):
                self.prepare("write-test")
                self.volume = 4
                self.load_playback()
                self.all_songs_queue()
                self.confirm_removal()

                self.call(0x0306B754, r0=5, r1=7)
                self.cpu.mem_write(0x0307D9D8, bytes(0xd4c))
                self.replace_index(self.volume, keep_successor=keep_successor)
                self.finish_refresh()
                if not keep_successor:
                    self.assertEqual(self.windows[-1][0], 1)
                    continue

                self.assertEqual(self.windows[-1], (2, struct.pack("<HH26xH", 0, 0x82, 0x6a1e)))
                self.call(0x0306B754, r0=23, r1=7)
                self.in_browser = True
                self.cpu.mem_write(0x0300E968, struct.pack("<H", 3))
                self.cpu.mem_write(0x0300E974, b"\x02\0")
                self.store(0x0300E734, 30)
                self.store(0x0300E740, 30 + 0x52000)
                self.key = 0
                if remounted:
                    self.call(0x0305BE3C)
                    self.assertEqual(self.call(0x0307B180), 0)
                    self.assertEqual(self.windows[-1][0], 2)
                    continue

                self.call(0x0307CACE)
                self.assertEqual(self.windows[-1][0], 3)
                self.assertEqual(self.word(0x0300C778), 7)
                self.assertEqual(self.word(0x0300C77C), 24)
                self.assertEqual(self.cpu.mem_read(0x0300E974, 2), b"\x01\0")
                self.assertEqual(self.cpu.mem_read(0x0300E97E, 2), b"\x01\0")
                self.assertEqual(self.call(0x0307B180), 0)
                self.call(0x0306B754, r0=34, r1=7)
                self.assertEqual(self.call(0x03085C08, 0x03085C4C), 0)
                self.assertEqual(bytes(self.cpu.mem_read(0x03078844, 12)), b"\x01NEXT    MP3")
                self.assertEqual(self.word(0x03078844 + 0x14), 34)

    def test_m3u_restores_the_selected_occurrence_after_stock_clears_the_cache(self):
        self.prepare("write-test")
        self.volume = 4
        self.load_playback()
        self.all_songs_queue()
        self.m3u = True
        self.cpu.mem_write(0x0300C744, struct.pack("<2H", 5, 3))
        self.cpu.mem_write(0x0300C836, struct.pack("<H", 0xe))
        self.cpu.mem_write(0x0300D140, struct.pack("<H", 5))
        self.cpu.mem_write(0x0300D144, struct.pack("<10I", 7, 24, 8, 4, 4, 19, 7, 24, 4, 19))
        self.confirm_removal()

        self.call(0x0306B754, r0=5, r1=7)
        self.call(0x0306C0B6)
        self.assertEqual(self.cpu.mem_read(0x0300D140, 2), bytes(2))
        self.replace_index(self.volume)
        self.finish_refresh()
        self.assertEqual(self.windows[-1][0], 2)
        self.assertEqual(self.cpu.mem_read(0x0300D140, 2), struct.pack("<H", 3))
        self.assertEqual(self.cpu.mem_read(0x0300D144, 24), struct.pack("<6I", 7, 24, 8, 4, 7, 24))

        self.call(0x0306B754, r0=23, r1=7)
        self.in_browser = True
        self.cpu.mem_write(0x0300E968, struct.pack("<H", 3))
        self.key = 0
        self.call(0x0307CACE)
        self.assertEqual(self.windows[-1][0], 3)
        self.assertEqual(self.cpu.mem_read(0x0300C744, 4), struct.pack("<2H", 3, 3))
        self.assertEqual(self.word(0x0300C778), 7)
        self.assertEqual(self.word(0x0300C77C), 24)

    def test_favorites_resumes_in_the_rebuilt_favorites_list(self):
        self.prepare("write-test")
        self.volume = 4
        self.load_playback()
        queue = self.all_songs_queue()
        struct.pack_into("<H", queue, 0xf2, 2)
        self.cpu.mem_write(0x0300C744, bytes(queue))
        self.cpu.mem_write(0x0300C084, struct.pack("<H", 2))
        self.favorites = struct.pack("<4I3984xII88x", 4, 19, 7, 24, 2, 0x55aa55aa)
        self.confirm_removal()

        self.call(0x0306B754, r0=5, r1=7)
        self.favorites = struct.pack("<4I3984xII88x", 8, 4, 7, 24, 2, 0x55aa55aa)
        self.finish_refresh()
        self.assertEqual(self.windows[-1][0], 2)
        self.assertEqual(self.load_resumed_browser(), 2)
        self.call(0x0307CACE)
        self.assertEqual(self.windows[-1][0], 3)
        self.assertEqual(self.cpu.mem_read(0x0300C836, 2), struct.pack("<H", 2))
        self.assertEqual(self.word(0x0300C778), 7)
        self.assertEqual(self.word(0x0300C77C), 24)

    def test_grouped_successor_reopens_the_new_ancestor_rows(self):
        for kind, depth, map_offset in ((4, 1, 0x52140), (3, 2, 0x52020), (5, 3, 0x521e0)):
            with self.subTest(kind=kind):
                self.prepare("write-test")
                self.load_playback()
                queue = self.all_songs_queue()
                struct.pack_into("<H", queue, 0xf2, kind)
                struct.pack_into("<H", queue, 0xf4, depth)
                struct.pack_into("<I", queue, 0xfc, 24 + map_offset)
                struct.pack_into("<H", queue, 0x110 + 2 * depth, 4)
                self.cpu.mem_write(0x0300C744, bytes(queue))
                self.library[((24 + map_offset) << 9) + 8] = struct.pack("<2H", 6, 9)
                self.confirm_removal()

                self.call(0x0306B754, r0=5, r1=7)
                self.replace_index(self.volume)
                self.library[(30 + map_offset) << 9] = struct.pack("<3H", 1, 3, 12)
                self.store(0x0300C07E + (kind - 3) * 2, 2)
                for level in range(depth):
                    table = (30 + map_offset + 0x20 + level * 0x80) << 9
                    self.library[table] = struct.pack("<8H", 0, 0, 1, 0, 1, 1,
                                                     2 if level + 1 == depth else 1, 0)
                self.finish_refresh()
                self.assertEqual(self.windows[-1][0], 2)
                self.assertEqual(self.cpu.mem_read(0x0300C36A, 2), struct.pack("<H", kind))

                self.assertEqual(self.load_resumed_browser(), 2)
                self.call(0x0307CACE)
                self.assertEqual(self.windows[-1][0], 3)
                self.assertEqual(self.cpu.mem_read(0x0300C744, 4), struct.pack("<2H", 2, 2))
                self.assertEqual(self.cpu.mem_read(0x0300C838, 2), struct.pack("<H", depth))
                self.assertEqual(self.word(0x0300C778), 7)
                self.assertEqual(self.word(0x0300C77C), 24)

    def test_artist_play_all_resumes_inside_the_flattened_artist_queue(self):
        self.prepare("write-test")
        self.load_playback()
        queue = self.all_songs_queue()
        struct.pack_into("<HH", queue, 0xf2, 3, 1)
        struct.pack_into("<I", queue, 0xfc, 24 + 0x52020)
        struct.pack_into("<H", queue, 0x112, 4)
        self.cpu.mem_write(0x0300C744, bytes(queue))
        self.cpu.mem_write(0x0300C0B9, b"\x02\0\0")
        self.library[((24 + 0x52020) << 9) + 8] = struct.pack("<2H", 6, 9)
        self.confirm_removal()

        self.call(0x0306B754, r0=5, r1=7)
        self.replace_index(self.volume)
        self.library[(30 + 0x52020) << 9] = struct.pack("<3H", 1, 3, 12)
        self.library[(30 + 0x52040) << 9] = struct.pack("<8H", 0, 0, 1, 1, 1, 1, 2, 2)
        self.library[(30 + 0x520c0) << 9] = struct.pack("<12H", 0, 0, 1, 0, 1, 1, 1, 0, 2, 2, 1, 0)
        self.library[(30 << 9) + (3 << 11)] = struct.pack("<IIIII", 8, 4, 0, 0, 0)
        name = "NEXT.MP3".encode("utf-16le")
        self.library[(30 << 9) + (12 << 11) + 0x400] = name.ljust(0x100, b"\0")
        self.store(0x0300C07E, 2)
        self.finish_refresh()
        self.assertEqual(self.windows[-1][0], 2)
        self.assertEqual(self.load_resumed_browser(), 3)
        self.call(0x0307CACE)
        self.assertEqual(self.windows[-1][0], 3)
        self.assertEqual(self.cpu.mem_read(0x0300C744, 4), struct.pack("<2H", 2, 2))
        self.assertEqual(self.cpu.mem_read(0x0300C838, 2), struct.pack("<H", 1))
        self.assertEqual(self.cpu.mem_read(0x0300C856, 2), struct.pack("<H", 1))
        self.assertEqual(self.word(0x0300C778), 7)
        self.assertEqual(self.word(0x0300C77C), 24)
        self.assertEqual(self.cpu.mem_read(0x0300C0B9, 3), b"\x02\0\0")

    def sort_stock_library(self, base, records):
        for index, (directory, entry, *tags) in enumerate(records):
            record = bytearray(0x800)
            struct.pack_into("<2I", record, 0, directory, entry)
            record[0x14:0x1c] = f"{index + 1:04}".encode("utf-16le")
            for field, tag in enumerate(tags, start=4):
                name = tag.encode("utf-16le")
                offset = field * 0x100
                record[offset:offset + len(name)] = name
            self.library[(base << 9) + index * 0x800] = bytes(record)

        self.store(0x0300C030, base)
        self.cpu.mem_write(0x0300C07A, struct.pack("<H", len(records)))
        self.call(0x0306B754, r0=40, r1=7, steps=3500000)
        self.sorting = True
        try:
            self.call(0x0307B2BC, r0=0x0300C07A, r1=base, steps=3500000)
        finally:
            self.sorting = False

    def test_genre_play_all_and_artist_within_genre_resume_on_the_expected_row(self):
        for scope in (1, 2):
            with self.subTest(scope=scope):
                self.prepare("write-test")
                self.volume = 4
                self.load_playback()
                queue = self.all_songs_queue()
                struct.pack_into("<HH", queue, 0xf2, 5, 2)
                struct.pack_into("<H", queue, 0x102, scope - 1)
                struct.pack_into("<H", queue, 0x114, 4)
                struct.pack_into("<I", queue, 0xfc, 24 + 0x521e0)
                self.cpu.mem_write(0x0300C744, bytes(queue))
                self.library[((24 + 0x521e0) << 9) + 8] = struct.pack("<2H", 6, 9)
                self.library[(24 + 0x52200) << 9] = struct.pack("<4H", 5 - scope, 0, 2, scope + 1)
                self.cpu.mem_write(0x0300C082, struct.pack("<H", 1))
                self.confirm_removal()

                self.library.clear()
                records = [
                    (11, 3, "Alpha", "Blue", "First", "Folk"),
                    (8, 4, "Beta", "Nova", "Middle", "Rock"),
                    (7, 24, "Zeta", "Odin" if scope == 1 else "Nova", "Zigzag", "Rock"),
                ]
                if scope == 2:
                    records.append((9, 5, "Other", "Odin", "Other", "Rock"))
                self.sort_stock_library(30, records)
                self.call(0x0306B754, r0=5, r1=7)
                self.finish_refresh()
                self.assertEqual(self.windows[-1][0], 2)
                self.assertEqual(self.load_resumed_browser(), 3)
                self.call(0x0307CACE)
                self.assertEqual(self.windows[-1][0], 3)
                self.assertEqual(self.cpu.mem_read(0x0300C744, 4), struct.pack("<2H", 2, 2))
                self.assertEqual(self.cpu.mem_read(0x0300C838, 2), struct.pack("<H", 2))
                self.assertEqual(self.word(0x0300C778), 7)
                self.assertEqual(self.word(0x0300C77C), 24)

    def test_finds_the_album_path_in_tables_written_by_the_stock_sorter(self):
        self.prepare("write-test")
        self.load_playback()
        queue = self.all_songs_queue()
        struct.pack_into("<HH", queue, 0xf2, 4, 1)
        struct.pack_into("<I", queue, 0xfc, 24 + 0x52140)
        self.cpu.mem_write(0x0300C744, bytes(queue))
        self.library[(24 + 0x52140) << 9] = struct.pack("<2H", 6, 9)
        self.confirm_removal()

        self.library.clear()
        self.sort_stock_library(30, [
            (7, 24, "Zeta", "Nova", "Second", "Rock"),
            (11, 3, "Alpha", "Blue", "First", "Folk"),
            (8, 4, "Beta", "Nova", "Second", "Rock"),
        ])
        self.call(0x0306B754, r0=5, r1=7)
        self.finish_refresh()
        self.assertEqual(self.windows[-1][0], 2)
        self.assertEqual(self.load_resumed_browser(), 2)
        self.call(0x0307CACE)
        self.assertEqual(self.windows[-1][0], 3)
        self.assertEqual(self.word(0x0300C778), 7)
        self.assertEqual(self.word(0x0300C77C), 24)
        self.call(0x0306B754, r0=34, r1=7)
        self.assertEqual(self.call(0x03085C08, 0x03085C4C), 0)
        self.assertEqual(bytes(self.cpu.mem_read(0x03078844, 12)), b"\x01NEXT    MP3")

    def test_manual_refresh_and_shuffle_all_open_normally(self):
        self.prepare("write-test")
        self.call(0x0306B754, r0=5, r1=7)
        self.cpu.mem_map(0x40160000, 0x1000)
        self.store(0x40160050, 1 << 11)
        self.store(0x0300E5D4, 1)
        self.store(0x03040000, 0xDEADBEEF)
        self.flash_reads.clear()
        self.assertEqual(self.call(self.word(0x0300E5BC) & ~1), 0xffffffff)
        self.assertEqual([window for window, _ in self.windows], [1])
        self.assertEqual(self.flash_reads, [])

        self.replace_index(self.volume)
        self.cpu.mem_write(0x0300C07A, struct.pack("<H", 3))
        self.call(0x0306B754, r0=21, r1=7)
        self.cpu.mem_write(0x0300E600, b"\x01\0")
        self.call(0x0307ACFA)
        self.assertEqual((self.windows[-1][0], self.windows[-1][1][:4]), (2, b"\0\0\x82\0"))

        self.call(0x0306B754, r0=23, r1=7)
        self.in_browser = True
        self.call(0x0307B130, r0=0x03004B10)
        self.call(0x0307D20A)
        self.assertEqual(self.cpu.mem_read(0x0300E968, 2), b"\x03\0")
        self.assertEqual(self.cpu.mem_read(0x0300E974, 2), b"\0\0")
        self.store(0xE000E018, 2)
        self.call(0x0307CACE)
        window, playback = self.windows[-1]
        self.assertEqual((window, playback[:4]), (3, b"\x02\0\x5a\0"))
        self.assertEqual(self.cpu.mem_read(0x0300C744, 4), struct.pack("<HH", 3, 2))
        self.assertEqual(self.word(0x0300C778), 7)
        self.assertEqual(self.word(0x0300C77C), 24)
        self.assertEqual(self.cpu.mem_read(0x0300E97E, 2), b"\x01\0")

        self.in_browser = False
        self.call(0x0306B754, r0=27, r1=7)
        self.cpu.mem_write(0x0300C000, playback[2:4])
        self.key = 0x40001000  # Back event on the playback screen.
        self.call(self.word(0x0300E5C0) & ~1)
        self.assertEqual((self.windows[-1][0], self.windows[-1][1][:4]), (2, b"\x01\0\x5a\0"))
        self.call(0x0306B754, r0=23, r1=7)
        self.key = 0
        self.assertEqual(self.call(0x0307B180), 0)
        self.assertEqual(self.cpu.mem_read(0x0300C744, 4), struct.pack("<HH", 3, 2))

if __name__ == "__main__":
    unittest.main()
