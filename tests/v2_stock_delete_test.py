import struct
import sys
import unittest
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_SP

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.rknano_fw import load

FILE_BROWSER_MODULE = 31
BROWSER_STATE = 0x03004E54
STORAGE_MODE = 0x03077D00
FILE_ENTRY = 0x03020000
DIRECTORY_STATE = 0x03024000
RETURN_ADDRESS = 0x03010000
DELETE_FILE = 0x0307BF56
CONFIRMATION_GATE = 0x0307C4B0
AFTER_CONFIRMATION = 0x0307C4EE


class StockDeleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = load(str(ROOT / "NANOV200.IMG"))

    def setUp(self):
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.cpu.mem_map(0x03000000, 0x100000)
        module = self.image.modules[FILE_BROWSER_MODULE]
        for kind in ("code", "data"):
            offset = self.image.l2f(module[kind + "_load"])
            length = module[kind + "_len"]
            self.cpu.mem_write(module[kind + "_exec"], bytes(self.image.d[offset:offset + length]))
        self.cpu.mem_write(BROWSER_STATE, struct.pack("<I", FILE_ENTRY))
        self.cpu.mem_write(BROWSER_STATE + 0x1F4, struct.pack("<I", DIRECTORY_STATE))
        self.cpu.mem_write(FILE_ENTRY + 8, struct.pack("<II", 0x11223344, 0x55667788))
        self.cpu.mem_write(FILE_ENTRY + 0x4E, b"\x01SAME    MP3")
        self.cpu.reg_write(UC_ARM_REG_SP, 0x03040000)
        self.cpu.reg_write(UC_ARM_REG_LR, RETURN_ADDRESS | 1)

    def return_from_stub(self, result=0):
        self.cpu.reg_write(UC_ARM_REG_R0, result)
        self.cpu.reg_write(UC_ARM_REG_PC, self.cpu.reg_read(UC_ARM_REG_LR))

    def run_until(self, start, stop):
        self.cpu.reg_write(UC_ARM_REG_LR, RETURN_ADDRESS | 1)
        self.cpu.emu_start(start | 1, stop, count=300)
        self.assertEqual(self.cpu.reg_read(UC_ARM_REG_PC), stop)

    def test_without_confirmation_browser_skips_delete(self):
        def check_event(machine, address, size, data):
            self.assertEqual(machine.reg_read(UC_ARM_REG_R0), 0x156)
            self.return_from_stub()

        self.cpu.hook_add(UC_HOOK_CODE, check_event, begin=0x0306B0F4, end=0x0306B0F4)
        self.run_until(CONFIRMATION_GATE, AFTER_CONFIRMATION)

    def test_stock_delete_passes_the_selected_entry_to_each_storage_backend(self):
        cases = (
            (2, 0x0307A5F4, 0x11223344, FILE_ENTRY + 0x4F),
            (3, 0x0307A656, FILE_ENTRY + 0x24, 0x55667788),
        )
        calls = []

        def storage_call(machine, address, size, data):
            if address in (0x0307A5F4, 0x0307A656):
                calls.append((address, machine.reg_read(UC_ARM_REG_R0), machine.reg_read(UC_ARM_REG_R1)))
                self.return_from_stub()
            elif address in (0x0306B754, 0x0306AF7C, 0x0305ADD6, 0x0307BB16, 0x0307B95A, 0x0306AFC8):
                self.return_from_stub()

        self.cpu.hook_add(UC_HOOK_CODE, storage_call)
        for mode, backend, first, second in cases:
            with self.subTest(mode=mode):
                calls.clear()
                self.cpu.mem_write(STORAGE_MODE, bytes([mode]))
                self.run_until(DELETE_FILE, RETURN_ADDRESS)
                self.assertEqual(calls, [(backend, first, second)])


if __name__ == "__main__":
    unittest.main()
