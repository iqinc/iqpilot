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


class CarState(CarStateBase):
  def __init__(self, CP, CP_IQ):
    super().__init__(CP, CP_IQ)
    self.lkas_hud = {}
    self.acc_cmd = {}
    self.buttons = {}
    self.eps_state = EPS_STATE_OFF

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

    # both torques come from STEERING_TORQUE; 0x11F's 16|8 field is unsigned and reads as a
    # steering rate in BYD's own firmware, so it is not used for override detection
    ret.steeringAngleDeg = cp.vl["STEER_MODULE_2"]["STEER_ANGLE_2"]
    ret.steeringTorque = cp.vl["STEERING_TORQUE"]["DRIVER_TORQUE"]
    ret.steeringTorqueEps = cp.vl["STEERING_TORQUE"]["MAIN_TORQUE"]
    ret.steeringPressed = self.update_steering_pressed(abs(ret.steeringTorque) > CCP.STEER_DRIVER_OVERRIDE, 5)
    ret.steeringDisengage = abs(ret.steeringTorque) > CCP.STEER_DRIVER_DISENGAGE

    # state 11 is a latched dropout: the command stream stopped while the EPS was actuating.
    # It clears only on a STEER_REQ rising edge over a continuous stream.
    lks_prepared = bool(cp.vl["STEERING_TORQUE"]["LKS_PREPARED"])
    cruise_activated = bool(cp.vl["STEERING_TORQUE"]["CRUISE_ACTIVATED"])
    self.eps_state = EPS_STATE_OFF + int(lks_prepared) + 2 * int(cruise_activated)

    ret.steerFaultTemporary = self.eps_state == EPS_STATE_LATCHED_FAULT
    ret.steerFaultTemporary |= int(cp_cam.vl["LKAS_HUD_ADAS"]["LKAS_STATE"]) == 4
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

    # ACC_STATE: 0=OFF, 2=AVAILABLE, 3=ACTIVE, 5=OVERRIDE, 7=ERROR
    ret.cruiseState.speed = cp_cam.vl["ACC_HUD_ADAS"]["SET_SPEED"] * CV.KPH_TO_MS
    acc_state = int(cp_cam.vl["ACC_HUD_ADAS"]["ACC_STATE"])
    ret.cruiseState.available = acc_state in (2, 3, 5)
    ret.cruiseState.enabled = acc_state in (3, 5)
    ret.cruiseState.standstill = bool(cp_cam.vl["ACC_CMD"]["STANDSTILL_STATE"])

    self.lkas_hud = copy.copy(cp_cam.vl["LKAS_HUD_ADAS"])
    self.acc_cmd = copy.copy(cp_cam.vl["ACC_CMD"])
    self.buttons = copy.copy(cp.vl["PCM_BUTTONS"])

    return ret, ret_iq

  @staticmethod
  def get_can_parsers(CP, CP_IQ):
    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 0),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 2),
    }
