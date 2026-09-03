#!/usr/bin/env python3
import unittest
import unittest.mock

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
        # despite the name, the stock camera never inverts this - 0 in every engaged frame and
        # in 47625 of 47660 idle frames
        self.assertEqual(vals["STEER_REQ_ACTIVE_LOW"], 0)
        self.assertEqual(vals["E2E_ALIVE_1"], 1)
        self.assertEqual(vals["E2E_ALIVE_2"], 1)

  def test_rate_limits_held_when_inactive(self):
    # The camera holds +500/-500 with STEER_REQ=0 (idle frame f4 31 c8 .. .. 64). Zeroing them
    # while idle parked the EPS in nibble 11 permanently.
    for lat_active in (True, False):
      _, dat, _ = bydcan.create_steering_control(self.packer, 0.0, lat_active)
      vals = _unpack(DBC_NAME, "STEERING_MODULE_ADAS", dat)
      self.assertEqual(vals["ANGLE_RATE_LIMIT_UPPER"], bydcan.ANGLE_RATE_LIMIT_UPPER)
      self.assertEqual(vals["ANGLE_RATE_LIMIT_LOWER"], bydcan.ANGLE_RATE_LIMIT_LOWER)

  def _oem_ceiling(self):
    return unittest.mock.patch.multiple(
      bydcan,
      ANGLE_RATE_LIMIT_UPPER=bydcan.OEM_ANGLE_RATE_LIMIT,
      ANGLE_RATE_LIMIT_LOWER=-bydcan.OEM_ANGLE_RATE_LIMIT,
    )

  def test_matches_stock_camera_idle_frame(self):
    # stock idle: f4 31 c8 .. .. 64 (47625 samples); only the angle bytes differ. Checked with the
    # OEM ceiling so the frame stays provably stock-shaped independent of the shipped ceiling.
    with self._oem_ceiling():
      _, dat, _ = bydcan.create_steering_control(self.packer, 0.0, False)
    self.assertEqual(bytes(dat[:3]), bytes.fromhex("f431c8"),
                     f"idle 0x1E2 diverges from stock: {bytes(dat[:3]).hex(' ')} != f4 31 c8")
    self.assertEqual(dat[5], 0x64)

  def test_matches_stock_camera_engaged_frame(self):
    # Byte-for-byte against a frame the stock camera actually sent while steering, captured in
    # dashcam mode (route 0000007b--88dd577c32): f4 31 e8 01 00 64 9f ee at +0.1 deg.
    # Only COUNTER/CHECKSUM (byte 6 high nibble, byte 7) may differ.
    OEM = bytes.fromhex("f431e8010064")
    with self._oem_ceiling():
      _, dat, _ = bydcan.create_steering_control(self.packer, 0.1, True)
    self.assertEqual(bytes(dat[:6]), OEM,
                     f"0x1E2 diverges from stock: {bytes(dat[:6]).hex(' ')} != {OEM.hex(' ')}")
    self.assertEqual(dat[6] & 0x0F, 0x0F)

  def test_authority_ceiling_is_symmetric_and_within_stock(self):
    # The ceiling caps how hard the EPS may push, so it must never exceed what stock authorises,
    # and it must stay symmetric or the EPS gets more authority one way than the other.
    self.assertEqual(bydcan.ANGLE_RATE_LIMIT_LOWER, -bydcan.ANGLE_RATE_LIMIT_UPPER)
    self.assertLessEqual(bydcan.ANGLE_RATE_LIMIT_UPPER, bydcan.OEM_ANGLE_RATE_LIMIT)
    self.assertGreater(bydcan.ANGLE_RATE_LIMIT_UPPER, 0)
    self.assertEqual(bydcan.SET_ME_FF_VALUE, 0x64)
    _, dat, _ = bydcan.create_steering_control(self.packer, 0.0, True)
    self.assertEqual(_unpack(DBC_NAME, "STEERING_MODULE_ADAS", dat)["SET_ME_FF"], 0x64)


