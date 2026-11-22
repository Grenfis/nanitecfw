import struct
import unittest
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_THUMB
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R5,
    UC_ARM_REG_R8, UC_ARM_REG_R10, UC_ARM_REG_SP,
)

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.rknano_fw import load

ROOT = Path(__file__).resolve().parents[1]
IMAGE_PATH = ROOT / "out/nanitecfw-6.0.58-20261104/NANOV200.IMG"
HOOK_ADDRESS = 0x0307B180
GET_KEY_ADDRESS = 0x030632DA
RETURN_ADDRESS = 0x03010000
TITLE_ADDRESS = 0x03004B12
BROWSER_TITLE = 0x0300E704
LIST_ADDRESS = 0x0300E968
TICK_ADDRESS = 0xE000E018
PLAYBACK_TICKS = 0x0300C02C
QUEUE_ADDRESS = 0x0300C744
INIT_HISTORY = 0x030816C2
HISTORY_ADDRESS = 0x03035400
WALK_HISTORY = 0x03081740
SELECT_TRACK = 0x0306050C
TRACK_SELECTED = 0x030608A0


class ShuffleHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = load(str(IMAGE_PATH))

    def machine(self, key=0):
        cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        cpu.mem_map(0x03000000, 0x100000)
        cpu.mem_map(0xE000E000, 0x1000)
        for module in (0, 23, 33):
            region = self.image.modules[module]
            offset = self.image.l2f(region["code_load"])
            end = offset + region["code_len"]
            cpu.mem_write(region["code_exec"], bytes(self.image.d[offset:end]))

        def firmware_call(machine, address, size, user_data):
            if address == GET_KEY_ADDRESS:
                machine.reg_write(UC_ARM_REG_R0, key)
            elif address in (0x03065898, 0x0306AF7C, 0x0306AFC8):
                machine.reg_write(UC_ARM_REG_R0, 0)
            else:
                return
            machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

        cpu.hook_add(UC_HOOK_CODE, firmware_call)
        return cpu

    def call(self, cpu, address, r0=0, r1=0, stop=RETURN_ADDRESS):
        cpu.reg_write(UC_ARM_REG_SP, 0x03040000)
        cpu.reg_write(UC_ARM_REG_LR, RETURN_ADDRESS | 1)
        cpu.reg_write(UC_ARM_REG_R0, r0)
        cpu.reg_write(UC_ARM_REG_R1, r1)
        cpu.emu_start(address | 1, stop, count=200000)
        self.assertEqual(cpu.reg_read(UC_ARM_REG_PC), stop)

    def run_hook(self, title, key, count=10, index=3, ticks=12345):
        cpu = self.machine(key)
        cpu.mem_write(TITLE_ADDRESS, struct.pack("<H", title))
        cpu.mem_write(BROWSER_TITLE, struct.pack("<H", title))
        cpu.mem_write(LIST_ADDRESS, struct.pack("<H", count))
        cpu.mem_write(LIST_ADDRESS + 0x0c, struct.pack("<H", index))
        cpu.mem_write(LIST_ADDRESS + 0x16, struct.pack("<H", index))
        cpu.mem_write(TICK_ADDRESS, struct.pack("<I", ticks))
        self.call(cpu, HOOK_ADDRESS)
        title_after = struct.unpack("<H", cpu.mem_read(TITLE_ADDRESS, 2))[0]
        self.assertEqual(cpu.mem_read(BROWSER_TITLE, 2), struct.pack("<H", title_after))
        index_after = struct.unpack("<H", cpu.mem_read(LIST_ADDRESS + 0x16, 2))[0]
        self.assertEqual(cpu.mem_read(LIST_ADDRESS + 0x0c, 2), struct.pack("<H", index_after))
        return cpu.reg_read(UC_ARM_REG_R0), title_after, index_after

    def test_shuffle_all_selects_random_song(self):
        key, title, index = self.run_hook(0x82, 0)
        self.assertEqual(key, 0x40000040)
        self.assertEqual(title, 0x5A)
        self.assertLess(index, 10)
        starts = {self.run_hook(0x82, 0, ticks=tick)[2] for tick in range(100, 108)}
        self.assertGreater(len(starts), 1)

    def test_m3u_title_and_key_pass_through(self):
        original_key = 0x40008000
        key, title, index = self.run_hook(0x5E, original_key)
        self.assertEqual(key, original_key)
        self.assertEqual(title, 0x5E)
        self.assertEqual(index, 3)

    def test_other_browser_title_passes_through(self):
        original_key = 0x40004000
        key, title, index = self.run_hook(0x5A, original_key)
        self.assertEqual(key, original_key)
        self.assertEqual(title, 0x5A)
        self.assertEqual(index, 3)

    def playback(self, count, first):
        cpu = self.machine()
        cpu.mem_write(QUEUE_ADDRESS, struct.pack("<HH", count, first))
        cpu.mem_write(QUEUE_ADDRESS + 12, b"\x01\x00")
        cpu.mem_write(TICK_ADDRESS, struct.pack("<I", 12345))
        self.call(cpu, INIT_HISTORY, count, first - 1)
        return cpu

    def step(self, cpu, direction=1):
        self.call(cpu, SELECT_TRACK, QUEUE_ADDRESS, direction & 0xffffffff, TRACK_SELECTED)
        return struct.unpack("<H", cpu.mem_read(QUEUE_ADDRESS + 2, 2))[0]

    def test_pass_draws_from_both_sides_without_repeats_or_timing_dependence(self):
        def play(clock_offset):
            cpu = self.playback(20, 11)
            order = [11, self.step(cpu)]
            for step in range(18):
                cpu.mem_write(PLAYBACK_TICKS, struct.pack("<I", step + clock_offset))
                cpu.mem_write(TICK_ADDRESS, struct.pack("<I", 100 * step + clock_offset))
                order.append(self.step(cpu))
            return order, self.step(cpu)

        order, restart = play(0)
        self.assertEqual(sorted(order), list(range(1, 21)))
        self.assertNotIn(restart, (order[0], order[-1]))
        self.assertEqual(play(2345), (order, restart))

    def test_previous_then_next_follows_history(self):
        cpu = self.playback(12, 6)

        def walk(direction):
            self.call(cpu, WALK_HISTORY, direction & 0xffffffff)
            return cpu.reg_read(UC_ARM_REG_R0)

        second, third = walk(1), walk(1)
        self.assertEqual(walk(-1), second)
        self.assertEqual(walk(1), third)

    def test_opening_or_toggling_shuffle_seeds_history_with_the_current_track(self):
        for entry, end, queue_register in (
            (0x03083BD2, 0x03083C24, UC_ARM_REG_R8),
            (0x03084394, 0x030843E8, UC_ARM_REG_R10),
        ):
            with self.subTest(entry=hex(entry)):
                cpu = self.machine()
                cpu.mem_write(QUEUE_ADDRESS, struct.pack("<HH", 12, 6))
                cpu.reg_write(queue_register, QUEUE_ADDRESS)
                cpu.reg_write(UC_ARM_REG_R5, 0x030397A4)
                self.call(cpu, entry, 12, stop=end)
                self.assertEqual(cpu.mem_read(HISTORY_ADDRESS, 4), b"\x01\x00\x05\x00")

    def test_full_history_buffer_is_used(self):
        cpu = self.playback(8192, 4096)
        self.assertNotEqual(self.step(cpu), 4096)
        self.assertEqual(cpu.mem_read(HISTORY_ADDRESS, 2), b"\x02\x00")

    def test_large_queue_draws_stay_in_range_and_avoid_immediate_repeats(self):
        cpu = self.playback(8200, 8195)
        previous = 8195
        for _ in range(20):
            current = self.step(cpu)
            self.assertTrue(1 <= current <= 8200)
            self.assertNotEqual(current, previous)
            previous = current


if __name__ == "__main__":
    unittest.main()
