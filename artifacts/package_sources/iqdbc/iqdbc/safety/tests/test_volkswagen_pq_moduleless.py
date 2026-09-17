#!/usr/bin/env python3
import unittest

from iqdbc.car.volkswagen.values import VolkswagenSafetyFlags
from iqdbc.car.structs import CarParams
from iqdbc.safety.tests.libsafety import libsafety_py
from iqdbc.safety.tests.common import CANPackerSafety

MSG_AWV = 0x366
MSG_ACC_SYSTEM = 0x368
MSG_MOTOR_2 = 0x288
MSG_GRA_NEU = 0x38A
MSG_ACC_GRA_ANZEIGE = 0x56A

PT_BUS = 1
ECAN_BUS = 0
CMD_TIMEOUT = 25
ECHO_DEPTH = 4

ACC_SYSTEM_IDLE = [0x20, 0x00, 0xFE, 0x07, 0xFE, 0xFE, 0x00]
AWV_IDLE = [0x00, 0x00, 0x74, 0x00, 0x00, 0x80, 0xD0]


def xor_checksum(payload):
  out = 0
  for b in payload:
    out ^= b
  return out


class KeeperTestBase(unittest.TestCase):
  """Drives the panda-side ACC module synthesizer directly. The safety layer only decides
  whether it is armed; everything asserted here is what actually reaches the car's CAN bus."""

  TX_MSGS = None
  LONGITUDINAL = True
  MODULELESS = True
  LOWLINE = False

  def setUp(self):
    self.packer = CANPackerSafety("vw_pq")
    self.safety = libsafety_py.libsafety
    param = 0
    if self.LONGITUDINAL:
      param |= VolkswagenSafetyFlags.LONG_CONTROL
    if self.MODULELESS:
      param |= VolkswagenSafetyFlags.PQ_MODULELESS
    if self.LOWLINE:
      param |= VolkswagenSafetyFlags.PQ_LOWLINE
    self.safety.set_safety_hooks(CarParams.SafetyModel.volkswagenPq, param)
    self.safety.init_tests()
    self.safety.pq_moduleless_test_reset()

  def tearDown(self):
    self.safety.set_safety_hooks(CarParams.SafetyModel.silent, 0)

  def _motor_2(self, tsk=True, bus=PT_BUS):
    return self.packer.make_can_msg_safety("Motor_2", bus, {"MO2_Status_TSK": 1 if tsk else 0})

  def _gra_neu(self, coded=True, bus=PT_BUS):
    return self.packer.make_can_msg_safety("GRA_Neu", bus, {"GRA_Kodierinfo": 1 if coded else 0})

  def _foreign_acc_system(self, bus=PT_BUS):
    return self.packer.make_can_msg_safety("ACC_System", bus, {"ACS_Sollbeschl": 0.0})

  def _sent(self):
    out = []
    for i in range(self.safety.get_can_sent_count()):
      out.append((self.safety.get_can_sent_addr(i),
                  self.safety.get_can_sent_bus(i),
                  [self.safety.get_can_sent_byte(i, b) for b in range(8)]))
    return out

  def _ignition_on(self):
    self.safety.pq_moduleless_ignition(True)
    self.safety.reset_can_sent()

  def _coding_seen(self):
    self.safety.pq_moduleless_rx(self._gra_neu())
    self.safety.reset_can_sent()

  def _tick(self, n=1, tsk=True):
    for _ in range(n):
      self.safety.pq_moduleless_rx(self._motor_2(tsk=tsk))

  def _run(self, ticks):
    self._ignition_on()
    self._coding_seen()
    self._tick(ticks)
    return self._sent()


class TestKeeperSilentWhenNotArmed(KeeperTestBase):
  """The single most important property: a panda that was never told this car is moduleless
  must never put a synthesized ACC module on anyone's bus."""

  MODULELESS = False

  def test_not_armed(self):
    self.assertFalse(self.safety.get_pq_moduleless_armed())

  def test_never_emits(self):
    self.assertEqual([], self._run(200))
    self.assertFalse(self.safety.get_pq_moduleless_emitting())

  def test_never_emits_without_ignition(self):
    self._coding_seen()
    self._tick(200)
    self.assertEqual([], self._sent())


