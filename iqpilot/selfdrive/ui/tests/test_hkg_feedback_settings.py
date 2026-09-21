# Copyright (c) 2026 IQ.Lvbs. All rights reserved.
from types import SimpleNamespace

import pytest

from iqpilot.common.params import Params
from iqpilot.cereal import car
from iqpilot.selfdrive.ui.mici.layouts.settings import steering, vehicle


def test_feedback_selection_disables_nnff(monkeypatch):
  params = Params()
  params.put_bool("NeuralNetworkFeedForward", True)
  monkeypatch.setattr(vehicle, "ui_state", SimpleNamespace(params=params))
  vehicle.VehicleLayoutMici._on_hkg_reduced_feedback(None, True)
  assert not params.get_bool("NeuralNetworkFeedForward")


def test_feedback_deselection_preserves_nnff(monkeypatch):
  params = Params()
  params.put_bool("NeuralNetworkFeedForward", True)
  monkeypatch.setattr(vehicle, "ui_state", SimpleNamespace(params=params))
  vehicle.VehicleLayoutMici._on_hkg_reduced_feedback(None, False)
  assert params.get_bool("NeuralNetworkFeedForward")


@pytest.mark.parametrize("brand,checked,expected", [("hyundai", True, False), ("hyundai", False, True), ("toyota", True, True)])
def test_nnff_selection_excludes_hkg_feedback_only(monkeypatch, brand, checked, expected):
  params = Params()
  params.put_bool("IQHkgReducedTorqueFeedback", True)
  monkeypatch.setattr(steering, "ui_state", SimpleNamespace(params=params, CP=SimpleNamespace(brand=brand)))
  steering.SteeringLayoutMici._on_nnff_toggled(None, checked)
  assert params.get_bool("IQHkgReducedTorqueFeedback") == expected


@pytest.mark.parametrize("brand,angle,tuning,offroad,visible", [
  ("hyundai", False, "torque", True, True),
  ("hyundai", False, "torque", False, True),
  ("hyundai", True, "torque", True, False),
  ("hyundai", False, "pid", True, False),
  ("toyota", False, "torque", True, False),
])
def test_visibility_and_offroad_gate(monkeypatch, mocker, brand, angle, tuning, offroad, visible):
  layout = mocker.Mock()
  control = layout._hkg_reduced_feedback
  layout._all_brand_widgets = [control]
  layout._brand_widgets = {"hyundai": [control]}
  layout._get_current_brand.return_value = brand
  cp = SimpleNamespace(steerControlType=car.CarParams.SteerControlType.angle if angle else car.CarParams.SteerControlType.torque,
                       lateralTuning=SimpleNamespace(which=lambda: tuning))
  monkeypatch.setattr(vehicle, "ui_state", SimpleNamespace(CP=cp, is_offroad=lambda: offroad))
  vehicle.VehicleLayoutMici._refresh(layout)
  control.set_visible.assert_called_once_with(visible)
  control.set_enabled.assert_called_once_with(offroad)
