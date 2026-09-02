import copy

from iqdbc.car import Bus, structs
from iqdbc.can.parser import CANParser
from iqdbc.car.common.conversions import Conversions as CV
from iqdbc.car.byd.values import DBC, CarControllerParams as CCP
from iqdbc.car.interfaces import CarStateBase

GearShifter = structs.CarState.GearShifter

GEAR_MAP = {
  1: GearShifter.park,
  2: GearShifter.reverse,
  3: GearShifter.neutral,
  4: GearShifter.drive,
}

# STEERING_TORQUE low nibble, rebuilt from LKS_PREPARED + CRUISE_ACTIVATED
EPS_STATE_OFF = 8
EPS_STATE_PREPARED = 9
EPS_STATE_ACTUATING = 10
EPS_STATE_LATCHED_FAULT = 11

# ACC_HUD_ADAS.CRUISE_STATE
CRUISE_STATE_OFF = 0
CRUISE_STATE_AVAILABLE = 1
CRUISE_STATE_ENGAGED = 2


class CarState(CarStateBase):
  def __init__(self, CP, CP_IQ):
    super().__init__(CP, CP_IQ)
    self.lkas_hud = {}
    self.acc_cmd = {}
    self.buttons = {}
    self.eps_state = EPS_STATE_OFF
    self.eps_actuating = False
    self.override_latched = False
    self.lkas_btn_prev = False
    self.override_frames = 0
    self.release_frames = 0

  def update(self, can_parsers) -> tuple[structs.CarState, structs.IQCarState]:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]
    ret = structs.CarState()
    ret_iq = structs.IQCarState()

    ret.wheelSpeeds.fl = cp.vl["WHEEL_SPEEDS"]["FL"] * CV.KPH_TO_MS
    ret.wheelSpeeds.fr = cp.vl["WHEEL_SPEEDS"]["FR"] * CV.KPH_TO_MS
    ret.wheelSpeeds.rl = cp.vl["WHEEL_SPEEDS"]["RL"] * CV.KPH_TO_MS
    ret.wheelSpeeds.rr = cp.vl["WHEEL_SPEEDS"]["RR"] * CV.KPH_TO_MS
    self.parse_wheel_speeds(ret,
      cp.vl["WHEEL_SPEEDS"]["FL"],
      cp.vl["WHEEL_SPEEDS"]["FR"],
      cp.vl["WHEEL_SPEEDS"]["RL"],
      cp.vl["WHEEL_SPEEDS"]["RR"],
    )
    ret.standstill = ret.vEgoRaw < 0.01
    ret.vEgoCluster = ret.vEgo

    ret.steeringAngleDeg = cp.vl["STEER_MODULE_2"]["STEER_ANGLE_2"]
    # DRIVER_EPS_TORQUE (STEER_MODULE_2 byte 2, raw 0-255) is the column torque sensor = clean
    # driver input. Verified on route ff: 0 hands-off even while openpilot steers, rising only on
    # real wheel input (route max 79). Unlike STEERING_TORQUE.DRIVER_TORQUE it does NOT read the tire
    # self-aligning load (which hit 85 Nm hands-off and made the torque override chatter at every
    # threshold). Threshold 80: normal driver turns on this sensor peak ~52, so 80 clears them.
    ret.steeringTorque = cp.vl["STEER_MODULE_2"]["DRIVER_EPS_TORQUE"]
    ret.steeringTorqueEps = cp.vl["STEERING_TORQUE"]["MAIN_TORQUE"]
    ret.steeringPressed = self.update_steering_pressed(ret.steeringTorque > CCP.STEER_DRIVER_OVERRIDE, 5)
    # Disengagement on override is handled by the latch below, which also decides when
    # re-engagement is allowed, so no separate hard-disengage threshold here.
    ret.steeringDisengage = False

    # state 11 is a latched dropout: the command stream stopped while the EPS was actuating.
    # It clears only on a STEER_REQ rising edge over a continuous stream.
    lks_prepared = bool(cp.vl["STEERING_TORQUE"]["LKS_PREPARED"])
    cruise_activated = bool(cp.vl["STEERING_TORQUE"]["CRUISE_ACTIVATED"])
    self.eps_state = EPS_STATE_OFF + int(lks_prepared) + 2 * int(cruise_activated)
    # The EPS drives the wheel whenever CRUISE_ACTIVATED is set - that is nibble 10 AND 11.
    # Testing for nibble == 10 excluded the most common state and pinned the command to the
    # wheel there, leaving almost no steering authority.
    self.eps_actuating = cruise_activated

    # Use the EPS's own fault bits. Neither of the conditions this used to test is a fault:
    #  - nibble 11 (LKS_PREPARED + CRUISE_ACTIVATED) is the MOST COMMON operating state
    #    (14714 samples, TORQUE_FAILED=0 in every one, CRUISE_STATE=2, openpilot active).
    #    "state 11 = latched dropout" came from the Atto 3 notes and does not hold here.
    #  - LKAS_STATE 4 is the camera's state when IT is not commanding LKAS, which is normal
    #    while openpilot steers (1778 samples with openpilot active).
    # Reporting either as a fault produced constant phantom "Steering Assist Unavailable"
    # and, because we then refused to steer, blocked engagement.
    ret.steerFaultTemporary = bool(cp.vl["STEERING_TORQUE"]["TORQUE_TEMP_FAILED"])
    ret.steerFaultPermanent = bool(cp.vl["STEERING_TORQUE"]["TORQUE_FAILED"])

    # DRIVE_STATE.RAW_THROTTLE is powertrain torque demand, not the pedal
    ret.gasPressed = cp.vl["PEDAL"]["GAS_PEDAL"] > 0.10
    ret.brake = cp.vl["PEDAL"]["BRAKE_PEDAL"]
    # must stay the same bit byd_rx_hook reads, or the two engage latches desync on a light
    # brake graze and controlsd raises "Controls Mismatch"
    ret.brakePressed = bool(cp.vl["DRIVE_STATE"]["BRAKE_PRESSED"])

    ret.gearShifter = GEAR_MAP.get(int(cp.vl["DRIVE_STATE"]["GEAR"]), GearShifter.unknown)

    ret.leftBlinker = bool(cp.vl["STALKS"]["LEFT_BLINKER"])
    ret.rightBlinker = bool(cp.vl["STALKS"]["RIGHT_BLINKER"])

    ret.leftBlindspot = cp.vl["BSD_RADAR"]["LEFT_APPROACH"] != 0
    ret.rightBlindspot = cp.vl["BSD_RADAR"]["RIGHT_APPROACH"] != 0

    ret.doorOpen = any((
      cp.vl["METER_CLUSTER"]["FRONT_LEFT_DOOR"],
      cp.vl["METER_CLUSTER"]["FRONT_RIGHT_DOOR"],
      cp.vl["METER_CLUSTER"]["BACK_LEFT_DOOR"],
      cp.vl["METER_CLUSTER"]["BACK_RIGHT_DOOR"],
    ))
    ret.seatbeltUnlatched = not bool(cp.vl["METER_CLUSTER"]["SEATBELT_DRIVER"])

    # The ADAS/ACC ECU is on the chassis bus, not behind the camera relay, so these come off
    # bus 0. Bus 2 carries only the camera's own frames (0x1E2, 0x316, ...). This differs from
    # the Atto 3, where PR #3337 reads both from the camera bus.
    # CRUISE_STATE: 0=off, 1=available, 2=engaged, 3=engaged and commanding accel.
    # Do NOT use PR #3337/#3352's ACC_STATE (19|3) - byte 2 is a constant 0x3c on this car, so
    # it reads 7 (ERROR) forever and engagement can never happen.
    ret.cruiseState.speed = cp.vl["ACC_HUD_ADAS"]["SET_SPEED"] * CV.KPH_TO_MS
    cruise_state = int(cp.vl["ACC_HUD_ADAS"]["CRUISE_STATE"])
    ret.cruiseState.available = cruise_state >= CRUISE_STATE_AVAILABLE

    # cruiseState.enabled tracks the car's ACC engage bit directly (plain pcm_cruise), matching
    # the panda's controls_allowed. The old torque-based override latch that suppressed this was
    # the controlsMismatch source: the driver holding the wheel at ~15-19 Nm straddled the 18 Nm
    # threshold, so the latch flickered and dragged cruiseState.enabled True<->False while the
    # raw CRUISE_STATE sat steady at 2 - and python's and the panda's latches read the torque a
    # few frames apart, so they disagreed. Removed here AND in byd.h (acc_on = cruise_state>=2).
    # Driver override is now the standard path: openpilot yields on steeringPressed and the
    # carcontroller's TORQUE_BAIL drops REQ, without ever desyncing the engage state.
    self.override_latched = False
    ret.cruiseState.enabled = cruise_state >= CRUISE_STATE_ENGAGED
    ret.cruiseState.standstill = bool(cp.vl["ACC_CMD"]["STANDSTILL_STATE"])

    self.lkas_hud = copy.copy(cp_cam.vl["LKAS_HUD_ADAS"])
    self.acc_cmd = copy.copy(cp.vl["ACC_CMD"])
    self.buttons = copy.copy(cp.vl["PCM_BUTTONS"])

    return ret, ret_iq

  @staticmethod
  def get_can_parsers(CP, CP_IQ):
    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 0),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 2),
    }