class TestKeeperSilentOnOtherSafetyModes(KeeperTestBase):
  def test_every_non_pq_mode_disarms(self):
    modes = [CarParams.SafetyModel.silent, CarParams.SafetyModel.toyota, CarParams.SafetyModel.hondaNidec,
             CarParams.SafetyModel.hondaBosch, CarParams.SafetyModel.hyundai, CarParams.SafetyModel.gm,
             CarParams.SafetyModel.volkswagen, CarParams.SafetyModel.volkswagenMeb,
             CarParams.SafetyModel.volkswagenMlb, CarParams.SafetyModel.subaru, CarParams.SafetyModel.ford,
             CarParams.SafetyModel.tesla, CarParams.SafetyModel.rivian, CarParams.SafetyModel.nissan,
             CarParams.SafetyModel.mazda, CarParams.SafetyModel.chrysler, CarParams.SafetyModel.body,
             CarParams.SafetyModel.elm327, CarParams.SafetyModel.noOutput, CarParams.SafetyModel.allOutput]
    for mode in modes:
      with self.subTest(mode=int(mode)):
        self.safety.set_safety_hooks(CarParams.SafetyModel.volkswagenPq,
                                     VolkswagenSafetyFlags.LONG_CONTROL | VolkswagenSafetyFlags.PQ_MODULELESS)
        self.assertTrue(self.safety.get_pq_moduleless_armed())

        self.safety.set_safety_hooks(mode, 0)
        self.assertFalse(self.safety.get_pq_moduleless_armed(),
                         "switching safety mode must disarm the synthesizer")
        self.safety.pq_moduleless_ignition(False)
        self.safety.pq_moduleless_ignition(True)
        self.safety.reset_can_sent()
        self._coding_seen()
        self._tick(100)
        self.assertEqual([], self._sent())

  def test_pq_without_moduleless_flag_disarms(self):
    self.safety.set_safety_hooks(CarParams.SafetyModel.volkswagenPq,
                                 VolkswagenSafetyFlags.LONG_CONTROL | VolkswagenSafetyFlags.PQ_MODULELESS)
    self.assertTrue(self.safety.get_pq_moduleless_armed())
    self.safety.set_safety_hooks(CarParams.SafetyModel.volkswagenPq, VolkswagenSafetyFlags.LONG_CONTROL)
    self.assertFalse(self.safety.get_pq_moduleless_armed())

  def test_moduleless_without_long_disarms(self):
    self.safety.set_safety_hooks(CarParams.SafetyModel.volkswagenPq, VolkswagenSafetyFlags.PQ_MODULELESS)
    self.assertFalse(self.safety.get_pq_moduleless_armed())


