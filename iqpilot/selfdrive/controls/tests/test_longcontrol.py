from types import SimpleNamespace

import pytest

from iqpilot.selfdrive.controls.lib.longcontrol import LongControl, LongCtrlState


@pytest.mark.parametrize("current_state", [LongCtrlState.off, LongCtrlState.stopping, LongCtrlState.pid])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("stop_requested,braking,cruise_hold,interceptor,expected_at_rest", [
  (False, False, False, False, LongCtrlState.pid),
  (False, False, False, True, LongCtrlState.pid),
  (False, False, True, False, LongCtrlState.stopping),
  (False, False, True, True, LongCtrlState.pid),
  (False, True, False, False, LongCtrlState.stopping),
  (False, True, False, True, LongCtrlState.stopping),
  (False, True, True, False, LongCtrlState.stopping),
  (False, True, True, True, LongCtrlState.stopping),
  (True, False, False, False, LongCtrlState.stopping),
  (True, False, False, True, LongCtrlState.stopping),
  (True, False, True, False, LongCtrlState.stopping),
  (True, False, True, True, LongCtrlState.stopping),
  (True, True, False, False, LongCtrlState.stopping),
  (True, True, False, True, LongCtrlState.stopping),
  (True, True, True, False, LongCtrlState.stopping),
  (True, True, True, True, LongCtrlState.stopping),
])
def test_stop_and_release_policy(current_state, active, stop_requested, braking, cruise_hold, interceptor, expected_at_rest):
  control = object.__new__(LongControl)
  control.CP_IQ = SimpleNamespace(enableGasInterceptor=interceptor)
  control.long_control_state = current_state
  control.smooth = SimpleNamespace(enabled=False)
  car_state = SimpleNamespace(brakePressed=braking, cruiseState=SimpleNamespace(standstill=cruise_hold))

  control._update_state(active, car_state, stop_requested)

  expected = expected_at_rest
  if not active:
    expected = LongCtrlState.off
  elif current_state == LongCtrlState.pid and not stop_requested:
    expected = LongCtrlState.pid
  assert control.long_control_state == expected


@pytest.mark.parametrize("current_state", [LongCtrlState.off, LongCtrlState.stopping, LongCtrlState.pid])
@pytest.mark.parametrize("settled", [False, True])
def test_smooth_stop_enters_hold_only_after_settling(current_state, settled):
  requests = []
  control = object.__new__(LongControl)
  control.CP_IQ = SimpleNamespace(enableGasInterceptor=False)
  control.long_control_state = current_state
  control.smooth = SimpleNamespace(enabled=True, want_hold=lambda *args: requests.append(args) or settled)
  car_state = SimpleNamespace(vEgo=0.4, standstill=False, brakePressed=False, cruiseState=SimpleNamespace(standstill=False))

  control._update_state(True, car_state, True)

  if current_state == LongCtrlState.stopping:
    assert requests == []
    assert control.long_control_state == LongCtrlState.stopping
  else:
    assert requests == [(True, 0.4, False)]
    assert control.long_control_state == (LongCtrlState.stopping if settled else LongCtrlState.pid)


def test_gas_override_preserves_negative_accel_command():
  pid_calls = []
  control = object.__new__(LongControl)
  control.CP = SimpleNamespace(stopAccel=-0.55)
  control.CP_IQ = SimpleNamespace(enableGasInterceptor=False)
  control.long_control_state = LongCtrlState.pid
  control.pid = SimpleNamespace(
    update=lambda error, **kwargs: pid_calls.append((error, kwargs)) or -0.5,
    reset=lambda: None,
  )
  control.last_output_accel = -0.4
  control.stopping_decel_rate = 1.0
  control.smooth = SimpleNamespace(enabled=False, update=lambda: None, reset=lambda: None)
  car_state = SimpleNamespace(
    vEgo=15.0,
    aEgo=0.0,
    brakePressed=False,
    standstill=False,
    cruiseState=SimpleNamespace(standstill=False),
  )

  output = control.update(True, car_state, -0.5, False, (-3.5, 2.0), gas_override=True)

  assert output == -0.5
  assert pid_calls == [(-0.5, {"speed": 15.0, "feedforward": -0.5, "freeze_integrator": True})]
