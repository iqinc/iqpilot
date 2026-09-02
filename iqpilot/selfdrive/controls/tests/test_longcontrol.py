from types import SimpleNamespace

from iqpilot.cereal import custom
from iqpilot.selfdrive.controls.lib.longcontrol import LongControl, LongCtrlState, long_control_state_trans


class TestLongControlStateTransition:

  def test_stay_stopped(self):
    CP_IQ = custom.IQCarParams.new_message()
    active = True
    current_state = LongCtrlState.stopping
    next_state = long_control_state_trans(CP_IQ, active, current_state,
                             should_stop=True, brake_pressed=False, cruise_standstill=False)
    assert next_state == LongCtrlState.stopping
    next_state = long_control_state_trans(CP_IQ, active, current_state,
                             should_stop=False, brake_pressed=True, cruise_standstill=False)
    assert next_state == LongCtrlState.stopping
    next_state = long_control_state_trans(CP_IQ, active, current_state,
                             should_stop=False, brake_pressed=False, cruise_standstill=True)
    assert next_state == LongCtrlState.stopping
    next_state = long_control_state_trans(CP_IQ, active, current_state,
                             should_stop=False, brake_pressed=False, cruise_standstill=False)
    assert next_state == LongCtrlState.pid
    active = False
    next_state = long_control_state_trans(CP_IQ, active, current_state,
                             should_stop=False, brake_pressed=False, cruise_standstill=False)
    assert next_state == LongCtrlState.off

def test_engage():
  CP_IQ = custom.IQCarParams.new_message()
  active = True
  current_state = LongCtrlState.off
  next_state = long_control_state_trans(CP_IQ, active, current_state,
                             should_stop=True, brake_pressed=False, cruise_standstill=False)
  assert next_state == LongCtrlState.stopping
  next_state = long_control_state_trans(CP_IQ, active, current_state,
                             should_stop=False, brake_pressed=True, cruise_standstill=False)
  assert next_state == LongCtrlState.stopping
  next_state = long_control_state_trans(CP_IQ, active, current_state,
                             should_stop=False, brake_pressed=False, cruise_standstill=True)
  assert next_state == LongCtrlState.stopping
  next_state = long_control_state_trans(CP_IQ, active, current_state,
                             should_stop=False, brake_pressed=False, cruise_standstill=False)
  assert next_state == LongCtrlState.pid


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