class TestKeeperArmed(KeeperTestBase):
  def test_armed(self):
    self.assertTrue(self.safety.get_pq_moduleless_armed())

  def test_tx_bus_is_ecan_on_highline(self):
    self.assertEqual(ECAN_BUS, self.safety.get_pq_moduleless_tx_bus())

  def test_silent_until_ignition(self):
    self._coding_seen()
    self._tick(100)
    self.assertEqual([], self._sent())
    self.assertFalse(self.safety.get_pq_moduleless_emitting())

  def test_silent_without_tsk(self):
    self._ignition_on()
    self.safety.pq_moduleless_rx(self._gra_neu())
    self._tick(100, tsk=False)
    self.assertEqual([], self._sent())

  def test_silent_without_gra_coding(self):
    self._ignition_on()
    self.safety.pq_moduleless_rx(self._gra_neu(coded=False))
    self._tick(100)
    self.assertEqual([], self._sent())

  def test_emits_on_first_motor_2_after_coding(self):
    self._ignition_on()
    self._coding_seen()
    self._tick(1)
    addrs = [a for a, _, _ in self._sent()]
    self.assertEqual([MSG_ACC_SYSTEM, MSG_ACC_GRA_ANZEIGE, MSG_AWV], addrs,
                     "all three messages must be up on the very first powertrain frame")

  def test_only_powertrain_motor_2_drives_emission(self):
    self._ignition_on()
    self._coding_seen()
    for bus in (0, 2):
      self.safety.pq_moduleless_rx(self._motor_2(bus=bus))
    self.assertEqual([], self._sent())

  def test_emits_on_configured_bus(self):
    for addr, bus, _ in self._run(100):
      self.assertEqual(ECAN_BUS, bus, f"0x{addr:03X} went out on bus {bus}")

  def test_message_rates(self):
    sent = self._run(100)
    counts = {a: 0 for a in (MSG_ACC_SYSTEM, MSG_ACC_GRA_ANZEIGE, MSG_AWV)}
    for addr, _, _ in sent:
      counts[addr] += 1
    self.assertEqual(100, counts[MSG_ACC_SYSTEM], "ACC_System is 50Hz against 50Hz Motor_2")
    self.assertEqual(50, counts[MSG_ACC_GRA_ANZEIGE], "ACC_GRA_Anzeige is 25Hz")
    self.assertEqual(50, counts[MSG_AWV], "AWV is 25Hz")

  def test_no_other_addresses(self):
    for addr, _, _ in self._run(100):
      self.assertIn(addr, (MSG_ACC_SYSTEM, MSG_ACC_GRA_ANZEIGE, MSG_AWV))

  def test_checksum_is_xor_of_remaining_bytes(self):
    for addr, _, data in self._run(100):
      self.assertEqual(xor_checksum(data[1:]), data[0], f"bad checksum on 0x{addr:03X}")

  def test_counters_increment_by_one_per_message(self):
    sent = self._run(200)
    for addr, extract in ((MSG_ACC_SYSTEM, lambda d: d[1] & 0xF),
                          (MSG_AWV, lambda d: d[1] & 0xF),
                          (MSG_ACC_GRA_ANZEIGE, lambda d: d[7] >> 4)):
      seq = [extract(d) for a, _, d in sent if a == addr]
      self.assertGreater(len(seq), 16)
      steps = {(seq[i] - seq[i - 1]) % 16 for i in range(1, len(seq))}
      self.assertEqual({1}, steps, f"0x{addr:03X} counter must step by exactly 1 per frame")

  def test_counter_wraps_at_15(self):
    seq = [d[1] & 0xF for a, _, d in self._run(200) if a == MSG_ACC_SYSTEM]
    self.assertEqual(set(range(16)), set(seq))

  def test_idle_acc_system_matches_oem_radar(self):
    payloads = {tuple(d[1:]) for a, _, d in self._run(100) if a == MSG_ACC_SYSTEM}
    counters = {p[0] & 0xF for p in payloads}
    self.assertEqual(set(range(16)), counters)
    for p in payloads:
      self.assertEqual(ACC_SYSTEM_IDLE[0] >> 4, p[0] >> 4, "ACS_Sta_ADR must idle at 2, not 0")
      self.assertEqual(ACC_SYSTEM_IDLE[1:], list(p[1:]))

  def test_idle_awv_matches_oem_radar(self):
    for addr, _, d in self._run(100):
      if addr == MSG_AWV:
        self.assertEqual(AWV_IDLE[0] >> 4, d[1] >> 4)
        self.assertEqual(AWV_IDLE[1:], d[2:])

  def test_ignition_off_stops_emission(self):
    self._run(10)
    self.safety.pq_moduleless_ignition(False)
    self.safety.reset_can_sent()
    self._coding_seen()
    self._tick(100)
    self.assertEqual([], self._sent())
    self.assertFalse(self.safety.get_pq_moduleless_emitting())

  def test_ignition_cycle_restarts_from_idle_and_counter_zero(self):
    self._run(37)
    self.safety.pq_moduleless_ignition(False)
    self.safety.pq_moduleless_ignition(True)
    self.safety.reset_can_sent()
    self._coding_seen()
    self._tick(1)
    acc = [d for a, _, d in self._sent() if a == MSG_ACC_SYSTEM]
    self.assertEqual(1, len(acc))
    self.assertEqual(0, acc[0][1] & 0xF)
    self.assertEqual(ACC_SYSTEM_IDLE[1:], acc[0][2:])


