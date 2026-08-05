#!/usr/bin/env python3
import unittest

import numpy as np

from iqdbc.can.packer import CANPacker
from iqdbc.can.parser import CANParser
from iqdbc.car.byd import bydcan
from iqdbc.car.byd.carstate import (EPS_STATE_OFF, EPS_STATE_PREPARED, EPS_STATE_ACTUATING,
                                    EPS_STATE_LATCHED_FAULT)
from iqdbc.car.byd.fingerprints import FW_VERSIONS
from iqdbc.car.byd.interface import CarInterface
from iqdbc.car.byd.values import CAR, DBC, BydFlags, BydSafetyFlags, CarControllerParams
from iqdbc.car.fw_versions import match_fw_to_car_exact, build_fw_dict
from iqdbc.car import structs
from iqdbc.car.structs import CarParams

DBC_NAME = DBC[CAR.BYD_SEALION_7]['pt']

Ecu = CarParams.Ecu


def _unpack(dbc_name, msg_name, dat):
  """Decode one frame with the DBC, bypassing the parser's liveness tracking."""
  dbc = CANParser(dbc_name, [], 0).dbc
  msg = dbc.name_to_msg[msg_name]
  out = {}
  for sig in msg.sigs.values():
    val = 0
    if sig.is_little_endian:
      for i in range(sig.size):
        bit = sig.lsb + i
        val |= ((dat[bit // 8] >> (bit % 8)) & 1) << i
    else:
      be_bits = [j + i * 8 for i in range(64) for j in range(7, -1, -1)]
      idx = be_bits.index(sig.start_bit)
      for i in range(sig.size):
        bit = be_bits[idx + i]
        val = (val << 1) | ((dat[bit // 8] >> (bit % 8)) & 1)
    if sig.is_signed and (val & (1 << (sig.size - 1))):
      val -= (1 << sig.size)
    out[sig.name] = val * sig.factor + sig.offset
  return out


class TestBydChecksum(unittest.TestCase):
  def test_checksum_is_inverted_sum(self):
    for dat in (bytearray(8), bytearray(b'\x01' * 8), bytearray(b'\xff' * 8),
                bytearray(b'\x12\x34\x56\x78\x9a\xbc\xde\x00')):
      self.assertEqual(bydcan.byd_checksum(0, None, dat), (~sum(dat[:7])) & 0xFF)

  def test_packer_fills_checksum_and_counter(self):
    packer = CANPacker(DBC_NAME)
    seen = []
    for _ in range(18):
      _, dat, _ = packer.make_can_msg("STEERING_MODULE_ADAS", 0, {"STEER_REQ": 1})
      self.assertEqual(dat[7], (~sum(dat[:7])) & 0xFF, "checksum not filled by the DBC layer")
      seen.append(dat[6] >> 4)  # COUNTER is 55|4@0
    # rolls 0..15 and wraps, never repeating within a cycle
    self.assertEqual(seen[:16], list(range(16)))
    self.assertEqual(seen[16:], [0, 1])


class TestBydSteeringControl(unittest.TestCase):
  def setUp(self):
    self.packer = CANPacker(DBC_NAME)

  def test_steer_req_and_angle_round_trip(self):
    for angle in (-390.0, -100.5, 0.0, 12.3, 390.0):
      for lat_active in (True, False):
        _, dat, _ = bydcan.create_steering_control(self.packer, angle, lat_active)
        vals = _unpack(DBC_NAME, "STEERING_MODULE_ADAS", dat)
        self.assertAlmostEqual(vals["STEER_ANGLE"], angle, places=4)
        self.assertEqual(vals["STEER_REQ"], 1 if lat_active else 0)
        # STEER_REQ_ACTIVE_LOW is the inverse of STEER_REQ
        self.assertEqual(vals["STEER_REQ_ACTIVE_LOW"], 0 if lat_active else 1)
        self.assertEqual(vals["E2E_ALIVE_1"], 1)
        self.assertEqual(vals["E2E_ALIVE_2"], 1)

  def test_rate_limits_zeroed_when_inactive(self):
    _, dat, _ = bydcan.create_steering_control(self.packer, 0.0, True)
    vals = _unpack(DBC_NAME, "STEERING_MODULE_ADAS", dat)
    self.assertEqual(vals["ANGLE_RATE_LIMIT_UPPER"], bydcan.ANGLE_RATE_LIMIT_UPPER)
    self.assertEqual(vals["ANGLE_RATE_LIMIT_LOWER"], bydcan.ANGLE_RATE_LIMIT_LOWER)

    _, dat, _ = bydcan.create_steering_control(self.packer, 0.0, False)
    vals = _unpack(DBC_NAME, "STEERING_MODULE_ADAS", dat)
    self.assertEqual(vals["ANGLE_RATE_LIMIT_UPPER"], 0)
    self.assertEqual(vals["ANGLE_RATE_LIMIT_LOWER"], 0)


class TestBydLkasHud(unittest.TestCase):
  def setUp(self):
    self.packer = CANPacker(DBC_NAME)
    # a stock frame with bits set in every field we touch and several we must not
    self.stock = {
      "HMA_STATE": 3, "LEFT_LANE_STATE": 1, "LKS_MODE": 2, "HANDS_ON_WHEEL_REQ": 1,
      "TJA_ICA_STATE": 5, "HMA_ON_OFF": 1, "LKAS_OUTPUT": -20, "LKAS_REQ_PREPARE": 1,
      "LKAS_ACTIVE": 1, "SLA_STATE": 3, "RIGHT_LANE_STATE": 1, "LKAS_STATE": 0b1000,
      "SPEED_LIMIT_VALUE": 100, "LDSW_TYPE": 2, "COUNTER": 9, "CHECKSUM": 0x11,
    }

  def test_passes_stock_bits_through(self):
    # The ADAS modules cross-check this frame; every bit we do not own must survive.
    _, dat, _ = bydcan.create_lkas_hud(self.packer, False, self.stock, None)
    vals = _unpack(DBC_NAME, "LKAS_HUD_ADAS", dat)
    for name in ("HMA_STATE", "LKS_MODE", "HANDS_ON_WHEEL_REQ", "TJA_ICA_STATE", "HMA_ON_OFF",
                 "LKAS_OUTPUT", "LKAS_REQ_PREPARE", "LKAS_ACTIVE", "SLA_STATE",
                 "SPEED_LIMIT_VALUE", "LDSW_TYPE"):
      self.assertEqual(vals[name], self.stock[name], f"{name} was modified")

  def test_hands_on_wheel_req_never_cleared(self):
    for lat_active in (True, False):
      _, dat, _ = bydcan.create_lkas_hud(self.packer, lat_active, self.stock, None)
      vals = _unpack(DBC_NAME, "LKAS_HUD_ADAS", dat)
      self.assertEqual(vals["HANDS_ON_WHEEL_REQ"], 1)

  def test_active_asserts_eps_arming_bits_only(self):
    _, dat, _ = bydcan.create_lkas_hud(self.packer, True, self.stock, None)
    vals = _unpack(DBC_NAME, "LKAS_HUD_ADAS", dat)
    # low 2 bits become 0b10, the stock upper 2 bits are preserved
    self.assertEqual(int(vals["LKAS_STATE"]), 0b1010)
    self.assertEqual(int(vals["LEFT_LANE_STATE"]), 1 | 2)
    self.assertEqual(int(vals["RIGHT_LANE_STATE"]), 1 | 2)

  def test_counter_not_inherited_from_stock(self):
    # inheriting the camera's counter would make our 50 Hz stream non-monotonic
    counters = []
    for _ in range(4):
      _, dat, _ = bydcan.create_lkas_hud(self.packer, True, self.stock, None)
      counters.append(int(_unpack(DBC_NAME, "LKAS_HUD_ADAS", dat)["COUNTER"]))
    self.assertNotEqual(counters, [self.stock["COUNTER"]] * 4)
    self.assertEqual(counters, [(counters[0] + i) % 16 for i in range(4)])

  def test_checksum_recomputed_not_inherited(self):
    _, dat, _ = bydcan.create_lkas_hud(self.packer, True, self.stock, None)
    self.assertEqual(dat[7], (~sum(dat[:7])) & 0xFF)


class TestBydAccCmd(unittest.TestCase):
  def setUp(self):
    self.packer = CANPacker(DBC_NAME)
    self.stock = {"ACCEL_CMD": 0.0, "COUNTER": 7, "CHECKSUM": 0x22}

  def test_accel_scale_is_physical(self):
    # raw x 0.05 - 5 m/s^2, so 0 m/s^2 is raw 100
    for accel in (-3.0, -1.5, 0.0, 0.5, 1.5):
      _, dat, _ = bydcan.create_acc_cmd(self.packer, accel, True, self.stock)
      self.assertEqual(dat[0], round((accel + 5.0) / 0.05))
      vals = _unpack(DBC_NAME, "ACC_CMD", dat)
      self.assertAlmostEqual(vals["ACCEL_CMD"], accel, places=6)

  def test_inactive_commands_zero_accel(self):
    _, dat, _ = bydcan.create_acc_cmd(self.packer, -2.0, False, self.stock)
    vals = _unpack(DBC_NAME, "ACC_CMD", dat)
    self.assertEqual(vals["ACCEL_CMD"], 0.0)
    self.assertEqual(dat[0], 100)
    self.assertEqual(vals["ACC_ON_1"], 0)
    self.assertEqual(vals["ACC_ON_2"], 0)
    self.assertEqual(vals["ACC_CONTROLLABLE_AND_ON"], 0)
    self.assertEqual(vals["CMD_REQ_ACTIVE_LOW"], 1)

  def test_standstill_hold_and_resume(self):
    _, dat, _ = bydcan.create_acc_cmd(self.packer, -0.5, True, self.stock, standstill=True)
    vals = _unpack(DBC_NAME, "ACC_CMD", dat)
    self.assertEqual(vals["STANDSTILL_STATE"], 1)
    self.assertEqual(vals["ACC_OVERRIDE_OR_STANDSTILL"], 1)
    self.assertEqual(vals["ACC_REQ_NOT_STANDSTILL"], 0)
    self.assertEqual(vals["STANDSTILL_RESUME"], 0)

    _, dat, _ = bydcan.create_acc_cmd(self.packer, 0.5, True, self.stock, standstill=True, resume=True)
    vals = _unpack(DBC_NAME, "ACC_CMD", dat)
    self.assertEqual(vals["STANDSTILL_RESUME"], 1)
    self.assertEqual(vals["STANDSTILL_STATE"], 0)
    self.assertEqual(vals["ACC_REQ_NOT_STANDSTILL"], 1)

  def test_regime_pairs(self):
    for accel, expected in ((0.0, (0, 0)), (0.05, (0, 0)), (0.8, (12, 5)),
                            (-1.0, (13, 1)), (-2.5, (1, 1))):
      _, dat, _ = bydcan.create_acc_cmd(self.packer, accel, True, self.stock)
      vals = _unpack(DBC_NAME, "ACC_CMD", dat)
      self.assertEqual((int(vals["ACCEL_FACTOR"]), int(vals["DECEL_FACTOR"])), expected, f"{accel=}")

  def test_accel_within_safety_bounds(self):
    # the comfort envelope must stay inside what byd.h allows (-3.5 .. +2.0)
    self.assertGreaterEqual(CarControllerParams.ACCEL_MIN, -3.5)
    self.assertLessEqual(CarControllerParams.ACCEL_MAX, 2.0)


class TestBydEpsState(unittest.TestCase):
  """The 0x1FC decode is the core fix over the Sealion 7 PR, which inherited a stub that
  packed these status bits into a fake 16-bit torque value."""

  def test_state_nibble_table(self):
    for prepared, activated, expected in (
      (0, 0, EPS_STATE_OFF),
      (1, 0, EPS_STATE_PREPARED),
      (0, 1, EPS_STATE_ACTUATING),
      (1, 1, EPS_STATE_LATCHED_FAULT),
    ):
      self.assertEqual(EPS_STATE_OFF + prepared + 2 * activated, expected)

  def test_steering_torque_signals_exist_and_are_signed(self):
    dbc = CANParser(DBC_NAME, [], 0).dbc
    sigs = dbc.name_to_msg["STEERING_TORQUE"].sigs
    for name in ("LKS_PREPARED", "CRUISE_ACTIVATED", "TORQUE_FAILED", "DRIVER_TORQUE",
                 "TARGET_ANGLE", "MAIN_TORQUE"):
      self.assertIn(name, sigs, f"{name} missing from STEERING_TORQUE")
    # driver torque must be signed or override detection cannot see direction
    self.assertTrue(sigs["DRIVER_TORQUE"].is_signed)
    self.assertTrue(sigs["MAIN_TORQUE"].is_signed)
    self.assertEqual(sigs["DRIVER_TORQUE"].start_bit, 4)
    self.assertEqual(sigs["DRIVER_TORQUE"].size, 12)
    self.assertEqual(sigs["MAIN_TORQUE"].start_bit, 32)
    self.assertEqual(sigs["MAIN_TORQUE"].size, 12)

  def test_driver_torque_decodes_negative(self):
    packer = CANPacker(DBC_NAME)
    for torque in (-20.0, -0.5, 0.0, 0.5, 20.0):
      _, dat, _ = packer.make_can_msg("STEERING_TORQUE", 0, {"DRIVER_TORQUE": torque})
      vals = _unpack(DBC_NAME, "STEERING_TORQUE", dat)
      self.assertAlmostEqual(vals["DRIVER_TORQUE"], torque, places=4)


class TestBydWheelSpeeds(unittest.TestCase):
  def test_four_independent_wheels(self):
    dbc = CANParser(DBC_NAME, [], 0).dbc
    sigs = dbc.name_to_msg["WHEEL_SPEEDS"].sigs
    for name, start in (("FL", 0), ("FR", 16), ("RL", 28), ("RR", 40)):
      self.assertEqual(sigs[name].start_bit, start)
      self.assertEqual(sigs[name].size, 12)
      self.assertAlmostEqual(sigs[name].factor, 0.0725)

  def test_wheel_speeds_round_trip(self):
    packer = CANPacker(DBC_NAME)
    _, dat, _ = packer.make_can_msg("WHEEL_SPEEDS", 0, {"FL": 50.0, "FR": 51.0, "RL": 52.0, "RR": 53.0})
    vals = _unpack(DBC_NAME, "WHEEL_SPEEDS", dat)
    for name, expected in (("FL", 50.0), ("FR", 51.0), ("RL", 52.0), ("RR", 53.0)):
      self.assertAlmostEqual(vals[name], expected, delta=0.0725)


class TestBydFingerprint(unittest.TestCase):
  def test_placeholder_never_matches_a_real_car(self):
    # a platform whose ECU dict is empty survives as a candidate for EVERY car, so the
    # placeholder must be a version no car reports rather than an empty dict
    self.assertTrue(FW_VERSIONS[CAR.BYD_SEALION_7], "empty ECU dict would match every car")

    live = build_fw_dict([CarParams.CarFw(ecu=Ecu.engine, fwVersion=b'REAL_CAR_FW', brand='byd',
                                          address=0x7e0, subAddress=0)])
    self.assertNotIn(str(CAR.BYD_SEALION_7), match_fw_to_car_exact(live, 'byd'))

  def test_fuzzy_match_requires_vds(self):
    # WMI + model year alone would claim every BYD of that year
    from iqdbc.car.byd.values import match_fw_to_car_fuzzy
    self.assertEqual(CAR.BYD_SEALION_7.config.vds_prefixes, set())
    vin = "LGX" + "A" * 6 + "R" + "A" * 7  # LGX, 2024 model year
    self.assertEqual(match_fw_to_car_fuzzy({}, vin, {}), set())


class TestBydCarController(unittest.TestCase):
  """The EPS latches a fault (state 11) if the 0x1E2 stream stops while it is actuating, and
  re-arms only on a STEER_REQ rising edge over a continuous stream. The safety also statically
  blocks the camera's own 0x1E2/0x316, so openpilot is the only source of both."""

  def _run(self, lat_active, long_active=False, frames=20):
    CP = CarInterface.get_non_essential_params("BYD_SEALION_7")
    CP_IQ = CarInterface.get_non_essential_params_iq(CP, "BYD_SEALION_7")
    CC_obj = structs.CarControl()
    CC_obj.enabled = lat_active
    CC_obj.latActive = lat_active
    CC_obj.longActive = long_active
    CC = CC_obj.as_reader()
    CC_IQ = structs.IQCarControl()

    carcontroller = CarInterface.CarController({'pt': DBC_NAME}, CP, CP_IQ)
    carstate = CarInterface.CarState(CP, CP_IQ)
    parsers = CarInterface.CarState.get_can_parsers(CP, CP_IQ)
    cs_out, _ = carstate.update(parsers)

    class _CS:
      pass
    cs = _CS()
    cs.out = cs_out
    cs.lkas_hud = carstate.lkas_hud
    cs.acc_cmd = carstate.acc_cmd
    cs.buttons = carstate.buttons

    sent = []
    for i in range(frames):
      _, can_sends = carcontroller.update(CC, CC_IQ, cs, i * 10_000_000)
      sent.append([addr for addr, _, _ in can_sends])
    return sent

  def test_steering_stream_is_continuous_when_inactive(self):
    for lat_active in (True, False):
      sent = self._run(lat_active)
      steering = [i for i, addrs in enumerate(sent) if 0x1E2 in addrs]
      hud = [i for i, addrs in enumerate(sent) if 0x316 in addrs]
      # every other frame, whether or not lateral is active
      self.assertEqual(steering, list(range(0, 20, 2)), f"{lat_active=}")
      self.assertEqual(hud, list(range(0, 20, 2)), f"{lat_active=}")

  def test_steer_req_gates_actuation_not_transmission(self):
    CP = CarInterface.get_non_essential_params("BYD_SEALION_7")
    CP_IQ = CarInterface.get_non_essential_params_iq(CP, "BYD_SEALION_7")
    carcontroller = CarInterface.CarController({'pt': DBC_NAME}, CP, CP_IQ)
    for lat_active in (False, True):
      _, dat, _ = bydcan.create_steering_control(carcontroller.packer, 0.0, lat_active)
      vals = _unpack(DBC_NAME, "STEERING_MODULE_ADAS", dat)
      self.assertEqual(vals["STEER_REQ"], 1 if lat_active else 0)

  def test_no_acc_cmd_without_openpilot_longitudinal(self):
    sent = self._run(True, long_active=True)
    self.assertFalse(any(0x32E in addrs for addrs in sent),
                     "0x32E sent while openpilotLongitudinalControl is off")


class TestBydLowSpeedAngleRate(unittest.TestCase):
  """Regression for the 2026-08-05 EPS latch. At 0.29 m/s the planner oscillated and the command
  swung -5.9 to +2.4 deg against a stationary wheel in 220 ms; the EPS went from state 9 straight
  to a latched 11 and took LKAS with it. The vehicle-model jerk limit cannot catch this because
  it scales as 1/v^2."""

  def _slew(self, v_ego, targets):
    CP = CarInterface.get_non_essential_params("BYD_SEALION_7")
    CP_IQ = CarInterface.get_non_essential_params_iq(CP, "BYD_SEALION_7")
    cc = CarInterface.CarController({'pt': DBC_NAME}, CP, CP_IQ)
    carstate = CarInterface.CarState(CP, CP_IQ)
    cs_out, _ = carstate.update(CarInterface.CarState.get_can_parsers(CP, CP_IQ))
    cs_out.vEgoRaw = v_ego
    cs_out.vEgo = v_ego
    cs_out.steeringAngleDeg = 0.1

    class _CS:
      pass
    cs = _CS()
    cs.out = cs_out
    cs.lkas_hud = carstate.lkas_hud
    cs.acc_cmd = carstate.acc_cmd
    cs.buttons = carstate.buttons

    CC_obj = structs.CarControl()
    CC_obj.enabled = True
    CC_obj.latActive = True
    CC_IQ = structs.IQCarControl()

    sent = []
    for i, tgt in enumerate(targets):
      CC_obj.actuators.steeringAngleDeg = tgt
      cc.update(CC_obj.as_reader(), CC_IQ, cs, i * 10_000_000)
      sent.append(cc.apply_angle_last)
    return sent

  # the actual planner output recorded during the fault
  OSCILLATION = [-2.9, -5.9, -2.9, 0.1, 1.7, 1.8, 2.4, 2.2] * 3

  def test_standstill_slew_is_bounded(self):
    v = 0.29  # the speed at which the EPS latched
    sent = self._slew(v, self.OSCILLATION)
    cap = float(np.interp(v, CarControllerParams.ANGLE_RATE_BP, CarControllerParams.ANGLE_RATE_V))
    steps = [abs(b - a) for a, b in zip(sent, sent[1:], strict=False)]
    self.assertLessEqual(max(steps), cap + 1e-6,
                         "command slews faster than the standstill rate cap")
    # the uncapped path stepped a full 3.0 deg/frame here
    self.assertLess(cap, 1.0)
    # and it must never wander far from the stationary wheel
    self.assertLess(max(abs(a - 0.1) for a in sent), 2.0,
                    "command diverged from the measured angle at a standstill")

  def test_rate_cap_scales_with_speed(self):
    slow = self._slew(0.0, [30.0] * 10)
    fast = self._slew(20.0, [30.0] * 10)
    slow_step = max(abs(b - a) for a, b in zip(slow, slow[1:], strict=False))
    fast_step = max(abs(b - a) for a, b in zip(fast, fast[1:], strict=False))
    self.assertLess(slow_step, fast_step, "low-speed cap must be tighter than at speed")
    self.assertLessEqual(fast_step, CarControllerParams.ANGLE_LIMITS.MAX_ANGLE_RATE + 1e-6)


class TestBydHarnessType(unittest.TestCase):
  """Longitudinal requires the ACC ECU to sit behind the relay so 0x32E is filterable. That is
  a property of the harness, and it cannot be inferred from the fingerprint: fingerprinting
  runs with the relay closed, which ties bus 2 to bus 0, so bus 2 shows the whole car either
  way. Default must therefore be the camera harness (lateral only)."""

  @staticmethod
  def _params(cam_bus_addrs, alpha_long=True):
    fp = {0: {0x1FC: 8, 0x1F0: 8}, 1: {}, 2: dict.fromkeys(cam_bus_addrs, 8)}
    return CarInterface.get_params("BYD_SEALION_7", fp, [], alpha_long, False, False)

  def test_defaults_to_camera_harness_lateral_only(self):
    CP = self._params([0x1E2, 0x316])
    self.assertFalse(CP.flags & BydFlags.GATEWAY_HARNESS)
    self.assertFalse(CP.alphaLongitudinalAvailable)
    self.assertFalse(CP.openpilotLongitudinalControl)
    self.assertFalse(CP.safetyConfigs[0].safetyParam & BydSafetyFlags.LONG_CONTROL)

  def test_acc_cmd_on_fingerprint_bus2_does_not_imply_gateway(self):
    # the relay is closed while fingerprinting, so bus 2 sees the chassis bus too. Seeing
    # 0x32E there must NOT unlock longitudinal.
    CP = self._params([0x1E2, 0x316, 0x32E, 0x32D, 0x1FC])
    self.assertFalse(CP.flags & BydFlags.GATEWAY_HARNESS)
    self.assertFalse(CP.alphaLongitudinalAvailable)
    self.assertFalse(CP.openpilotLongitudinalControl)

  def test_lateral_still_available_on_camera_harness(self):
    CP = self._params([0x1E2, 0x316])
    self.assertFalse(CP.dashcamOnly)
    self.assertEqual(CP.steerControlType, CarParams.SteerControlType.angle)


class TestBydCarParams(unittest.TestCase):
  def test_angle_control_and_no_radar(self):
    CP = CarInterface.get_non_essential_params("BYD_SEALION_7")
    self.assertEqual(CP.brand, "byd")
    self.assertEqual(CP.steerControlType, CarParams.SteerControlType.angle)
    self.assertEqual(CP.safetyConfigs[0].safetyModel, CarParams.SafetyModel.byd)
    # the BYD-6 harness jumpers the Veoneer private CAN-FD pair straight through
    self.assertTrue(CP.radarUnavailable)
    self.assertFalse(CP.dashcamOnly)

  def test_steer_step_matches_safety_frequency(self):
    # byd.h declares .frequency = 50U for the angle limiter
    self.assertEqual(CarControllerParams.STEER_STEP, 2)


if __name__ == "__main__":
  unittest.main()
