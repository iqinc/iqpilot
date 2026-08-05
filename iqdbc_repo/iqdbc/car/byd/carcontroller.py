import numpy as np

from iqdbc.can.packer import CANPacker
from iqdbc.car import Bus, structs
from iqdbc.car.lateral import apply_steer_angle_limits_vm
from iqdbc.car.interfaces import CarControllerBase
from iqdbc.car.byd import bydcan
from iqdbc.car.byd.values import CarControllerParams
from iqdbc.car.vehicle_model import VehicleModel

LongCtrlState = structs.CarControl.Actuators.LongControlState

ACC_STEP = 3  # ~33 Hz
ACC_DT = ACC_STEP * 0.01


class CarController(CarControllerBase):
  def __init__(self, dbc_names, CP, CP_IQ):
    super().__init__(dbc_names, CP, CP_IQ)
    self.packer = CANPacker(dbc_names[Bus.pt])
    self.apply_angle_last = 0.0
    self.accel_last = 0.0
    self.VM = VehicleModel(CP)

  def update(self, CC, CC_IQ, CS, now_nanos):
    can_sends = []
    actuators = CC.actuators

    # 0x1E2/0x316 go out unconditionally, gated only by STEER_REQ: the safety blocks the
    # camera's copies, and the EPS latches a fault if the stream stops while it is actuating.
    if self.frame % CarControllerParams.STEER_STEP == 0:
      apply_angle = apply_steer_angle_limits_vm(actuators.steeringAngleDeg, self.apply_angle_last,
                                                CS.out.vEgoRaw, CS.out.steeringAngleDeg,
                                                CC.latActive, CarControllerParams, self.VM)

      # The vehicle-model jerk limit stops binding below a few m/s, so cap the slew rate
      # directly there. Without this the planner's standstill oscillation drives the command
      # tens of degrees away from a stationary wheel and the EPS latches state 11.
      if CC.latActive:
        max_rate = float(np.interp(CS.out.vEgoRaw, CarControllerParams.ANGLE_RATE_BP,
                                   CarControllerParams.ANGLE_RATE_V))
        apply_angle = float(np.clip(apply_angle, self.apply_angle_last - max_rate,
                                    self.apply_angle_last + max_rate))

        # Never wind the command away from the wheel: the EPS latches on angle divergence, and
        # a driver holding the wheel below the override threshold would otherwise let the
        # controller run tens of degrees past it.
        err = CarControllerParams.MAX_ANGLE_ERROR
        apply_angle = float(np.clip(apply_angle, CS.out.steeringAngleDeg - err,
                                    CS.out.steeringAngleDeg + err))
      self.apply_angle_last = apply_angle

      can_sends.append(bydcan.create_steering_control(self.packer, self.apply_angle_last, CC.latActive))
      can_sends.append(bydcan.create_lkas_hud(self.packer, CC.latActive, CS.lkas_hud, CC.hudControl))

    accel = 0.0
    if self.CP.openpilotLongitudinalControl and self.frame % ACC_STEP == 0:
      if CC.longActive:
        accel = self._apply_long_limits(actuators, CS, CC)
      else:
        self.accel_last = float(np.clip(CS.out.aEgo, CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX))

      lcs = actuators.longControlState
      stopping = (lcs == LongCtrlState.stopping) or (CS.out.standstill and accel <= 0.0)
      resume = (lcs == LongCtrlState.starting) or CC.cruiseControl.resume
      can_sends.append(bydcan.create_acc_cmd(self.packer, accel, CC.longActive, CS.acc_cmd,
                                             standstill=stopping and CS.out.standstill, resume=resume))

    new_actuators = actuators.as_builder()
    new_actuators.steeringAngleDeg = float(self.apply_angle_last)
    new_actuators.accel = accel

    self.frame += 1
    return new_actuators, can_sends

  def _apply_long_limits(self, actuators, CS, CC) -> float:
    target = float(np.clip(actuators.accel, CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX))

    launch = CS.out.vEgo < 2.0 and target > 0.0
    up = (CarControllerParams.JERK_UP_LAUNCH if launch else CarControllerParams.JERK_UP) * ACC_DT
    down = CarControllerParams.JERK_DOWN * ACC_DT

    # the hold parks the ramp at the stopping brake; snap to 0 so the launch kick applies
    # immediately instead of ramping back through the negative band while ESC-held
    resume = (actuators.longControlState == LongCtrlState.starting) or CC.cruiseControl.resume
    if resume and CS.out.standstill and self.accel_last < 0.0:
      self.accel_last = 0.0

    accel = float(np.clip(target, self.accel_last - down, self.accel_last + up))
    self.accel_last = accel
    return accel
