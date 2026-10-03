"""
Copyright © IQ.Lvbs, apart of Project Teal Lvbs, All Rights Reserved, licensed under https://konn3kt.com/tos/
"""

from __future__ import annotations

import math

from iqpilot.cereal import car, custom, log

from iqpilot.common.constants import CV
from iqpilot.common.params import Params
from iqpilot.common.realtime import DT_MDL
from iqpilot.selfdrive.controls.lib.helpers.lane_change import (
  IQLaneSwapController,
  AutoLaneChangeMode,
  NavExitLaneChangeController,
)
from iqpilot.selfdrive.controls.lib.helpers.lateral_edge_guard import LateralEdgeGuard
from iqpilot.selfdrive.controls.lib.helpers.lane_turn import IQNavTurnController

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection
TurnDirection = custom.IQTurnSignalDirection
LateralEdgeBlock = custom.IQLateralEdgeBlock
NavManeuverPhase = custom.IQNavState.ManeuverPhase

LANE_CHANGE_SPEED_MIN = 20 * CV.MPH_TO_MS
LANE_CHANGE_TIME_MAX = 10.0
LANE_CHANGE_CANCEL_TORQUE_FRAMES = round(0.25 / DT_MDL)
# only the rising edge reaches the model and it stays visible for the 5 s the history spans, so one
# edge per 5 s keeps the turn alive through the maneuver without stacking edges that cut the corner
TURN_DESIRE_PULSE_CYCLE_FRAMES = round(5.0 / DT_MDL)
TURN_DESIRE_PULSE_HOLD_FRAMES = TURN_DESIRE_PULSE_CYCLE_FRAMES - 1
TURN_RECOIL_HEADING_RAD = math.radians(55.0)
TURN_DESIRE_MAX_SECONDS = 25.0
TURN_DESIRE_MOVING_SPEED = 0.5

_LANE_CHANGE_DESIRES = {
  (LaneChangeDirection.none, LaneChangeState.off): log.Desire.none,
  (LaneChangeDirection.none, LaneChangeState.preLaneChange): log.Desire.none,
  (LaneChangeDirection.none, LaneChangeState.laneChangeStarting): log.Desire.none,
  (LaneChangeDirection.none, LaneChangeState.laneChangeFinishing): log.Desire.none,
  (LaneChangeDirection.left, LaneChangeState.off): log.Desire.none,
  (LaneChangeDirection.left, LaneChangeState.preLaneChange): log.Desire.none,
  (LaneChangeDirection.left, LaneChangeState.laneChangeStarting): log.Desire.laneChangeLeft,
  (LaneChangeDirection.left, LaneChangeState.laneChangeFinishing): log.Desire.laneChangeLeft,
  (LaneChangeDirection.right, LaneChangeState.off): log.Desire.none,
  (LaneChangeDirection.right, LaneChangeState.preLaneChange): log.Desire.none,
  (LaneChangeDirection.right, LaneChangeState.laneChangeStarting): log.Desire.laneChangeRight,
  (LaneChangeDirection.right, LaneChangeState.laneChangeFinishing): log.Desire.laneChangeRight,
}

_TURN_DESIRES = {
  TurnDirection.none: log.Desire.none,
  TurnDirection.turnLeft: log.Desire.turnLeft,
  TurnDirection.turnRight: log.Desire.turnRight,
}

_PULSED_TURN_DESIRES = {
  log.Desire.turnLeft,
  log.Desire.turnRight,
}


def turn_desire(turn_direction) -> log.Desire:
  return _TURN_DESIRES[getattr(turn_direction, "raw", turn_direction)]


def _direction_from_blinkers(carstate) -> int:
  if carstate.leftBlinker:
    return LaneChangeDirection.left
  if carstate.rightBlinker:
    return LaneChangeDirection.right
  return LaneChangeDirection.none


def _steering_nudge_matches(carstate, direction: int) -> bool:
  if not carstate.steeringPressed:
    return False
  return (
    (direction == LaneChangeDirection.left and carstate.steeringTorque > 0) or
    (direction == LaneChangeDirection.right and carstate.steeringTorque < 0)
  )


