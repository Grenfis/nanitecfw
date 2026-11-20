import struct
import sys
import unittest
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
    UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.rknano_fw import load

OPEN_PLAYBACK_FILE = 0x0305BE72
CLOSE_FILE = 0x0305C340
READ_TRACK_RECORD = 0x0306C2FC
START_PLAYBACK_OPEN = 0x03085C08
PLAYBACK_HANDLE_WRITTEN = 0x03085C4C
PLAYBACK_STATE = 0x0300C744
PLAYBACK_HANDLE = 0x030045AC
READ_LIBRARY = 0x0306B404
READ_FAT_ENTRY = 0x0305B44A
READ_EXFAT_ENTRY = 0x0305B302
NORMALIZE_EXFAT_NAME = 0x0305FAF4
STORAGE_STATE = 0x03077D00
FILE_TABLE = 0x03078844
STACK = 0x03040000
RETURN_ADDRESS = 0x03010000
SHORT_NAME = b"SAME    MP3"


class PlaybackFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image = load(str(ROOT / "NANOV200.IMG"))
        cls.code = []
        for index in (0, 34):
            module = image.modules[index]
            start = image.l2f(module["code_load"])
            cls.code.append((module["code_exec"], bytes(image.d[start:start + module["code_len"]])))

    def machine(self):
        cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        cpu.mem_map(0x03000000, 0x100000)
        for address, code in self.code:
            cpu.mem_write(address, code)
        cpu.reg_write(UC_ARM_REG_SP, STACK)
        cpu.reg_write(UC_ARM_REG_LR, RETURN_ADDRESS | 1)
        return cpu

    def open_file(self, source, device, directory, entry, first_cluster, *, exfat=False, missing=False):
        cpu = self.machine()
        cpu.mem_write(STORAGE_STATE, bytes([3 if exfat else 2]))
        cpu.mem_write(STORAGE_STATE + 8, struct.pack("<I", device))
        cpu.mem_write(0x03004904, b"R\0")
        kind = {1: 0x0A, 2: 8, 3: 0x63}[source]
        args = (directory, entry + 1) if source == 3 else (7, 4)
        cpu.mem_write(PLAYBACK_STATE + 0x34, struct.pack("<II", *args))
        cpu.mem_write(PLAYBACK_STATE + 0xF2, struct.pack("<H", kind))

        library = {}
        if source == 1:
            library = {
                0x0A600014: struct.pack("<H", 5),
                0x080000C0: struct.pack("<IIII", 0, 0, 9, 0),
                0x4800: struct.pack("<I", directory),
                0x4804: struct.pack("<I", entry + 1),
                0x4834: bytes(42),
            }
        elif source == 2:
            library = {
                0x0A800014: struct.pack("<H", 5),
                0x088000C0: struct.pack("<IIII", 0, 0, 9, 0),
                0x04004800: struct.pack("<I", directory),
                0x04004804: struct.pack("<I", entry + 1),
            }
        reads = []
        opened_sources = []

        def return_from_stub(machine, result=0):
            machine.reg_write(UC_ARM_REG_R0, result)
            machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

        def capture_source(machine, address, size, data):
            opened_sources.append(machine.reg_read(UC_ARM_REG_R3))

        def read_library(machine, address, size, data):
            self.assertEqual(machine.reg_read(UC_ARM_REG_R0), 0x00030001)
            location = machine.reg_read(UC_ARM_REG_R1)
            payload = library[location]
            self.assertEqual(machine.reg_read(UC_ARM_REG_R2), len(payload))
            machine.mem_write(machine.reg_read(UC_ARM_REG_R3), payload)
            reads.append(location)
            return_from_stub(machine)

        def read_fat_entry(machine, address, size, data):
            self.assertEqual(machine.reg_read(UC_ARM_REG_R1), directory)
            self.assertEqual(machine.reg_read(UC_ARM_REG_R2), entry)
            if missing:
                return_from_stub(machine, 1)
                return
            payload = bytearray(32)
            payload[:11] = SHORT_NAME
            struct.pack_into("<H", payload, 0x14, first_cluster >> 16)
            struct.pack_into("<HI", payload, 0x1A, first_cluster & 0xFFFF, 12345)
            machine.mem_write(machine.reg_read(UC_ARM_REG_R0), bytes(payload))
            return_from_stub(machine)

        def read_exfat_entry(machine, address, size, data):
            descriptor = machine.reg_read(UC_ARM_REG_R1)
            self.assertEqual(bytes(machine.mem_read(descriptor, 4)), struct.pack("<I", directory))
            index = machine.reg_read(UC_ARM_REG_R2)
            payload = bytearray(32)
            if index == entry:
                payload[0] = 0x85
            else:
                self.assertEqual(index, entry + 1)
                payload[0] = 0xC0
                struct.pack_into("<I", payload, 0x14, first_cluster)
            machine.mem_write(machine.reg_read(UC_ARM_REG_R0), bytes(payload))
            return_from_stub(machine)

        def normalize_name(machine, address, size, data):
            machine.mem_write(machine.reg_read(UC_ARM_REG_R0), SHORT_NAME)
            return_from_stub(machine)

        for address, stub in (
            (READ_LIBRARY, read_library), (READ_FAT_ENTRY, read_fat_entry),
            (READ_EXFAT_ENTRY, read_exfat_entry), (NORMALIZE_EXFAT_NAME, normalize_name),
            (OPEN_PLAYBACK_FILE, capture_source),
        ):
            cpu.hook_add(UC_HOOK_CODE, stub, begin=address, end=address)
        cpu.emu_start(START_PLAYBACK_OPEN | 1, PLAYBACK_HANDLE_WRITTEN, count=1500)

        self.assertEqual(cpu.reg_read(UC_ARM_REG_PC), PLAYBACK_HANDLE_WRITTEN)
        self.assertEqual(opened_sources, [source])
        self.assertEqual(reads, list(library))
        handle = struct.unpack("<i", cpu.mem_read(PLAYBACK_HANDLE, 4))[0]
        if missing:
            self.assertEqual(handle, -1)
            self.assertEqual(bytes(cpu.mem_read(FILE_TABLE, 1)), b"\x00")
            return None

        self.assertEqual(handle, 0)
        self.assertEqual(bytes(cpu.mem_read(FILE_TABLE, 12)), b"\x01" + SHORT_NAME)
        self.assertEqual(bytes(cpu.mem_read(PLAYBACK_STATE + 0x14, 11)), SHORT_NAME)
        if exfat:
            self.assertEqual(bytes(cpu.mem_read(FILE_TABLE + 0x22A, 4)), struct.pack("<I", directory))
        identity = (
            struct.unpack("<I", cpu.mem_read(STORAGE_STATE + 8, 4))[0],
            *struct.unpack("<I4xI4xI", cpu.mem_read(FILE_TABLE + 0x0C, 20)),
        )
        cpu.reg_write(UC_ARM_REG_R0, handle)
        old_stack = cpu.reg_read(UC_ARM_REG_SP)
        cpu.reg_write(UC_ARM_REG_LR, RETURN_ADDRESS | 1)
        cpu.emu_start(CLOSE_FILE | 1, RETURN_ADDRESS, count=100)
        self.assertEqual(cpu.reg_read(UC_ARM_REG_PC), RETURN_ADDRESS)
        self.assertEqual(cpu.reg_read(UC_ARM_REG_SP), old_stack)
        self.assertEqual(cpu.reg_read(UC_ARM_REG_R0), 0)
        self.assertEqual(bytes(cpu.mem_read(FILE_TABLE, 1)), b"\x00")
        return identity

    def test_both_library_sources_open_the_same_fat_record_as_direct_playback(self):
        identity = (0x30002, 0x125, 0x45678, 19)
        for source in (1, 2, 3):
            with self.subTest(source=source):
                device, directory, cluster, entry = identity
                self.assertEqual(self.open_file(source, device, directory, entry, cluster), identity)

    def test_identical_directory_entries_on_different_devices_still_have_distinct_identity(self):
        first = self.open_file(3, 0x30002, 0x125, 19, 0x45678)
        second = self.open_file(3, 0x40000, 0x125, 19, 0x45678)
        self.assertEqual(first[1:], second[1:])
        self.assertNotEqual(first[0], second[0])

    def test_exfat_handle_retains_directory_and_primary_entry(self):
        identity = (0x40000, 0x125, 0x45678, 19)
        device, directory, cluster, entry = identity
        self.assertEqual(self.open_file(3, device, directory, entry, cluster, exfat=True), identity)

    def test_missing_record_does_not_leave_an_active_playback_handle(self):
        self.assertIsNone(self.open_file(3, 0x30002, 0x125, 19, 0x45678, missing=True))

    def test_track_record_marks_time_ranges_even_for_a_segment_starting_at_zero(self):
        # The player copies this flag and the range into playback state +0x44..0x4C.
        for start, end in ((0, 0), (0, 1000), (1000, 2000)):
            with self.subTest(start=start, end=end):
                cpu = self.machine()
                output = 0x03012000
                for register, value in (
                    (UC_ARM_REG_R0, output), (UC_ARM_REG_R1, 0),
                    (UC_ARM_REG_R2, 4), (UC_ARM_REG_R3, 0),
                ):
                    cpu.reg_write(register, value)
                records = {
                    0x800: struct.pack("<H", 2),
                    0x1000: struct.pack("<II", 0x125, 19),
                    0x1008: bytes(4),
                    0x100C: struct.pack("<I", start),
                    0x1010: struct.pack("<I", end),
                }

                def read_record(machine, address, size, data):
                    location = machine.reg_read(UC_ARM_REG_R1)
                    length = machine.reg_read(UC_ARM_REG_R2)
                    payload = records.get(location, bytes(length))
                    self.assertEqual(len(payload), length)
                    machine.mem_write(machine.reg_read(UC_ARM_REG_R3), payload)
                    machine.reg_write(UC_ARM_REG_R0, 0)
                    machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

                cpu.hook_add(UC_HOOK_CODE, read_record, begin=READ_LIBRARY, end=READ_LIBRARY)
                cpu.emu_start(READ_TRACK_RECORD | 1, RETURN_ADDRESS, count=1500)
                self.assertEqual(cpu.reg_read(UC_ARM_REG_PC), RETURN_ADDRESS)
                flag, segment_start, segment_end = struct.unpack("<III", cpu.mem_read(output + 0x10, 12))
                self.assertEqual((flag, segment_start, segment_end), (int(bool(start or end)), start, end))


if __name__ == "__main__":
    unittest.main()