class TestKeeperCommandPassthrough(KeeperTestBase):
  def _cmd(self, accel=0.0, **kwargs):
    values = {"ACS_Sollbeschl": accel}
    values.update(kwargs)
    return self.packer.make_can_msg_safety("IQ_PQ_ACC_CMD", PT_BUS, values)

  def _hud(self, sta_acc=0, **kwargs):
    values = {"ACA_StaACC": sta_acc}
    values.update(kwargs)
    return self.packer.make_can_msg_safety("IQ_PQ_ACC_HUD", PT_BUS, values)

  def test_command_contents_reach_the_oem_message(self):
    self.safety.set_controls_allowed(True)
    self._ignition_on()
    self._coding_seen()
    self.assertTrue(self.safety.safety_tx_hook(self._cmd(accel=1.0)))
    self.safety.reset_can_sent()
    self._tick(1)
    acc = [d for a, _, d in self._sent() if a == MSG_ACC_SYSTEM]
    self.assertEqual(1, len(acc))
    raw = ((acc[0][4] & 0x7) << 8) | acc[0][3]
    self.assertAlmostEqual(1.0, raw * 0.005 - 7.22, places=2)

  def test_hud_contents_reach_the_oem_message(self):
    self._ignition_on()
    self._coding_seen()
    self.assertTrue(self.safety.safety_tx_hook(self._hud(sta_acc=2)))
    self.safety.reset_can_sent()
    self._tick(1)
    hud = [d for a, _, d in self._sent() if a == MSG_ACC_GRA_ANZEIGE]
    self.assertEqual(1, len(hud))
    self.assertEqual(2, hud[0][1] & 0x7)

  def test_blocked_command_never_reaches_the_car(self):
    self.safety.set_controls_allowed(False)
    self._ignition_on()
    self._coding_seen()
    self.assertFalse(self.safety.safety_tx_hook(self._cmd(accel=1.0)))
    self.safety.reset_can_sent()
    self._tick(1)
    acc = [d for a, _, d in self._sent() if a == MSG_ACC_SYSTEM]
    self.assertEqual(ACC_SYSTEM_IDLE[1:], acc[0][2:],
                     "a rejected accel must leave the synthesized module at idle")

  def test_command_times_out_back_to_idle(self):
    self.safety.set_controls_allowed(True)
    self._ignition_on()
    self._coding_seen()
    self.assertTrue(self.safety.safety_tx_hook(self._cmd(accel=1.0)))
    self._tick(CMD_TIMEOUT + 2)
    self.safety.reset_can_sent()
    self._tick(1)
    acc = [d for a, _, d in self._sent() if a == MSG_ACC_SYSTEM]
    self.assertEqual(ACC_SYSTEM_IDLE[1:], acc[0][2:],
                     "openpilot going quiet must fall back to the OEM idle frame")

  def test_command_holds_until_timeout(self):
    self.safety.set_controls_allowed(True)
    self._ignition_on()
    self._coding_seen()
    self.assertTrue(self.safety.safety_tx_hook(self._cmd(accel=1.0)))
    self._tick(CMD_TIMEOUT - 2)
    self.safety.reset_can_sent()
    self._tick(1)
    acc = [d for a, _, d in self._sent() if a == MSG_ACC_SYSTEM]
    self.assertNotEqual(ACC_SYSTEM_IDLE[1:], acc[0][2:])

  def test_keeper_overwrites_command_counter_and_checksum(self):
    self.safety.set_controls_allowed(True)
    self._ignition_on()
    self._coding_seen()
    for _ in range(20):
      self.assertTrue(self.safety.safety_tx_hook(self._cmd(accel=0.5)))
    self.safety.reset_can_sent()
    self._tick(20)
    acc = [d for a, _, d in self._sent() if a == MSG_ACC_SYSTEM]
    seq = [d[1] & 0xF for d in acc]
    self.assertEqual({1}, {(seq[i] - seq[i - 1]) % 16 for i in range(1, len(seq))})
    for d in acc:
      self.assertEqual(xor_checksum(d[1:]), d[0])


