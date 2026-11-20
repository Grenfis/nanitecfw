import struct
import sys
import unittest
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_SP

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.rknano_fw import load

STOP_PLAYBACK = 0x0308331C
CLOSE_DECODER_FILES = 0x03085FE0
CLOSE_FILE = 0x0305C340
RETURN = 0x03010000
PLAYBACK_HANDLE = 0x030045AC
FILES = 0x03078844
FILE_SIZE = 0x27E
DECODER = 0x03039D94


class DecoderStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image = load(str(ROOT / "NANOV200.IMG"))
        cls.code = []
        for index in (0, 33, 34):
            module = image.modules[index]
            offset = image.l2f(module["code_load"])
            cls.code.append(
                (module["code_exec"], bytes(image.d[offset:offset + module["code_len"]]))
            )

    def test_stop_closes_primary_and_format_specific_playback_handles(self):
        for decoder, extra_handles in ((0, ()), (2, (0x030045B4, 0x030045B8)),
                                       (7, (0x030045B4, 0x030045B8)),
                                       (8, (0x030045BC,)), (9, (0x030045C0,))):
            with self.subTest(decoder=decoder):
                cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
                cpu.mem_map(0x03000000, 0x100000)
                for address, code in self.code:
                    cpu.mem_write(address, code)
                cpu.reg_write(UC_ARM_REG_SP, 0x03040000)
                cpu.reg_write(UC_ARM_REG_LR, RETURN | 1)
                cpu.reg_write(UC_ARM_REG_R0, 1)
                cpu.mem_write(DECODER, struct.pack("<I", decoder))
                cpu.mem_write(0x030045A4, struct.pack("<I", CLOSE_FILE | 1))
                cpu.mem_write(0x03039614, struct.pack("<I", 0x03011000))
                addresses = (PLAYBACK_HANDLE, *extra_handles)
                for handle, address in enumerate(addresses):
                    cpu.mem_write(address, struct.pack("<I", handle))
                    cpu.mem_write(FILES + handle * FILE_SIZE, b"\x01")
                cpu.mem_write(FILES + 7 * FILE_SIZE, b"\x01")

                calls = []
                stubs = {
                    0x0306B138, 0x03065750, 0x03059AD4, 0x030568E0, 0x03054D4C,
                    0x0306B754, 0x0306B11A, 0x03084818, 0x03082C5C, 0x0305FC52,
                    0x03059614, 0x030596BE, 0x0306A3D4, 0x03086058,
                }

                def trace(machine, address, size, data):
                    if address in (CLOSE_DECODER_FILES, CLOSE_FILE):
                        calls.append((address, machine.reg_read(UC_ARM_REG_R0)))
                    elif address in stubs:
                        machine.reg_write(UC_ARM_REG_R0, 0)
                        machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

                cpu.hook_add(UC_HOOK_CODE, trace)
                cpu.emu_start(STOP_PLAYBACK | 1, RETURN, count=1500)
                self.assertEqual(cpu.reg_read(UC_ARM_REG_PC), RETURN)
                self.assertEqual(cpu.reg_read(UC_ARM_REG_R0), 1)
                self.assertEqual(calls[0][0], CLOSE_DECODER_FILES)
                self.assertEqual(calls[1:], [(CLOSE_FILE, n) for n in range(len(addresses))])
                for handle, address in enumerate(addresses):
                    self.assertEqual(bytes(cpu.mem_read(address, 4)), b"\xff" * 4)
                    self.assertEqual(bytes(cpu.mem_read(FILES + handle * FILE_SIZE, 1)), b"\x00")
                self.assertEqual(bytes(cpu.mem_read(FILES + 7 * FILE_SIZE, 1)), b"\x01")


if __name__ == "__main__":
    unittest.main()