def _opposite_direction(direction: int) -> int:
  if direction == LaneChangeDirection.left:
    return LaneChangeDirection.right
  if direction == LaneChangeDirection.right:
    return LaneChangeDirection.left
  return LaneChangeDirection.none


def _blinker_matches(carstate, direction: int) -> bool:
  return (
    (direction == LaneChangeDirection.left and carstate.leftBlinker and not carstate.rightBlinker) or
    (direction == LaneChangeDirection.right and carstate.rightBlinker and not carstate.leftBlinker)
  )


def _blindspot_matches(carstate, direction: int) -> bool:
  return (
    (direction == LaneChangeDirection.left and carstate.leftBlindspot) or
    (direction == LaneChangeDirection.right and carstate.rightBlindspot)
  )


def _read_enable_bsm() -> bool:
  try:
    with car.CarParams.from_bytes(Params().get("CarParams")) as cp:
      return bool(cp.enableBsm)
  except Exception:
    return False


class DesireHelper:
  def __init__(self):
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none
    self.lane_change_timer = 0.0
    self.lane_change_ll_prob = 1.0
    self.lane_change_counter_torque_frames = 0
    self.prev_one_blinker = False
    self.prev_nav_exit_active = False
    self.nav_exit_rearmed = False
    self.desire = log.Desire.none

    self.alc = IQLaneSwapController(self)
    self.lane_turn_controller = IQNavTurnController(self)
    self.nav_exit = NavExitLaneChangeController(_read_enable_bsm())
    self.lateral_edge_guard = LateralEdgeGuard()
    self.lateral_edge_block = LateralEdgeBlock.none
    self.lane_turn_direction = TurnDirection.none
    self.nav_turn_direction = TurnDirection.none
    self.turn_desire_frames = 0
    self.turn_desire_cycle_input = log.Desire.none
    self.turn_heading_rad = 0.0
    self.turn_desire_seconds = 0.0
    self.turn_recoiled = False

  @staticmethod
  def get_lane_change_direction(carstate):
    return _direction_from_blinkers(carstate)

  @staticmethod
  def _nav_turn_desire(nav_state):
    if nav_state is None or not getattr(nav_state, "active", False):
      return TurnDirection.none
    if getattr(nav_state, "maneuverPhase", NavManeuverPhase.none) != NavManeuverPhase.turnActive:
      return TurnDirection.none
    if not getattr(nav_state, "shouldSendTurnDesire", False):
      return TurnDirection.none
    return getattr(nav_state, "turnDesireDirection", TurnDirection.none)

  def _clear_lane_change(self) -> None:
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none

  def _refresh_turn_overrides(self, carstate, nav_state) -> bool:
    speed_mps = carstate.vEgo
    self.lane_turn_controller.update_params()
    self.lane_turn_controller.update_lane_turn(
      blindspot_left=carstate.leftBlindspot,
      blindspot_right=carstate.rightBlindspot,
      left_blinker=carstate.leftBlinker,
      right_blinker=carstate.rightBlinker,
      v_ego=speed_mps,
    )
    self.lane_turn_direction = self.lane_turn_controller.get_turn_direction()
    self.nav_turn_direction = self._nav_turn_desire(nav_state)

    self.nav_exit.update_params()
    self.nav_exit.update(nav_state, carstate)
    return bool(self.nav_exit.active)

  def _reset_required(self, lateral_active: bool, nav_exit_active: bool) -> bool:
    timed_out = self.lane_change_timer > LANE_CHANGE_TIME_MAX
    feature_disabled = self.alc.lane_change_set_timer == AutoLaneChangeMode.OFF and not nav_exit_active
    return (not lateral_active) or timed_out or feature_disabled

  def _begin_from_idle(self, one_blinker: bool, nav_exit_active: bool, below_speed: bool) -> None:
    if below_speed:
      return
    if one_blinker and not self.prev_one_blinker:
      self.lane_change_state = LaneChangeState.preLaneChange
      self.lane_change_direction = _direction_from_blinkers(self._last_carstate)
      self.lane_change_ll_prob = 1.0
      return
    if nav_exit_active and not self.prev_nav_exit_active:
      self.lane_change_state = LaneChangeState.preLaneChange
      self.lane_change_direction = self.nav_exit.direction
      self.lane_change_ll_prob = 1.0

  def _refresh_requested_direction(self, one_blinker: bool, nav_exit_active: bool) -> None:
    if one_blinker:
      self.lane_change_direction = _direction_from_blinkers(self._last_carstate)
    elif nav_exit_active:
      self.lane_change_direction = self.nav_exit.direction

  def _step_pre_lane_change(self, one_blinker: bool, nav_exit_active: bool, below_speed: bool) -> None:
    self._refresh_requested_direction(one_blinker, nav_exit_active)
    blindspot_detected = _blindspot_matches(self._last_carstate, self.lane_change_direction)
    self.lateral_edge_block = self.lateral_edge_guard.block_for_direction(self.lane_change_direction)
    lateral_edge_blocked = self.lateral_edge_block != LateralEdgeBlock.none
    steering_ready = _steering_nudge_matches(self._last_carstate, self.lane_change_direction)
    # an exit can need more than one lane, but only the first move may start without a driver nudge
    nav_auto_start = nav_exit_active and self.nav_exit.auto_allowed and not self.nav_exit_rearmed

    self.alc.update_lane_change(blindspot_detected=blindspot_detected, brake_pressed=self._last_carstate.brakePressed)
    allowed_to_launch = steering_ready or self.alc.auto_lane_change_allowed or nav_auto_start

    if (not (one_blinker or nav_exit_active)) or below_speed:
      self._clear_lane_change()
    elif allowed_to_launch and not blindspot_detected and not lateral_edge_blocked:
      self.lane_change_state = LaneChangeState.laneChangeStarting

  def _step_lane_change_starting(self, lane_change_prob: float) -> None:
    self.lane_change_ll_prob = max(self.lane_change_ll_prob - (2.0 * DT_MDL), 0.0)
    if lane_change_prob < 0.02 and self.lane_change_ll_prob < 0.01:
      self.lane_change_state = LaneChangeState.laneChangeFinishing

  def _step_lane_change_finishing(self, one_blinker: bool, nav_exit_active: bool) -> None:
    self.lane_change_ll_prob = min(self.lane_change_ll_prob + DT_MDL, 1.0)
    if self.lane_change_ll_prob <= 0.99:
      return
    self.lane_change_direction = LaneChangeDirection.none
    if one_blinker:
      self.lane_change_state = LaneChangeState.preLaneChange
    elif nav_exit_active:
      self.lane_change_state = LaneChangeState.preLaneChange
      self.lane_change_direction = self.nav_exit.direction
      self.nav_exit_rearmed = True
    else:
      self.lane_change_state = LaneChangeState.off

  def _driver_cancels_lane_change(self) -> bool:
    if self.lane_change_state not in (LaneChangeState.laneChangeStarting, LaneChangeState.laneChangeFinishing):
      self.lane_change_counter_torque_frames = 0
      return False
    opposite = _opposite_direction(self.lane_change_direction)
    if _steering_nudge_matches(self._last_carstate, opposite):
      self.lane_change_counter_torque_frames += 1
    else:
      self.lane_change_counter_torque_frames = 0
    return (_blinker_matches(self._last_carstate, opposite) or
            self.lane_change_counter_torque_frames >= LANE_CHANGE_CANCEL_TORQUE_FRAMES)

  def _cancel_lane_change(self) -> None:
    self._clear_lane_change()
    self.lane_change_ll_prob = 1.0
    self.lane_change_counter_torque_frames = 0

  def _advance_lane_change_machine(self, one_blinker: bool, nav_exit_active: bool, below_speed: bool, lane_change_prob: float) -> None:
    if self._driver_cancels_lane_change():
      self._cancel_lane_change()
      return
    if self.lane_change_state == LaneChangeState.off:
      self._begin_from_idle(one_blinker, nav_exit_active, below_speed)
      return
    if self.lane_change_state == LaneChangeState.preLaneChange:
      self._step_pre_lane_change(one_blinker, nav_exit_active, below_speed)
      return
    if self.lane_change_state == LaneChangeState.laneChangeStarting:
      self._step_lane_change_starting(lane_change_prob)
      return
    if self.lane_change_state == LaneChangeState.laneChangeFinishing:
      self._step_lane_change_finishing(one_blinker, nav_exit_active)

  def _update_timer(self) -> None:
    if self.lane_change_state in (LaneChangeState.off, LaneChangeState.preLaneChange):
      self.lane_change_timer = 0.0
    else:
      self.lane_change_timer += DT_MDL

  def _clear_turn_desire_pulse(self) -> None:
    self.turn_desire_frames = 0
    self.turn_desire_cycle_input = log.Desire.none
    self.turn_heading_rad = 0.0
    self.turn_desire_seconds = 0.0
    self.turn_recoiled = False

  def _measured_yaw_rate(self) -> float:
    # carState.yawRate is left unpopulated by many ports, so the model pose is the primary source
    rate = getattr(getattr(getattr(self, "_last_modeldata", None), "orientationRate", None), "z", None)
    try:
      if rate is not None and len(rate) and math.isfinite(rate[0]):
        return float(rate[0])
    except (TypeError, IndexError):
      pass
    try:
      return float(getattr(self._last_carstate, "yawRate", 0.0) or 0.0)
    except (TypeError, ValueError):
      return 0.0

  def _turn_recoiled(self, desired_output: log.Desire) -> bool:
    # blinkers keep running well past the apex, so the executed heading change has to end the
    # desire: one that outlives the turn keeps steering the plan into the far side of the road
    if desired_output != self.turn_desire_cycle_input:
      self.turn_heading_rad = 0.0
      self.turn_desire_seconds = 0.0
      self.turn_recoiled = False
    self.turn_heading_rad += abs(self._measured_yaw_rate()) * DT_MDL
    # a red light at the junction must not use up the turn before the car moves
    if float(getattr(self._last_carstate, "vEgo", 0.0) or 0.0) > TURN_DESIRE_MOVING_SPEED:
      self.turn_desire_seconds += DT_MDL
    if self.turn_heading_rad >= TURN_RECOIL_HEADING_RAD or self.turn_desire_seconds >= TURN_DESIRE_MAX_SECONDS:
      self.turn_recoiled = True
    return self.turn_recoiled

  def _pulse_turn_desire(self, desired_output: log.Desire) -> log.Desire:
    if desired_output not in _PULSED_TURN_DESIRES:
      self._clear_turn_desire_pulse()
      return desired_output

    recoiled = self._turn_recoiled(desired_output)

    if desired_output != self.turn_desire_cycle_input:
      self.turn_desire_frames = 0
      self.turn_desire_cycle_input = desired_output

    cycle_frame = self.turn_desire_frames % TURN_DESIRE_PULSE_CYCLE_FRAMES
    self.turn_desire_frames += 1
    if recoiled or cycle_frame >= TURN_DESIRE_PULSE_HOLD_FRAMES:
      return log.Desire.none
    return desired_output

  def _pick_desire_output(self) -> None:
    desired_output = log.Desire.none
    if self.nav_turn_direction != TurnDirection.none:
      desired_output = turn_desire(self.nav_turn_direction)
    elif self.lane_turn_direction != TurnDirection.none:
      desired_output = turn_desire(self.lane_turn_direction)
    else:
      desired_output = _LANE_CHANGE_DESIRES[(self.lane_change_direction, self.lane_change_state)]

    self.desire = self._pulse_turn_desire(desired_output)

  def update(self, carstate, lateral_active, lane_change_prob, nav_state=None, modeldata=None, radar_state=None):
    self._last_carstate = carstate
    self._last_modeldata = modeldata
    self.lateral_edge_guard.update(modeldata, carstate.vEgo, DT_MDL)
    self.lateral_edge_block = LateralEdgeBlock.none
    one_blinker = carstate.leftBlinker != carstate.rightBlinker
    below_speed = carstate.vEgo < LANE_CHANGE_SPEED_MIN
    nav_exit_active = self._refresh_turn_overrides(carstate, nav_state)
    if not nav_exit_active:
      self.nav_exit_rearmed = False

    self.alc.update_params()
    if self._reset_required(lateral_active, nav_exit_active):
      self._clear_lane_change()
    else:
      self._advance_lane_change_machine(one_blinker, nav_exit_active, below_speed, lane_change_prob)

    self._update_timer()
    self.prev_one_blinker = one_blinker and lateral_active
    self.prev_nav_exit_active = nav_exit_active
    self.alc.update_state()
    self._pick_desire_output()
