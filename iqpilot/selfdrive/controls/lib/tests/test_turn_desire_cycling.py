from types import SimpleNamespace

import numpy as np
import pytest

from iqpilot.cereal import custom, log
from iqpilot.selfdrive.controls.lib.desire_helper import (
  DesireHelper,
  TURN_DESIRE_MAX_SECONDS,
  TURN_DESIRE_PULSE_CYCLE_FRAMES,
  TURN_DESIRE_PULSE_HOLD_FRAMES,
  TURN_RECOIL_HEADING_RAD,
)
from iqpilot.common.constants import CV
from iqpilot.common.realtime import DT_MDL


TurnDirection = custom.IQTurnSignalDirection
MODEL_HISTORY_FRAMES = 100


def helper(v_ego=0.0, yaw_rate=0.0, model_yaw_rate=None):
  result = DesireHelper.__new__(DesireHelper)
  result._last_carstate = SimpleNamespace(vEgo=v_ego, yawRate=yaw_rate)
  result._last_modeldata = (None if model_yaw_rate is None
                            else SimpleNamespace(orientationRate=SimpleNamespace(z=[model_yaw_rate])))
  result.turn_desire_frames = 0
  result.turn_desire_cycle_input = log.Desire.none
  result.turn_heading_rad = 0.0
  result.turn_desire_seconds = 0.0
  result.turn_recoiled = False
  result.nav_turn_direction = TurnDirection.none
  result.lane_turn_direction = TurnDirection.none
  result.lane_change_direction = log.LaneChangeDirection.none
  result.lane_change_state = log.LaneChangeState.off
  result.desire = log.Desire.none
  return result


def visible_pulses(outputs):
  previous = log.Desire.none
  rising = []
  for output in outputs:
    rising.append(output != log.Desire.none and previous != output)
    previous = output
  rising = np.array(rising)
  return np.array([rising[max(0, i - MODEL_HISTORY_FRAMES + 1):i + 1].sum() for i in range(len(rising))])


@pytest.mark.parametrize("source", ["manual", "nav"])
@pytest.mark.parametrize("v_ego,yaw_rate", [(0.0, 0.0), (6.0, 0.0), (4.0, 0.02)])
def test_turn_desire_keeps_exactly_one_pulse_in_model_history(source, v_ego, yaw_rate):
  h = helper(v_ego, yaw_rate)
  if source == "manual":
    h.lane_turn_direction = TurnDirection.turnLeft
  else:
    h.nav_turn_direction = TurnDirection.turnLeft

  outputs = []
  for _ in range(4 * TURN_DESIRE_PULSE_CYCLE_FRAMES):
    h._pick_desire_output()
    outputs.append(h.desire)

  gaps = [i for i, output in enumerate(outputs) if output == log.Desire.none]
  assert gaps[0] == TURN_DESIRE_PULSE_HOLD_FRAMES
  assert np.diff(gaps).tolist() == [TURN_DESIRE_PULSE_CYCLE_FRAMES] * (len(gaps) - 1)
  assert set(visible_pulses(outputs)[MODEL_HISTORY_FRAMES:].tolist()) == {1}


def test_turn_desire_recoils_once_the_turn_has_been_driven():
  yaw_rate = 0.4
  h = helper(v_ego=6.0, yaw_rate=yaw_rate)
  h.lane_turn_direction = TurnDirection.turnLeft

  frames_to_recoil = int(TURN_RECOIL_HEADING_RAD / (yaw_rate * DT_MDL))
  outputs = []
  for _ in range(frames_to_recoil + 4 * TURN_DESIRE_PULSE_CYCLE_FRAMES):
    h._pick_desire_output()
    outputs.append(h.desire)

  assert log.Desire.turnLeft in outputs[:TURN_DESIRE_PULSE_HOLD_FRAMES]
  assert h.turn_recoiled
  assert all(output == log.Desire.none for output in outputs[frames_to_recoil + 1:])


def test_recoil_holds_through_a_blinker_that_stays_on():
  h = helper(v_ego=6.0, yaw_rate=0.6)
  h.lane_turn_direction = TurnDirection.turnRight
  for _ in range(TURN_DESIRE_PULSE_CYCLE_FRAMES):
    h._pick_desire_output()
  assert h.turn_recoiled

  h._last_carstate = SimpleNamespace(vEgo=6.0, yawRate=0.0)
  outputs = []
  for _ in range(3 * TURN_DESIRE_PULSE_CYCLE_FRAMES):
    h._pick_desire_output()
    outputs.append(h.desire)
  assert all(output == log.Desire.none for output in outputs)