class TestBydAngleEnvelope(unittest.TestCase):
  """Guards the command envelope against the stock camera's measured behaviour."""

  def test_clamp_is_below_bail(self):
    # The clamp holds the command within MAX_ANGLE_ERROR of the wheel; the bail drops STEER_REQ
    # above ANGLE_ERROR_BAIL. If the clamp permits what the bail punishes, every firm turn
    # cuts steering and chatters - that shipped as 6.0/5.0 and broke cornering on-car.
    self.assertLess(CarControllerParams.MAX_ANGLE_ERROR, CarControllerParams.ANGLE_ERROR_BAIL)
    self.assertLess(CarControllerParams.MAX_ANGLE_ERROR_INACTIVE,
                    CarControllerParams.MAX_ANGLE_ERROR)

  def test_envelope_covers_stock_tracking_error(self):
    # stock ICC engaged: p99 3.31, p99.9 10.61, max 19.70 deg of |commanded - measured|
    self.assertGreater(CarControllerParams.MAX_ANGLE_ERROR, 10.61)
    self.assertGreater(CarControllerParams.ANGLE_ERROR_BAIL, 19.70)

  def test_command_rate_stays_within_stock(self):
    # stock never steps its own command more than 1.30 deg/frame
    self.assertLessEqual(max(CarControllerParams.ANGLE_RATE_V),
                         CarControllerParams.ANGLE_LIMITS.MAX_ANGLE_RATE)
    self.assertLess(CarControllerParams.ANGLE_LIMITS.MAX_ANGLE_RATE, 3)


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
    _, dat, _ = bydcan.create_lkas_hud(self.packer, bydcan.LKAS_STATE_IDLE, False, self.stock, None)
    vals = _unpack(DBC_NAME, "LKAS_HUD_ADAS", dat)
    for name in ("HMA_STATE", "LKS_MODE", "HANDS_ON_WHEEL_REQ", "HMA_ON_OFF",
                 "LKAS_OUTPUT", "SLA_STATE",
                 "SPEED_LIMIT_VALUE", "LDSW_TYPE"):
      self.assertEqual(vals[name], self.stock[name], f"{name} was modified")

  def test_hands_on_wheel_req_never_cleared(self):
    for state, active in ((bydcan.LKAS_STATE_ACTIVE, True), (bydcan.LKAS_STATE_IDLE, False)):
      _, dat, _ = bydcan.create_lkas_hud(self.packer, state, active, self.stock, None)
      vals = _unpack(DBC_NAME, "LKAS_HUD_ADAS", dat)
      self.assertEqual(vals["HANDS_ON_WHEEL_REQ"], 1)

  def test_camera_error_state_never_forwarded(self):
    # camera reports its own failure (TJA_ICA_STATE=2, LKAS_STATE=4) because its 0x1E2 is
    # blocked; forwarding that paints standing ADAS errors on the cluster
    err_stock = dict(self.stock, TJA_ICA_STATE=2, LKAS_STATE=4)
    for state, active in ((bydcan.LKAS_STATE_ACTIVE, True), (bydcan.LKAS_STATE_PREPARING, False),
                          (bydcan.LKAS_STATE_IDLE, False)):
      _, dat, _ = bydcan.create_lkas_hud(self.packer, state, active, err_stock, None)
      vals = _unpack(DBC_NAME, "LKAS_HUD_ADAS", dat)
      self.assertEqual(int(vals["TJA_ICA_STATE"]), 0)
      self.assertEqual(int(vals["LKAS_STATE"]), state)

  def test_matches_stock_camera_frames(self):
    # Byte-for-byte against the camera's own frames (route 0000007b--88dd577c32).
    # SPEED_LIMIT_VALUE is (5, -5), so the stock 0xff raw byte is 1270 kph (no limit / SNA)
    stock = dict(self.stock, HMA_STATE=15, LKS_MODE=3, HANDS_ON_WHEEL_REQ=1, HMA_ON_OFF=1,
                 LKAS_OUTPUT=0, SLA_STATE=7, SPEED_LIMIT_VALUE=1270, LDSW_TYPE=1, SET_ME_3=3)
    for state, active, oem in (
        (bydcan.LKAS_STATE_ACTIVE, True, "df8400f037ff"),      # engaged
        (bydcan.LKAS_STATE_PREPARING, False, "df8400e057ff"),  # preparing
        (bydcan.LKAS_STATE_SUSPENDED, False, "df8400e827ff"),  # suspend after override
    ):
      _, dat, _ = bydcan.create_lkas_hud(self.packer, state, active, stock, None)
      self.assertEqual(bytes(dat[:6]), bytes.fromhex(oem),
                       f"state {state} 0x316 diverges from stock: {bytes(dat[:6]).hex(' ')} != {oem}")
    # byte 6 low nibble is LDSW_TYPE (stock 1); the high nibble is our own COUNTER
    self.assertEqual(dat[6] & 0x0F, 0x01)

  def test_prepare_bit_accompanies_exactly_the_suspend_state(self):
    # 9/9 stock suspend runs have PREPARE=1; 0 frames anywhere else (stock passes it as 1 here
    # to prove we do not inherit it outside suspend)
    for state, active, expected in ((bydcan.LKAS_STATE_IDLE, False, 0),
                                    (bydcan.LKAS_STATE_PREPARING, False, 0),
                                    (bydcan.LKAS_STATE_ACTIVE, True, 0),
                                    (bydcan.LKAS_STATE_SUSPENDED, False, 1)):
      _, dat, _ = bydcan.create_lkas_hud(self.packer, state, active, self.stock, None)
      vals = _unpack(DBC_NAME, "LKAS_HUD_ADAS", dat)
      self.assertEqual(int(vals["LKAS_REQ_PREPARE"]), expected)

  def test_lane_bits_lit_in_every_non_idle_state(self):
    stock = dict(self.stock, LEFT_LANE_STATE=0, RIGHT_LANE_STATE=0)
    for state, active in ((bydcan.LKAS_STATE_PREPARING, False), (bydcan.LKAS_STATE_ACTIVE, True),
                          (bydcan.LKAS_STATE_SUSPENDED, False)):
      _, dat, _ = bydcan.create_lkas_hud(self.packer, state, active, stock, None)
      vals = _unpack(DBC_NAME, "LKAS_HUD_ADAS", dat)
      # stock uses 1, not a bitmask - 2 and 3 are never observed on an engaged frame
      self.assertEqual(int(vals["LEFT_LANE_STATE"]), 1)
      self.assertEqual(int(vals["RIGHT_LANE_STATE"]), 1)

  def test_counter_not_inherited_from_stock(self):
    # inheriting the camera's counter would make our 50 Hz stream non-monotonic
    counters = []
    for _ in range(4):
      _, dat, _ = bydcan.create_lkas_hud(self.packer, bydcan.LKAS_STATE_ACTIVE, True, self.stock, None)
      counters.append(int(_unpack(DBC_NAME, "LKAS_HUD_ADAS", dat)["COUNTER"]))
    self.assertNotEqual(counters, [self.stock["COUNTER"]] * 4)
    self.assertEqual(counters, [(counters[0] + i) % 16 for i in range(4)])

  def test_checksum_recomputed_not_inherited(self):
    _, dat, _ = bydcan.create_lkas_hud(self.packer, bydcan.LKAS_STATE_ACTIVE, True, self.stock, None)
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
    cs.eps_state = EPS_STATE_ACTUATING
    cs.eps_actuating = True
    cs.override_latched = False
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