class TestKeeperForeignModule(KeeperTestBase):
  """A real ACC module answering is the one condition that must shut the synthesizer down for
  good, so two transmitters never share ACC_System on a car that still has its radar."""

  def test_foreign_acc_system_before_emitting_latches_off(self):
    self._ignition_on()
    self.safety.pq_moduleless_rx(self._foreign_acc_system())
    self._coding_seen()
    self._tick(200)
    self.assertEqual([], self._sent())
    self.assertFalse(self.safety.get_pq_moduleless_emitting())

  def test_foreign_acc_system_while_emitting_latches_off(self):
    self._run(10)
    self.assertTrue(self.safety.get_pq_moduleless_emitting())
    self.safety.pq_moduleless_rx(self._foreign_acc_system())
    self.assertFalse(self.safety.get_pq_moduleless_emitting())
    self.safety.reset_can_sent()
    self._tick(200)
    self.assertEqual([], self._sent())

  def test_latch_is_not_cleared_by_more_traffic(self):
    self._run(10)
    self.safety.pq_moduleless_rx(self._foreign_acc_system())
    for _ in range(50):
      self._coding_seen()
      self._tick(10)
    self.assertEqual([], self._sent())

  def test_latch_clears_on_next_ignition_cycle(self):
    self._run(10)
    self.safety.pq_moduleless_rx(self._foreign_acc_system())
    self.safety.pq_moduleless_ignition(False)
    self.safety.pq_moduleless_ignition(True)
    self.safety.reset_can_sent()
    self._coding_seen()
    self._tick(10)
    self.assertGreater(len(self._sent()), 0)

  def test_foreign_acc_system_on_any_bus_latches_off(self):
    for bus in (0, 1, 2):
      with self.subTest(bus=bus):
        self.safety.pq_moduleless_ignition(False)
        self._run(10)
        self.safety.pq_moduleless_rx(self._foreign_acc_system(bus=bus))
        self.safety.reset_can_sent()
        self._tick(50)
        self.assertEqual([], self._sent())

  def test_own_echo_through_the_gateway_does_not_latch_off(self):
    self._ignition_on()
    self._coding_seen()
    for _ in range(200):
      self._tick(1)
      for addr, _, data in self._sent():
        if addr == MSG_ACC_SYSTEM:
          echo = self._foreign_acc_system(bus=PT_BUS)
          for i in range(8):
            echo.data[i] = data[i]
          self.safety.pq_moduleless_rx(echo)
      self.safety.reset_can_sent()
    self.assertTrue(self.safety.get_pq_moduleless_emitting())

  def test_delayed_own_echo_within_depth_does_not_latch_off(self):
    self._ignition_on()
    self._coding_seen()
    pending = []
    for _ in range(200):
      self._tick(1)
      for addr, _, data in self._sent():
        if addr == MSG_ACC_SYSTEM:
          pending.append(data)
      self.safety.reset_can_sent()
      if len(pending) > ECHO_DEPTH - 1:
        data = pending.pop(0)
        echo = self._foreign_acc_system(bus=PT_BUS)
        for i in range(8):
          echo.data[i] = data[i]
        self.safety.pq_moduleless_rx(echo)
    self.assertTrue(self.safety.get_pq_moduleless_emitting())

  def test_echo_older_than_depth_latches_off(self):
    self._ignition_on()
    self._coding_seen()
    self._tick(1)
    stale = [d for a, _, d in self._sent() if a == MSG_ACC_SYSTEM][0]
    self._tick(ECHO_DEPTH + 2)
    echo = self._foreign_acc_system(bus=PT_BUS)
    for i in range(8):
      echo.data[i] = stale[i]
    self.safety.pq_moduleless_rx(echo)
    self.assertFalse(self.safety.get_pq_moduleless_emitting())


class TestKeeperLowline(KeeperTestBase):
  LOWLINE = True

  def test_tx_bus_is_powertrain(self):
    self.assertEqual(PT_BUS, self.safety.get_pq_moduleless_tx_bus())

  def test_emits_on_powertrain_bus(self):
    for _, bus, _ in self._run(50):
      self.assertEqual(PT_BUS, bus)


if __name__ == "__main__":
  unittest.main()
