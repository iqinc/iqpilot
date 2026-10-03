"""
Copyright © IQ.Lvbs, apart of Project Teal Lvbs, All Rights Reserved, licensed under https://konn3kt.com/tos
"""
from iqpilot.cereal import log
from iqpilot.selfdrive.controls.lib.desire_helper import DesireHelper, LaneChangeState, LANE_CHANGE_CANCEL_TORQUE_FRAMES
from iqpilot.selfdrive.controls.lib.helpers.lane_change import AutoLaneChangeMode

LaneChangeDirection = log.LaneChangeDirection


class CarState:
  def __init__(self, vEgo=15.0, leftBlinker=False, rightBlinker=False, steeringPressed=False, steeringTorque=0):
    self.vEgo = vEgo
    self.leftBlinker = leftBlinker
    self.rightBlinker = rightBlinker
    self.leftBlindspot = False
    self.rightBlindspot = False
    self.steeringPressed = steeringPressed
    self.steeringTorque = steeringTorque
    self.brakePressed = False
    self.yawRate = 0.0


def helper():
  dh = DesireHelper()
  dh.alc.lane_change_set_timer = AutoLaneChangeMode.NUDGE
  dh.nav_exit._read_enabled = lambda: False
  return dh


def run(dh, cs, frames, lane_change_prob=1.0):
  for _ in range(frames):
    dh.update(cs, True, lane_change_prob)
  return dh


def started_right():
  dh = helper()
  run(dh, CarState(), 2)
  run(dh, CarState(rightBlinker=True, steeringPressed=True, steeringTorque=-240), 3)
  assert dh.lane_change_state == LaneChangeState.laneChangeStarting
  assert dh.lane_change_direction == LaneChangeDirection.right
  return dh


def test_blinker_released_mid_change_keeps_the_change():
  dh = run(started_right(), CarState(), 10)
  assert dh.lane_change_state == LaneChangeState.laneChangeStarting
  assert dh.desire == log.Desire.laneChangeRight


def test_opposite_blinker_cancels_immediately():
  dh = run(started_right(), CarState(leftBlinker=True), 1)
  assert dh.lane_change_state == LaneChangeState.off
  assert dh.lane_change_direction == LaneChangeDirection.none
  assert dh.desire == log.Desire.none


def test_sustained_opposite_torque_cancels():
  dh = run(started_right(), CarState(steeringPressed=True, steeringTorque=150), LANE_CHANGE_CANCEL_TORQUE_FRAMES - 1)
  assert dh.lane_change_state == LaneChangeState.laneChangeStarting
  run(dh, CarState(steeringPressed=True, steeringTorque=150), 1)
  assert dh.lane_change_state == LaneChangeState.off
  assert dh.desire == log.Desire.none


def test_brief_opposite_touch_does_not_cancel():
  dh = run(started_right(), CarState(steeringPressed=True, steeringTorque=150), LANE_CHANGE_CANCEL_TORQUE_FRAMES - 1)
  run(dh, CarState(), 5)
  assert dh.lane_change_state == LaneChangeState.laneChangeStarting


def test_same_direction_torque_does_not_cancel():
  dh = run(started_right(), CarState(steeringPressed=True, steeringTorque=-200), 20)
  assert dh.lane_change_state == LaneChangeState.laneChangeStarting


def test_cancel_during_finishing():
  dh = run(started_right(), CarState(rightBlinker=True), 12, lane_change_prob=0.0)
  assert dh.lane_change_state == LaneChangeState.laneChangeFinishing
  run(dh, CarState(leftBlinker=True), 1)
  assert dh.lane_change_state == LaneChangeState.off


def test_left_signal_after_torque_cancel_starts_a_left_change():
  dh = run(started_right(), CarState(), 3)
  run(dh, CarState(steeringPressed=True, steeringTorque=160), LANE_CHANGE_CANCEL_TORQUE_FRAMES)
  assert dh.lane_change_state == LaneChangeState.off
  run(dh, CarState(leftBlinker=True, steeringPressed=True, steeringTorque=160), 3)
  assert dh.lane_change_direction == LaneChangeDirection.left
  assert dh.lane_change_state == LaneChangeState.laneChangeStarting
