# iqdbc/can/dbc.py imports byd_checksum from here, so this module must not import from
# iqdbc.car (circular import at DBC parse time).

# stock camera saturates the 0x1E2 rate limits here while steering
ANGLE_RATE_LIMIT_UPPER = 251
ANGLE_RATE_LIMIT_LOWER = -252

# COUNTER and CHECKSUM are filled in by the packer from the DBC signal types, so they are
# stripped from any stock frame we pass through rather than inherited.
_GENERATED = ("COUNTER", "CHECKSUM")


def byd_checksum(address: int, sig, d: bytearray) -> int:
  return (~sum(d[:7])) & 0xFF


def _passthrough(stock: dict) -> dict:
  return {k: v for k, v in stock.items() if k not in _GENERATED}


def create_steering_control(packer, apply_angle: float, lat_active: bool):
  values = {
    "STEER_REQ": 1 if lat_active else 0,
    "STEER_REQ_ACTIVE_LOW": 0 if lat_active else 1,
    "STEER_ANGLE": apply_angle,
    "ANGLE_RATE_LIMIT_UPPER": ANGLE_RATE_LIMIT_UPPER if lat_active else 0,
    "ANGLE_RATE_LIMIT_LOWER": ANGLE_RATE_LIMIT_LOWER if lat_active else 0,
    "E2E_ALIVE_1": 1,
    "E2E_ALIVE_2": 1,
    "SET_ME_FF": 0xFF,
    "SET_ME_F": 0xF,
  }
  return packer.make_can_msg("STEERING_MODULE_ADAS", 0, values)


def create_lkas_hud(packer, lat_active: bool, stock_lkas_hud: dict, hud_control):
  # The ADAS modules cross-check this frame's exact bit pattern every cycle and fail-safe on a
  # mismatch, so only the bits proven to arm the EPS are asserted; everything else passes through.
  values = _passthrough(stock_lkas_hud)
  if lat_active:
    values["LKAS_STATE"] = (int(stock_lkas_hud["LKAS_STATE"]) & 0b1100) | 0b0010
    values["LEFT_LANE_STATE"] = int(stock_lkas_hud["LEFT_LANE_STATE"]) | 2
    values["RIGHT_LANE_STATE"] = int(stock_lkas_hud["RIGHT_LANE_STATE"]) | 2

  if hud_control is not None:
    if hud_control.leftLaneDepart:
      values["LEFT_LANE_STATE"] = 2
    if hud_control.rightLaneDepart:
      values["RIGHT_LANE_STATE"] = 2

  return packer.make_can_msg("LKAS_HUD_ADAS", 0, values)


def create_acc_cmd(packer, accel: float, long_active: bool, stock_acc_cmd: dict,
                   standstill: bool = False, resume: bool = False):
  # ACCEL_FACTOR/DECEL_FACTOR select the IPB gain profile: coast, soft accel, soft decel,
  # sustained brake. Pairs are stock's modal values per accel band.
  holding = long_active and standstill and not resume

  if not long_active or abs(accel) < 0.1:
    accel_fac, decel_fac = 0, 0
  elif accel > 0:
    accel_fac, decel_fac = 12, 5
  elif accel > -1.5:
    accel_fac, decel_fac = 13, 1
  else:
    accel_fac, decel_fac = 1, 1

  values = {
    **_passthrough(stock_acc_cmd),
    "ACCEL_CMD": accel if long_active else 0.0,
    "ACC_ON_1": 1 if long_active else 0,
    "ACC_ON_2": 1 if long_active else 0,
    "ACC_CONTROLLABLE_AND_ON": 1 if long_active else 0,
    "ACC_REQ_NOT_STANDSTILL": 0 if holding else (1 if long_active else 0),
    "CMD_REQ_ACTIVE_LOW": 0 if long_active else 1,
    "ACC_OVERRIDE_OR_STANDSTILL": 1 if holding else 0,
    "STANDSTILL_RESUME": 1 if (long_active and resume) else 0,
    "STANDSTILL_STATE": 1 if holding else 0,
    "ACCEL_FACTOR": accel_fac,
    "DECEL_FACTOR": decel_fac,
    "SET_ME_25_1": 25,
    "SET_ME_25_2": 25,
    "SET_ME_1": 1,
    "SET_ME_X8": 8,
    "SET_ME_XF": 15,
  }
  return packer.make_can_msg("ACC_CMD", 0, values)


def create_buttons(packer, stock_buttons: dict, cancel: bool):
  values = {
    **_passthrough(stock_buttons),
    "SET_ME_1_1": 1,
    "SET_ME_1_2": 1,
    "ACC_ON_BTN": 1 if cancel else 0,
  }
  return packer.make_can_msg("PCM_BUTTONS", 0, values)
