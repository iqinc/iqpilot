# iqdbc/can/dbc.py imports byd_checksum from here, so this module must not import from
# iqdbc.car (circular import at DBC parse time).

# Measured off the stock camera while it was actively steering (route 0000007b--88dd577c32):
# every engaged 0x1E2 carries +500/-500 and byte5 = 0x64. 251/-252/0xFF came from Atto 3 notes.
ANGLE_RATE_LIMIT_UPPER = 500
ANGLE_RATE_LIMIT_LOWER = -500
SET_ME_FF_VALUE = 0x64

# 0x316 LKAS_STATE, in the order the stock camera walks them on an engage
LKAS_STATE_IDLE = 1
LKAS_STATE_SUSPENDED = 2
LKAS_STATE_ACTIVE = 3
LKAS_STATE_PREPARING = 5

# COUNTER and CHECKSUM are filled in by the packer from the DBC signal types, so they are
# stripped from any stock frame we pass through rather than inherited.
_GENERATED = ("COUNTER", "CHECKSUM")


def byd_checksum(address: int, sig, d: bytearray) -> int:
  return (~sum(d[:7])) & 0xFF


def _passthrough(stock: dict) -> dict:
  return {k: v for k, v in stock.items() if k not in _GENERATED}


def create_steering_control(packer, apply_angle: float, steer_req: bool):
  # The rate limits and STEER_REQ_ACTIVE_LOW do NOT track STEER_REQ. Measured on the stock
  # camera's idle frame (f4 31 c8 .. .. 64, 47625 samples): it holds +500/-500 and keeps
  # STEER_REQ_ACTIVE_LOW at 0 with STEER_REQ=0. Zeroing the limits and asserting ACTIVE_LOW while
  # idle parks the EPS in nibble 11 (LKS_PREPARED+CRUISE_ACTIVATED) permanently, a state the stock
  # camera never produces while steering.
  values = {
    "STEER_REQ": 1 if steer_req else 0,
    "STEER_REQ_ACTIVE_LOW": 0,
    "STEER_ANGLE": apply_angle,
    "ANGLE_RATE_LIMIT_UPPER": ANGLE_RATE_LIMIT_UPPER,
    "ANGLE_RATE_LIMIT_LOWER": ANGLE_RATE_LIMIT_LOWER,
    "E2E_ALIVE_1": 1,
    "E2E_ALIVE_2": 1,
    "SET_ME_FF": SET_ME_FF_VALUE,
    "SET_ME_F": 0xF,
  }
  return packer.make_can_msg("STEERING_MODULE_ADAS", 0, values)


def create_lkas_hud(packer, lkas_state: int, lkas_active: bool, stock_lkas_hud: dict, hud_control):
  # The ADAS modules cross-check this frame's exact bit pattern every cycle and fail-safe on a
  # mismatch, so this reproduces the stock camera's frames field for field rather than
  # synthesizing them. Reference (dashcam-mode capture, camera driving ICC natively):
  #   idle       ACTIVE=0 PREPARE=0 LKAS_STATE=1 L=0 R=0  cf 84 00 e0 13 ff 71 49
  #   preparing  ACTIVE=0 PREPARE=0 LKAS_STATE=5 L=1 R=1  df 84 00 e0 57 ff 11 55
  #   engaged    ACTIVE=1 PREPARE=0 LKAS_STATE=3 L=1 R=1  df 84 00 f0 37 ff 61 15
  #   suspended  ACTIVE=0 PREPARE=1 LKAS_STATE=2 L=1 R=1  df 84 00 e8 27 ff 41 4d
  values = _passthrough(stock_lkas_hud)

  # The camera's health fields are NOT passed through: with its 0x1E2 blocked by the relay it
  # can never actuate, so it permanently reports its own failure (TJA_ICA_STATE/MPCErr=2 with
  # LKAS_STATE=4) and forwarding that painted standing ADAS errors on the cluster.
  values["TJA_ICA_STATE"] = 0

  values["LKAS_STATE"] = lkas_state
  values["LKAS_ACTIVE"] = 1 if lkas_active else 0
  # PREPARE accompanies exactly the suspend state: 9/9 stock suspend runs have it, 0 frames
  # anywhere else
  values["LKAS_REQ_PREPARE"] = 1 if lkas_state == LKAS_STATE_SUSPENDED else 0

  # stock lights the lanes in every non-idle state, including preparing and suspend
  if lkas_state != LKAS_STATE_IDLE:
    values["LEFT_LANE_STATE"] = 1
    values["RIGHT_LANE_STATE"] = 1

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