def test_recoil_clears_when_the_turn_request_drops():
  h = helper(v_ego=6.0, yaw_rate=0.6)
  h.lane_turn_direction = TurnDirection.turnRight
  for _ in range(TURN_DESIRE_PULSE_CYCLE_FRAMES):
    h._pick_desire_output()
  assert h.turn_recoiled

  h.lane_turn_direction = TurnDirection.none
  h._pick_desire_output()
  assert not h.turn_recoiled
  assert h.turn_heading_rad == 0.0

  h.lane_turn_direction = TurnDirection.turnRight
  h._last_carstate = SimpleNamespace(vEgo=6.0, yawRate=0.0)
  h._pick_desire_output()
  assert h.desire == log.Desire.turnRight


def test_turn_desire_gives_up_when_the_blinker_outlives_the_maneuver():
  h = helper(v_ego=6.0, yaw_rate=0.0)
  h.lane_turn_direction = TurnDirection.turnLeft
  for _ in range(int(TURN_DESIRE_MAX_SECONDS / DT_MDL) + 1):
    h._pick_desire_output()
  assert h.turn_recoiled
  assert h.desire == log.Desire.none


def test_new_turn_direction_restarts_pulse_immediately():
  h = helper()
  for _ in range(TURN_DESIRE_PULSE_HOLD_FRAMES // 2):
    assert h._pulse_turn_desire(log.Desire.turnLeft) == log.Desire.turnLeft
  assert h._pulse_turn_desire(log.Desire.turnRight) == log.Desire.turnRight
  assert h.turn_desire_cycle_input == log.Desire.turnRight
  assert h.turn_desire_frames == 1


def test_lane_change_desires_pass_through_and_reset_pulse():
  h = helper()
  h._pulse_turn_desire(log.Desire.turnLeft)
  assert h._pulse_turn_desire(log.Desire.laneChangeLeft) == log.Desire.laneChangeLeft
  assert h.turn_desire_frames == 0
  assert h.turn_desire_cycle_input == log.Desire.none
  assert not h.turn_recoiled


def test_turn_speed_gate_follows_the_param_past_the_old_ceiling(monkeypatch):
  from iqpilot.selfdrive.controls.lib.helpers import lane_turn

  class FakeParams:
    def get(self, *_a, **_k):
      return "30.0"

    def get_bool(self, *_a, **_k):
      return True

  monkeypatch.setattr(lane_turn, "Params", FakeParams)
  planner = lane_turn.TurnSignalPlanner(None)
  assert planner.speed_gate == pytest.approx(30.0 * CV.MPH_TO_MS)

  planner.sample(blink_l=True, speed_mps=28.0 * CV.MPH_TO_MS)
  assert planner.output() == TurnDirection.turnLeft

  planner.sample(blink_l=True, speed_mps=31.0 * CV.MPH_TO_MS)
  assert planner.output() == TurnDirection.none


def test_turn_speed_gate_is_capped_at_the_ceiling(monkeypatch):
  from iqpilot.selfdrive.controls.lib.helpers import lane_turn

  class FakeParams:
    def get(self, *_a, **_k):
      return "90.0"

    def get_bool(self, *_a, **_k):
      return True

  monkeypatch.setattr(lane_turn, "Params", FakeParams)
  planner = lane_turn.TurnSignalPlanner(None)
  assert planner.speed_gate == pytest.approx(lane_turn.TURN_SPEED_CEILING_MPS)


def test_the_turn_intent_stays_alive_through_the_maneuver():
  h = helper(v_ego=6.0, yaw_rate=0.15)
  h.lane_turn_direction = TurnDirection.turnLeft
  outputs = []
  for _ in range(int(TURN_RECOIL_HEADING_RAD / (0.15 * DT_MDL)) - 1):
    h._pick_desire_output()
    outputs.append(h.desire)

  assert not h.turn_recoiled
  assert set(visible_pulses(outputs)[MODEL_HISTORY_FRAMES:].tolist()) == {1}


def test_recoil_uses_the_model_pose_when_the_car_reports_no_yaw_rate():
  # every frame of 00000007--1984111bdd carries yawRate 0.0; the port never fills it in
  h = helper(v_ego=6.0, yaw_rate=0.0, model_yaw_rate=0.5)
  h.lane_turn_direction = TurnDirection.turnLeft
  for _ in range(int(TURN_RECOIL_HEADING_RAD / (0.5 * DT_MDL)) + 2):
    h._pick_desire_output()
  assert h.turn_recoiled
  assert h.desire == log.Desire.none


def test_recoil_falls_back_to_carstate_yaw_rate():
  h = helper(v_ego=6.0, yaw_rate=0.5)
  h.lane_turn_direction = TurnDirection.turnLeft
  for _ in range(int(TURN_RECOIL_HEADING_RAD / (0.5 * DT_MDL)) + 2):
    h._pick_desire_output()
  assert h.turn_recoiled
