import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_SP,
)
from tools.rknano_fw import load

ROOT = Path(__file__).resolve().parents[1]
CODE = 0x03021000
TARGET = 0x03022000
RETURN = 0x03010000
HANDLE = 0x030045AC
TRACK_RANGE = 0x0300C788
STORAGE = 0x03077D00
FILES = 0x03078844
ACTIVE_FILE = FILES + 0x27E
GENERATION = 17
GENERATION_ADDRESS = 0x03022100
NAME = b"SAME    MP3"
PLAYBACK_WIDGET = 0x0300E5B4
CONFIRMATION_WIDGET = 0x03004600
DIALOG_PARAMETERS = 0x0300BD58


class DeleteTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as name:
            elf = Path(name) / "target.elf"
            binary = Path(name) / "target.bin"
            subprocess.run(
                [
                    "arm-none-eabi-gcc", "-mthumb", "-mcpu=cortex-m3", "-Os",
                    "-Wall", "-Wextra", "-Werror", "-ffreestanding", "-nostdlib",
                    f"-Wl,-Ttext={CODE:#x},-e,delete_track_capture",
                    str(ROOT / "src/v2/delete_track_target.c"),
                    str(ROOT / "src/v2/delete_track_dialog.c"),
                    str(ROOT / "src/v2/delete_track_exfat.c"), "-o", str(elf),
                ],
                check=True,
            )
            subprocess.run(
                ["arm-none-eabi-objcopy", "-O", "binary", "-j", ".text", "-j", ".rodata",
                 str(elf), str(binary)],
                check=True,
            )
            cls.code = binary.read_bytes()
            symbols = subprocess.check_output(["arm-none-eabi-nm", str(elf)], text=True)
            cls.entries = {
                symbol: int(address, 16)
                for address, kind, symbol in (line.split() for line in symbols.splitlines())
                if kind == "T" and symbol.startswith("delete_track_")
            }

    def setUp(self):
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.cpu.mem_map(0x03000000, 0x100000)
        self.cpu.mem_write(CODE, self.code)
        self.cpu.mem_write(STORAGE, struct.pack("<B7xI", 2, 0x30002))
        self.cpu.mem_write(HANDLE, struct.pack("<I", 1))
        self.cpu.mem_write(ACTIVE_FILE, b"\x01" + NAME)
        self.cpu.mem_write(ACTIVE_FILE + 0x0C, struct.pack("<IIIII", 0x125, 12345, 0x45678, 0, 19))

    def call(self, name, generation=GENERATION, opened_device=0x30002, accepted=0):
        self.cpu.reg_write(UC_ARM_REG_SP, 0x03040000)
        self.cpu.reg_write(UC_ARM_REG_LR, RETURN | 1)
        self.cpu.reg_write(UC_ARM_REG_R0, TARGET)
        self.cpu.reg_write(UC_ARM_REG_R1, generation)
        self.cpu.reg_write(UC_ARM_REG_R2, opened_device)
        self.cpu.reg_write(UC_ARM_REG_R3, accepted)
        address = self.entries[name] if isinstance(name, str) else name
        self.cpu.emu_start(address | 1, RETURN, count=25000)
        self.assertEqual(self.cpu.reg_read(UC_ARM_REG_PC), RETURN)
        return self.cpu.reg_read(UC_ARM_REG_R0)

    def test_captures_the_playback_handle_and_ignores_read_position_changes(self):
        self.assertEqual(self.call("delete_track_capture"), 1)
        snapshot = struct.unpack("<6IBB11s", self.cpu.mem_read(TARGET, 37))
        self.assertEqual(snapshot, (GENERATION, 0x30002, 0x125, 19, 0x45678, 12345, 2, 1, NAME))
        self.cpu.mem_write(ACTIVE_FILE + 0x20, struct.pack("<I", 100))
        self.assertEqual(self.call("delete_track_matches"), 1)

    def test_rejects_same_named_file_after_device_or_track_change(self):
        self.assertEqual(self.call("delete_track_capture"), 1)
        for address, after, before in (
            (STORAGE + 8, 0x40000, 0x30002),
            (ACTIVE_FILE + 0x0C, 0x126, 0x125),
            (ACTIVE_FILE + 0x14, 0x45679, 0x45678),
            (ACTIVE_FILE + 0x1C, 20, 19),
        ):
            with self.subTest(address=hex(address)):
                self.cpu.mem_write(address, struct.pack("<I", after))
                self.assertEqual(self.call("delete_track_matches"), 0)
                self.cpu.mem_write(address, struct.pack("<I", before))
        self.assertEqual(self.call("delete_track_matches"), 1)

    def test_rejects_remount_even_when_device_and_file_fields_match(self):
        self.assertEqual(self.call("delete_track_capture"), 1)
        self.assertEqual(self.call("delete_track_matches", GENERATION + 1), 0)
        self.assertEqual(self.call("delete_track_capture", 0), 0)

    def test_failed_capture_invalidates_previous_snapshot(self):
        for address, after, before in (
            (TRACK_RANGE, b"\x01\0\0\0", b"\0\0\0\0"),
            (HANDLE, b"\xff\xff\xff\xff", b"\x01\0\0\0"),
            (ACTIVE_FILE, b"\0", b"\x01"),
            (STORAGE + 8, struct.pack("<I", 0x40000), struct.pack("<I", 0x30002)),
        ):
            with self.subTest(address=hex(address)):
                self.assertEqual(self.call("delete_track_capture"), 1)
                self.cpu.mem_write(address, after)
                self.assertEqual(self.call("delete_track_matches"), 0)
                self.assertEqual(self.call("delete_track_capture"), 0)
                self.cpu.mem_write(address, before)
                self.assertEqual(self.call("delete_track_matches"), 0)

    def test_cancel_or_confirm_consumes_the_pending_target(self):
        for accepted in (0, 1):
            with self.subTest(accepted=accepted):
                self.assertEqual(self.call("delete_track_capture"), 1)
                self.assertEqual(self.call("delete_track_finish", accepted=accepted), accepted)
                self.assertEqual(self.call("delete_track_finish", accepted=1), 0)

    def test_confirm_refuses_stale_target_and_consumes_it(self):
        self.assertEqual(self.call("delete_track_capture"), 1)
        self.assertEqual(self.call("delete_track_finish", GENERATION + 1, accepted=1), 0)
        self.assertEqual(self.call("delete_track_finish", accepted=1), 0)

    def execute(self, *, accepted=1, stop_result=1, closed=True, remount=False,
                changed_entry=False, write_error=False, exfat=False):
        if exfat:
            self.cpu.mem_write(STORAGE, b"\x03")
            self.cpu.mem_write(ACTIVE_FILE + 0x22A, struct.pack("<I", 0x125) + bytes(38))
        self.assertEqual(self.call("delete_track_capture"), 1)
        self.cpu.mem_write(GENERATION_ADDRESS, struct.pack("<I", GENERATION))
        calls = []
        lock_depth = 0

        def stock_call(machine, address, size, data):
            nonlocal lock_depth
            result = 0
            r0 = machine.reg_read(UC_ARM_REG_R0)
            r1 = machine.reg_read(UC_ARM_REG_R1)
            r2 = machine.reg_read(UC_ARM_REG_R2)
            if address == 0x0306A4A6:
                self.assertEqual(lock_depth, 0)
                lock_depth = 1
            elif address == 0x0306A4FE:
                self.assertEqual(lock_depth, 1)
                lock_depth = 0
            elif address == 0x0308331C:
                calls.append("stop")
                self.assertEqual((lock_depth, r0), (0, 1))
                if closed:
                    machine.mem_write(HANDLE, b"\xff" * 4)
                    machine.mem_write(ACTIVE_FILE, b"\0")
                if remount:
                    machine.mem_write(GENERATION_ADDRESS, struct.pack("<I", GENERATION + 1))
                result = stop_result
            elif address == 0x0305B44A:
                calls.append(f"read:{r2}")
                self.assertEqual(lock_depth, 1)
                self.assertEqual(bytes(machine.mem_read(ACTIVE_FILE, 1)), b"\0")
                payload = bytearray(32)
                cluster = 0x45679 if changed_entry else 0x45678
                self.assertEqual((r1, r2), (0x125, 19))
                payload[:11] = NAME
                struct.pack_into("<H", payload, 0x14, cluster >> 16)
                struct.pack_into("<HI", payload, 0x1A, cluster & 0xFFFF, 12345)
                machine.mem_write(r0, bytes(payload))
            elif address == 0x0305FC0E:
                calls.append("check-open")
                self.assertEqual(r0, 0x125)
                self.assertEqual(bytes(machine.mem_read(r1, 11)), NAME)
                result = 8
            elif address == 0x0305F392:
                calls.append("unlink-fat")
                self.assertEqual((lock_depth, r0), (1, 0x125))
                self.assertEqual(bytes(machine.mem_read(r1, 11)), NAME)
                result = int(write_error)
            elif address == 0x0305E75C:
                calls.append("release-fat")
                self.assertEqual((lock_depth, r0), (1, 0x45678))
            elif address in (0x0305F41E, 0x0305B302):
                self.fail("the write path reached the exFAT backend")
            else:
                return
            machine.reg_write(UC_ARM_REG_R0, result)
            machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

        self.cpu.hook_add(UC_HOOK_CODE, stock_call)
        status = self.call("delete_track_execute", GENERATION_ADDRESS, accepted=accepted)
        self.assertEqual(lock_depth, 0)
        self.assertEqual(self.call("delete_track_finish", accepted=1), 0)
        return status, calls

    def test_cancel_only_consumes_the_snapshot(self):
        self.assertEqual(self.execute(accepted=0), (0, []))

    def test_stop_failure_or_remount_prevents_filesystem_operations(self):
        for options, status in (({"stop_result": 0}, 1), ({"closed": False}, 2), ({"remount": True}, 2)):
            with self.subTest(**options):
                self.setUp()
                self.assertEqual(self.execute(**options), (status, ["stop"]))

    def test_fat_delete_rechecks_entry_after_stopping_and_releases_chain_after_unlink(self):
        self.assertEqual(self.execute(), (4, ["stop", "read:19", "check-open", "unlink-fat", "release-fat"]))

    def test_fat_write_failure_leaves_cluster_chain_allocated(self):
        self.assertEqual(self.execute(write_error=True), (3, ["stop", "read:19", "check-open", "unlink-fat"]))

    def test_changed_fat_entry_is_never_unlinked(self):
        self.assertEqual(self.execute(changed_entry=True), (2, ["stop", "read:19"]))

    def test_stock_dialog_defaults_to_no_and_forwarded_answer_is_consumed(self):
        image = load(str(ROOT / "NANOV200.IMG"))
        kernel = image.modules[0]
        for kind in ("code", "data"):
            start = image.l2f(kernel[kind + "_load"])
            length = kernel[kind + "_len"]
            self.cpu.mem_write(kernel[kind + "_exec"], bytes(image.d[start:start + length]))
        self.cpu.mem_write(HANDLE, struct.pack("<I", 1))
        self.cpu.mem_write(GENERATION_ADDRESS, struct.pack("<I", GENERATION))
        key = 0
        stopped = []
        stubs = {
            0x0306A4A6, 0x0306A4FE,
            0x03068F66, 0x03068F78, 0x03069002, 0x03068F92, 0x03068F3E, 0x03065B62,
            0x030500F6,
        }

        def stock_call(machine, address, size, data):
            if address == 0x030632DA:
                result = key
            elif address == 0x0308331C:
                stopped.append(machine.reg_read(UC_ARM_REG_R0))
                result = 0
            elif address in stubs:
                result = 0
            else:
                return
            machine.reg_write(UC_ARM_REG_R0, result)
            machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

        self.cpu.hook_add(UC_HOOK_CODE, stock_call)
        self.assertEqual(self.call("delete_track_show_dialog"), 1)
        self.assertEqual(
            self.cpu.mem_read(PLAYBACK_WIDGET, 4), struct.pack("<I", CONFIRMATION_WIDGET)
        )
        self.assertEqual(
            struct.unpack("<HHH", self.cpu.mem_read(DIALOG_PARAMETERS, 6)), (0, 0x38, 0x49)
        )
        self.assertEqual(self.call("delete_track_poll_dialog", GENERATION_ADDRESS), 0xffffffff)

        key = 0x40000040  # Confirm the default No.
        self.call(0x03058A44)
        self.assertEqual(self.call("delete_track_poll_dialog", GENERATION_ADDRESS), 0)
        self.assertEqual(stopped, [])

        self.assertEqual(self.call("delete_track_show_dialog"), 1)
        key = 0x40000004  # Move selection to Yes.
        self.call(0x03058A44)
        key = 0x40000040
        self.call(0x03058A44)
        self.assertEqual(self.call("delete_track_poll_dialog", GENERATION_ADDRESS), 1)
        self.assertEqual(stopped, [1])
        self.assertEqual(self.call("delete_track_poll_dialog", GENERATION_ADDRESS), 0xffffffff)

    def test_stock_fat_io_deletes_only_the_target_in_a_virtual_volume(self):
        for width in (12, 16, 32):
            for device in (0x30002, 0x40000):
                for copies in (1, 2):
                    with self.subTest(fat_width=width, device=hex(device), copies=copies):
                        self.setUp()
                        self.assert_virtual_fat_removal(width, device, copies)

    def assert_virtual_fat_removal(self, width, device, copies):
        kernel_image = load(str(ROOT / "NANOV200.IMG"))
        module = kernel_image.modules[0]
        start = kernel_image.l2f(module["code_load"])
        self.cpu.mem_write(module["code_exec"], bytes(kernel_image.d[start:start + module["code_len"]]))
        self.cpu.mem_write(STORAGE + 8, struct.pack("<I", device))
        self.cpu.mem_write(GENERATION_ADDRESS, struct.pack("<I", GENERATION))

        sectors = {}

        def sector(number):
            return sectors.setdefault(number, bytearray(512))

        fat12, fat32 = width == 12, width == 32
        reserved, fat_size, root_count, sector_count = {
            12: (1, 12, 224, 4000),
            16: (1, 32, 512, 8192),
            32: (32, 1024, 0, 131072),
        }[width]
        heap = reserved + fat_size * copies + root_count // 16
        end_of_chain = (1 << min(width, 28)) - 1
        boot = sector(0)
        boot[:11] = b"\xeb\x3c\x90MSDOS5.0"
        struct.pack_into("<HBHBHHBHHHII", boot, 11, 512, 1, reserved, copies, root_count,
                         0 if fat32 else sector_count, 0xf8, 0 if fat32 else fat_size,
                         32, 64, 0, sector_count if fat32 else 0)
        if fat32:
            struct.pack_into("<IHHIHH", boot, 36, fat_size, 0, 0, 2, 1, 6)
            boot[66] = 0x29
            boot[71:90] = b"NANO LAB   FAT32   "
        else:
            boot[38] = 0x29
            boot[43:62] = f"NANO LAB   FAT{width}   ".encode()
        boot[510:512] = b"\x55\xaa"
        target_cluster, neighbor_cluster = (341, 340) if fat12 else (6, 7)

        def fat_entry(copy, cluster, value=None):
            start = reserved + copy * fat_size
            offset = cluster * width // 8
            mask = (1 << width) - 1
            shift = (cluster & 1) * 4 if fat12 else 0
            cells = [(sector(start + (offset+i)//512), (offset+i)%512) for i in range(4 if fat32 else 2)]
            original = int.from_bytes(bytes(sector_data[index] for sector_data, index in cells), "little")
            if value is not None:
                new = (original & ~(mask << shift)) | value << shift
                for (sector_data, index), byte in zip(cells, new.to_bytes(len(cells), "little")):
                    sector_data[index] = byte
            return (original >> shift) & mask

        for copy in range(copies):
            for cluster in (0, 1, 2, 4, 5, target_cluster, neighbor_cluster):
                fat_entry(copy, cluster, end_of_chain & ~7 if cluster == 0 else end_of_chain)

        def make_entry(cluster, name=NAME):
            entry = bytearray(32)
            entry[:11] = name
            entry[11] = 0x20
            struct.pack_into("<HI", entry, 26, cluster, 128)
            return entry

        directory_cluster = 0 if fat12 else 4
        directory_sector = reserved + fat_size * copies if fat12 else heap + directory_cluster - 2
        directory = sector(directory_sector)
        other = directory if fat12 else sector(heap + 5 - 2)
        neighbor_offset = 32 if fat12 else 0
        neighbor_entry = make_entry(neighbor_cluster, b"KEEP    MP3" if fat12 else NAME)
        directory[:32] = make_entry(target_cluster)
        other[neighbor_offset:neighbor_offset+32] = neighbor_entry
        sector(heap + target_cluster - 2)[:128] = b"T" * 128
        sector(heap + neighbor_cluster - 2)[:128] = b"N" * 128
        expected_writes = [directory_sector] + [
            reserved + copy * fat_size + offset
            for copy in range(copies) for offset in range(2 if fat12 else 1)
        ]
        writes = []
        lock_depth = 0

        def call_stock(machine, address, size, data):
            nonlocal lock_depth
            r0 = machine.reg_read(UC_ARM_REG_R0)
            r1 = machine.reg_read(UC_ARM_REG_R1)
            r2 = machine.reg_read(UC_ARM_REG_R2)
            r3 = machine.reg_read(UC_ARM_REG_R3)
            if address == 0x0306B7AE:
                self.assertEqual(r0, device)
                machine.mem_write(r3, b"".join(sector(r1 + index) for index in range(r2)))
            elif address == 0x0306B7F6:
                self.assertEqual((r0, lock_depth), (device, 1))
                for index in range(r2):
                    number = r1 + index
                    self.assertIn(number, expected_writes)
                    writes.append(number)
                    sector(number)[:] = machine.mem_read(r3 + index * 512, 512)
            elif address == 0x0306A4A6:
                lock_depth += 1
            elif address == 0x0306A4FE:
                lock_depth -= 1
            elif address == 0x0308331C:
                self.assertEqual((lock_depth, r0), (0, 1))
                # The stock stop is exercised separately with all of its file handles.
                machine.mem_write(HANDLE, b"\xff" * 4)
                machine.mem_write(ACTIVE_FILE, b"\0")
                machine.reg_write(UC_ARM_REG_R0, 1)
                machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))
                return
            else:
                return
            machine.reg_write(UC_ARM_REG_R0, 0)
            machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

        self.cpu.hook_add(UC_HOOK_CODE, call_stock)
        self.assertEqual(self.call(0x0305ABB4), 0)
        self.assertEqual(self.cpu.mem_read(STORAGE, 1), bytes([{12: 0, 16: 1, 32: 2}[width]]))
        self.cpu.mem_write(ACTIVE_FILE + 0x0C, struct.pack("<IIIII", directory_cluster, 128, target_cluster, 0, 0))
        self.assertEqual(self.call("delete_track_capture", opened_device=device), 1)
        self.assertEqual(self.call("delete_track_execute", GENERATION_ADDRESS, device, accepted=1), 4)
        self.assertEqual(lock_depth, 0)
        self.assertEqual(writes, expected_writes)
        self.assertEqual(directory[0], 0xe5)
        self.assertEqual(other[neighbor_offset:neighbor_offset+32], neighbor_entry)
        for copy in range(copies):
            self.assertEqual(fat_entry(copy, target_cluster), 0)
            self.assertEqual(fat_entry(copy, neighbor_cluster), end_of_chain)
        self.assertEqual(sector(heap + neighbor_cluster - 2)[:128], b"N" * 128)

    def assert_virtual_exfat_removal(self, device, fragmented=False, stock_only=False,
                                     fail_bitmap_write=False):
        image = load(str(ROOT / "NANOV200.IMG"))
        kernel = image.modules[0]
        offset = image.l2f(kernel["code_load"])
        self.cpu.mem_write(kernel["code_exec"], bytes(image.d[offset:offset + kernel["code_len"]]))
        self.cpu.mem_write(STORAGE + 8, struct.pack("<I", device))
        self.cpu.mem_write(GENERATION_ADDRESS, struct.pack("<I", GENERATION))
        sectors = {}
        partition = 2048 if device == 0x40000 else 0
        shift = 8 if device == 0x40000 and not fragmented else 0
        data_clusters = (128_000_000_000 >> 17) if shift else (8192 if fragmented else 4040)
        fat_length = (data_clusters + 2 + 127) // 128
        heap = 24 + fat_length
        volume_length = heap + (data_clusters << shift)
        far_cluster = 4100

        def absolute_sector(number):
            return sectors.setdefault(number, bytearray(512))

        def sector(number):
            return absolute_sector(partition + number)

        def cluster_sector(cluster):
            return heap + ((cluster - 2) << shift)

        if partition:
            mbr = absolute_sector(0)
            mbr[450] = 7
            struct.pack_into("<II", mbr, 454, partition, volume_length)
            mbr[510:512] = b"\x55\xaa"
        boot = sector(0)
        boot[:11] = b"\xeb\x76\x90EXFAT   "
        struct.pack_into("<QQIIIIIIHHBBBBB", boot, 0x40, partition, volume_length,
                         24, fat_length, heap, data_clusters, 2, 0x12345678, 0x100, 0, 9, shift, 1, 0x80, 0)
        boot[510:512] = b"\x55\xaa"
        root = sector(heap)
        root[0] = 0x81
        struct.pack_into("<IQ", root, 0x14, 3, (data_clusters + 7) // 8)
        bitmap = sector(cluster_sector(3))
        bitmap[0] = 0x3f
        bitmap_tail = sector(cluster_sector(8))

        def set_fat(cluster, next_cluster):
            struct.pack_into("<I", sector(24 + cluster // 128), (cluster % 128) * 4, next_cluster)

        for cluster in range(8):
            set_fat(cluster, 0xfffffff8 if cluster == 0 else 0xffffffff)
        if fragmented:
            set_fat(3, 8)
            set_fat(8, 0xffffffff)
            set_fat(6, far_cluster)
            set_fat(far_cluster, 0xffffffff)
            bitmap[0] |= 0x40
            bitmap_tail[0] = 7
        directory = sector(cluster_sector(4))
        directory[:2] = b"\x85\x02"
        directory[4] = 0x20
        directory[32:36] = bytes((0xc0, 1 if fragmented else 3, 0, 8))
        size = 768 if fragmented else 128
        struct.pack_into("<Q", directory, 40, size)
        struct.pack_into("<IQ", directory, 52, 6, size)
        directory[64] = 0xc1
        directory[66:82] = "SAME.MP3".encode("utf-16le")
        checksum = 0
        for index, value in enumerate(directory[:96]):
            if index not in (2, 3):
                checksum = (((checksum >> 1) | (checksum << 15)) + value) & 0xffff
        struct.pack_into("<H", directory, 2, checksum)
        writes = []
        initial_bitmap = bitmap[0]

        def stock_sector_io(machine, address, size, data):
            if address in (0x0306A4A6, 0x0306A4FE, 0x03065898, 0x0308331C):
                result = 0
                if address == 0x0308331C:
                    machine.mem_write(HANDLE, b"\xff" * 4)
                    machine.mem_write(ACTIVE_FILE, b"\0")
                    result = 1
                machine.reg_write(UC_ARM_REG_R0, result)
                machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))
                return
            if address not in (0x0306B7AE, 0x0306B7F6):
                return
            actual_device = machine.reg_read(UC_ARM_REG_R0)
            first = machine.reg_read(UC_ARM_REG_R1)
            count = machine.reg_read(UC_ARM_REG_R2)
            buffer = machine.reg_read(UC_ARM_REG_R3)
            self.assertEqual(actual_device, device)
            if address == 0x0306B7F6 and fail_bitmap_write and first == partition + cluster_sector(3):
                machine.reg_write(UC_ARM_REG_R0, 1)
                machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))
                return
            for i in range(count):
                if address == 0x0306B7AE:
                    machine.mem_write(buffer + i * 512, bytes(absolute_sector(first + i)))
                else:
                    writes.append(first + i)
                    absolute_sector(first + i)[:] = machine.mem_read(buffer + i * 512, 512)
            machine.reg_write(UC_ARM_REG_R0, 0)
            machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

        self.cpu.hook_add(UC_HOOK_CODE, stock_sector_io)
        self.assertEqual(self.call(0x0305ABB4), 0)
        self.assertEqual(self.cpu.mem_read(STORAGE, 1), b"\x03")
        if stock_only:
            self.cpu.mem_write(TARGET, struct.pack("<I", 4) + bytes(38))
            self.assertEqual(self.call(0x0305F41E, 0), 0)
            self.assertEqual(writes, [partition + cluster_sector(4)])
            self.assertEqual(bitmap[0], initial_bitmap)
        else:
            self.cpu.mem_write(ACTIVE_FILE + 0x0C, struct.pack("<IIIII", 4, size, 6, 0, 0))
            self.cpu.mem_write(ACTIVE_FILE + 0x22A, struct.pack("<I", 4) + bytes(38))
            self.assertEqual(self.call("delete_track_capture", opened_device=device), 1)
            result = self.call("delete_track_execute", GENERATION_ADDRESS, device, accepted=1)
            expected = [partition, partition + cluster_sector(4)]
            if fail_bitmap_write:
                self.assertEqual(result, 3)
                self.assertEqual(writes, expected)
                self.assertEqual(boot[0x6a], 2)
                self.assertEqual(bitmap[0], initial_bitmap)
                self.assertEqual(directory[0], 5)
                return
            self.assertEqual(result, 4)
            expected.append(partition + cluster_sector(3))
            if fragmented:
                expected.append(partition + cluster_sector(8))
                self.assertEqual(bitmap_tail[0], 3)
            self.assertEqual(writes, [*expected, partition])
            self.assertEqual(boot[0x6a], 0)
            self.assertEqual(boot[0x70], 0xff)
            self.assertEqual(bitmap[0], initial_bitmap & ~0x10)
        self.assertEqual((directory[0], directory[32], directory[64]), (5, 0x40, 0x41))

    def test_stock_exfat_unlink_leaves_the_allocation_bitmap_unchanged(self):
        self.assert_virtual_exfat_removal(0x30002, stock_only=True)

    def test_exfat_frees_contiguous_and_fragmented_files_on_both_devices(self):
        for device in (0x30002, 0x40000):
            for fragmented in (False, True):
                with self.subTest(device=hex(device), fragmented=fragmented):
                    self.setUp()
                    self.assert_virtual_exfat_removal(device, fragmented)

    def test_exfat_bitmap_write_failure_leaves_the_volume_dirty(self):
        self.assert_virtual_exfat_removal(0x40000, fail_bitmap_write=True)


if __name__ == "__main__":
    unittest.main()
