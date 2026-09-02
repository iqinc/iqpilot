import math
import numpy as np

from iqdbc.can.packer import CANPacker
from iqdbc.car import Bus, structs
from iqdbc.car.lateral import apply_std_steer_angle_limits
from iqdbc.car.interfaces import CarControllerBase
from iqdbc.car.byd import bydcan
from iqdbc.car.byd.carstate import EPS_STATE_LATCHED_FAULT
from iqdbc.car.byd.values import CarControllerParams
from iqdbc.car.vehicle_model import VehicleModel

LongCtrlState = structs.CarControl.Actuators.LongControlState

ACC_STEP = 3  # ~33 Hz
ACC_DT = ACC_STEP * 0.01
STEER_DT = 0.02  # STEER_STEP (2) at the 100 Hz base loop = 50 Hz command


class CarController(CarControllerBase):
  def __init__(self, dbc_names, CP, CP_IQ):
    super().__init__(dbc_names, CP, CP_IQ)
    self.packer = CANPacker(dbc_names[Bus.pt])
    self.apply_angle_last = 0.0
    self.accel_last = 0.0
    self.VM = VehicleModel(CP)
    self.hands_on_frames = 0
    self.resume_frames = 0
    self.angle_cmd_filtered = 0.0
    self.low_speed_latch = True
    self.tight_turn_latch = False

  def update(self, CC, CC_IQ, CS, now_nanos):
    can_sends = []
    actuators = CC.actuators

    # 0x1E2/0x316 go out unconditionally, gated only by STEER_REQ: the safety blocks the
    # camera's copies, and the EPS latches a fault if the stream stops while it is actuating.
    if self.frame % CarControllerParams.STEER_STEP == 0:
      # MINIMAL steering shape (bark bisect baseline). Only three things:
      #  1. std angle-rate limiter (flat speed-interpolated curve in ANGLE_LIMITS, max 85 deg so the
      #     command never reaches the ~90 deg EPS fault),
      #  2. soft anti-windup clamp (command never sits more than MAX_ANGLE_ERROR from the wheel - this
      #     IS the driver override: openpilot can never fight the driver by more than that), and
      #  3. fault-bail (drop REQ the instant the EPS trips to nibble 11 so a transient never latches).
      # Everything else (low-speed gate, tight-turn hand-off, divergence/lane-change yield) is
      # removed for now; re-add one at a time with a drive between each to find what caused the bark.
      faulting = CS.eps_state == EPS_STATE_LATCHED_FAULT
      steer_req = CC.latActive and not faulting

      # 2 Hz 1-pole low-pass on the model's desired angle, filtered against its OWN state (not the
      # rate-limited output). Filtering against the output is fine only when the rate limiter never
      # binds; at our EPS-safe rate it does bind, and feeding the rate-limited value back into the
      # filter creates a feedback oscillation - the bark. Decoupling the filter from that feedback,
      # plus the actuator-delay anticipation so the rate-limited command arrives on time, is what
      # smooths the stronger turns. Snap to the wheel while not actuating so re-engage is clean.
      if steer_req:
        alpha_lp = math.exp(-2.0 * math.pi * CarControllerParams.STEER_LOWPASS_HZ * STEER_DT)
        self.angle_cmd_filtered = alpha_lp * self.angle_cmd_filtered + (1.0 - alpha_lp) * actuators.steeringAngleDeg
      else:
        self.angle_cmd_filtered = CS.out.steeringAngleDeg

      apply_angle = apply_std_steer_angle_limits(self.angle_cmd_filtered, self.apply_angle_last,
                                                 CS.out.vEgoRaw, CS.out.steeringAngleDeg,
                                                 CC.latActive, CarControllerParams.ANGLE_LIMITS)
      if steer_req:
        apply_angle = float(np.clip(apply_angle, CS.out.steeringAngleDeg - CarControllerParams.MAX_ANGLE_ERROR,
                                    CS.out.steeringAngleDeg + CarControllerParams.MAX_ANGLE_ERROR))
      else:
        apply_angle = CS.out.steeringAngleDeg
      self.apply_angle_last = apply_angle

      lkas_state = bydcan.LKAS_STATE_ACTIVE if steer_req else bydcan.LKAS_STATE_IDLE
      can_sends.append(bydcan.create_steering_control(self.packer, self.apply_angle_last, steer_req))
      can_sends.append(bydcan.create_lkas_hud(self.packer, lkas_state, steer_req,
                                              CS.lkas_hud, CC.hudControl))

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