class TestBydSteerReqGating(unittest.TestCase):
  """STEER_REQ is the only actuation gate. The frames stream continuously either way, because the
  EPS latches a fault if the 0x1E2 stream stops while it is actuating."""

  def _controller(self):
    CP = CarInterface.get_non_essential_params("BYD_SEALION_7")
    CP_IQ = CarInterface.get_non_essential_params_iq(CP, "BYD_SEALION_7")
    return CarInterface.CarController({'pt': DBC_NAME}, CP, CP_IQ), CP, CP_IQ

  def _step(self, cc, CP, CP_IQ, lat_active, eps_state, angle=0.0, frames=4):
    CC_obj = structs.CarControl()
    CC_obj.enabled = lat_active
    CC_obj.latActive = lat_active
    CC = CC_obj.as_reader()
    carstate = CarInterface.CarState(CP, CP_IQ)
    cs_out, _ = carstate.update(CarInterface.CarState.get_can_parsers(CP, CP_IQ))

    class _CS:
      pass
    cs = _CS()
    cs.out = cs_out
    cs.eps_state = eps_state
    cs.eps_actuating = eps_state == EPS_STATE_ACTUATING
    cs.override_latched = False
    cs.lkas_hud = carstate.lkas_hud
    cs.acc_cmd = carstate.acc_cmd
    cs.buttons = carstate.buttons

    out = []
    for i in range(frames):
      _, can_sends = cc.update(CC, structs.IQCarControl(), cs, i * 10_000_000)
      for addr, dat, _bus in can_sends:
        if addr == 0x1E2:
          out.append(_unpack(DBC_NAME, "STEERING_MODULE_ADAS", dat))
    return out

  def test_req_follows_lat_active(self):
    cc, CP, CP_IQ = self._controller()
    for lat_active in (True, False):
      frames = self._step(cc, CP, CP_IQ, lat_active, EPS_STATE_ACTUATING)
      self.assertTrue(frames)
      for vals in frames:
        self.assertEqual(int(vals["STEER_REQ"]), 1 if lat_active else 0)

  def test_latched_fault_drops_req(self):
    # A transient nibble 11 must drop STEER_REQ within a frame; holding it there latches the ADAS
    # for the rest of the drive.
    cc, CP, CP_IQ = self._controller()
    frames = self._step(cc, CP, CP_IQ, True, EPS_STATE_LATCHED_FAULT)
    self.assertTrue(frames)
    for vals in frames:
      self.assertEqual(int(vals["STEER_REQ"]), 0)

  def test_command_never_exceeds_eps_fault_angle(self):
    # The EPS faults out past ~90 deg of commanded wheel angle.
    self.assertLessEqual(CarControllerParams.ANGLE_LIMITS.STEER_ANGLE_MAX, 90.)


class TestBydLowSpeedAngleRate(unittest.TestCase):
  """Regression for the 2026-08-05 EPS latch. At 0.29 m/s the planner oscillated and the command
  swung -5.9 to +2.4 deg against a stationary wheel in 220 ms; the EPS went from state 9 straight
  to a latched 11. The rate curve is tightest at a standstill so the command cannot run away from
  a wheel that is not moving."""

  def _cap(self, v):
    bp, vals = CarControllerParams.ANGLE_LIMITS.ANGLE_RATE_LIMIT_UP
    return float(np.interp(v, bp, vals))

  def test_standstill_slew_is_bounded(self):
    self.assertLessEqual(self._cap(0.0), 0.5)

  def test_rate_cap_scales_with_speed(self):
    self.assertLess(self._cap(0.0), self._cap(5.0), "low-speed cap must be tighter than at speed")

  def test_rate_stays_within_stock_command_step(self):
    # the stock camera never steps its own command more than 1.30 deg/frame
    _bp, vals = CarControllerParams.ANGLE_LIMITS.ANGLE_RATE_LIMIT_UP
    self.assertLessEqual(max(vals), 1.30)


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
