# Copyright © IQ.Lvbs, apart of Project Teal Lvbs, All Rights Reserved, licensed under https://konn3kt.com/tos/
import re
from pathlib import Path

import pytest

from iqpilot.common.realtime import DT_MDL
from iqpilot.selfdrive.controls.lib.accel_boost import ACCEL_BOOST_MAX, ACCEL_BOOST_MIN_SPEED, ACCEL_BOOST_RATE, AccelBoost

FAST = ACCEL_BOOST_MIN_SPEED + 5.0
SLOW = ACCEL_BOOST_MIN_SPEED - 1.0


def run(boost, seconds, engaged=True, v_ego=FAST, gas=True):
  for _ in range(round(seconds / DT_MDL)):
    boost.update(engaged, v_ego, gas)
  return boost.value


def test_param_is_off_by_default():
  keys = (Path(__file__).parents[3] / "common" / "params_keys.h").read_text()
  assert re.search(r'\{"IQGasOverrideBoost", \{PERSISTENT, BOOL, "0"\}\}', keys)


def test_disabled_never_boosts():
  assert run(AccelBoost(False, DT_MDL), 30.0) == 0.0


def test_gas_ramps_to_one_and_holds():
  boost = AccelBoost(True, DT_MDL)
  assert run(boost, 1.0) == pytest.approx(ACCEL_BOOST_RATE, abs=1e-6)
  assert ACCEL_BOOST_MAX == 1.0
  assert run(boost, 15.0) == pytest.approx(1.0)
  assert run(boost, 5.0, gas=False) == pytest.approx(1.0)


def test_bleeds_off_below_ten_mph_and_resets_on_disengage():
  boost = AccelBoost(True, DT_MDL)
  run(boost, 5.0)
  assert run(boost, 2.0, v_ego=SLOW) == pytest.approx(0.3, abs=1e-6)
  assert run(boost, 5.0, v_ego=SLOW) == 0.0
  run(boost, 5.0)
  assert run(boost, DT_MDL, engaged=False) == 0.0


def test_boost_bar_only_replaces_the_confidence_ball_with_the_param_on(monkeypatch):
  from types import SimpleNamespace

  from iqpilot.selfdrive.ui.mici.onroad import augmented_road_view
  view = SimpleNamespace(_boost_bar="bar", _confidence_ball="ball")
  for enabled, expected in ((False, "ball"), (True, "bar")):
    monkeypatch.setattr(augmented_road_view.ui_state, "gas_override_boost", enabled, raising=False)
    assert augmented_road_view.AugmentedRoadView._side_panel(view) == expected
