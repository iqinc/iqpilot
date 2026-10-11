#!/usr/bin/env python3
import ctypes
import unittest

from panda.tests.libpanda import libpanda_py

lpp = libpanda_py.libpanda

IGNITION_CAN = ctypes.c_bool.in_dll(lpp, "ignition_can")
IGNITION_CAN_CNT = ctypes.c_uint32.in_dll(lpp, "ignition_can_cnt")

TESLA_LEGACY_ADDR = 0x348


def gtw_status(counter: int, drive_rail_req: bool, length: int = 8) -> bytes:
  dat = bytearray(length)
  dat[0] = 1 if drive_rail_req else 0
  dat[6] = counter & 0xF
  return bytes(dat)


class TestTeslaLegacyIgnition(unittest.TestCase):
  def setUp(self):
    IGNITION_CAN.value = False
    IGNITION_CAN_CNT.value = 7
    self._prime(0)
    IGNITION_CAN.value = False
    IGNITION_CAN_CNT.value = 7

  def _rx(self, bus: int, counter: int, drive_rail_req: bool, length: int = 8):
    pkt = libpanda_py.make_CANPacket(TESLA_LEGACY_ADDR, bus, gtw_status(counter, drive_rail_req, length))
    lpp.ignition_can_hook(pkt)

  def _prime(self, counter: int, bus: int = 0):
    self._rx(bus, counter, False)

  def test_ignition_follows_drive_rail_on_consecutive_counters(self):
    for bus in (0, 1):
      self._prime(0, bus)
      self._rx(bus, 1, True)
      self.assertTrue(IGNITION_CAN.value)
      self.assertEqual(IGNITION_CAN_CNT.value, 0)

      self._rx(bus, 2, False)
      self.assertFalse(IGNITION_CAN.value)

  def test_counter_wraps(self):
    self._prime(15)
    self._rx(0, 0, True)
    self.assertTrue(IGNITION_CAN.value)

  def test_non_consecutive_counter_is_ignored(self):
    self._prime(3)
    self._rx(0, 9, True)
    self.assertFalse(IGNITION_CAN.value)
    self.assertEqual(IGNITION_CAN_CNT.value, 7)

  def test_other_buses_are_ignored(self):
    self._prime(0)
    self._rx(2, 1, True)
    self.assertFalse(IGNITION_CAN.value)

  def test_wrong_length_is_ignored(self):
    self._prime(0)
    self._rx(0, 1, True, length=7)
    self.assertFalse(IGNITION_CAN.value)

  def test_cnt_resets_on_valid_frame(self):
    self._prime(4)
    IGNITION_CAN_CNT.value = 2
    self._rx(0, 5, False)
    self.assertEqual(IGNITION_CAN_CNT.value, 0)


if __name__ == "__main__":
  unittest.main()
