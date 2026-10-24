from types import SimpleNamespace

import pytest

from iqpilot.cereal import car, log
from iqpilot.common.realtime import DT_CTRL
from iqpilot.selfdrive.selfdrived.events import Events, ET
from iqpilot.selfdrive.selfdrived.helpers import CRITICAL_SERVICES, CRITICAL_SERVICE_TIMEOUT, unresponsive_critical_services
from iqpilot.selfdrive.selfdrived.state import StateMachine, State, SOFT_DISABLE_TIME

EventName = log.OnroadEvent.EventName


class FakeSM:
  def __init__(self, not_running=(), ignore_alive=()):
    self.frame = 0
    self.recv_frame = dict.fromkeys(CRITICAL_SERVICES, 0)
    self.ignore_alive = list(ignore_alive)
    processes = [SimpleNamespace(name=n, running=False, shouldBeRunning=True) for n in not_running]
    self._data = {'managerState': SimpleNamespace(processes=processes)}

  def __getitem__(self, s):
    return self._data[s]

  def step(self, silent=()):
    self.frame += 1
    for s in self.recv_frame:
      if s not in silent:
        self.recv_frame[s] = self.frame


def run_gap(service, gap_s):
  # 100Hz selfdrived loop with one critical service silent for gap_s
  sm = FakeSM()
  for _ in range(100):
    sm.step()
  flagged = []
  for _ in range(int(round(gap_s / DT_CTRL))):
    sm.step(silent=(service,))
    flagged.append(unresponsive_critical_services(sm))
  sm.step()
  return flagged, unresponsive_critical_services(sm)


# planner/radar stalls recorded on 0000000d--ecb4786fa1, which each raised a takeover
@pytest.mark.parametrize('gap_s', (0.506, 0.695, 0.983, 1.167))
@pytest.mark.parametrize('service', ('radarState', 'longitudinalPlan'))
def test_brief_stalls_stay_silent(service, gap_s):
  flagged, after = run_gap(service, gap_s)
  assert not any(flagged) and not after


def test_dead_model_output_is_flagged_and_clears_on_recovery():
  # 14.5 s model outage on 00000007--f9364b6c2f while iqmodeld restarted
  flagged, after = run_gap('modelV2', 14.454)
  first = next(i for i, f in enumerate(flagged) if f)
  assert first * DT_CTRL == pytest.approx(CRITICAL_SERVICE_TIMEOUT, abs=2 * DT_CTRL)
  assert all(f == ['modelV2'] for f in flagged[first:])
  assert after == []


def test_ignored_and_non_critical_services_are_not_flagged():
  sm = FakeSM(ignore_alive=['radarState'])
  sm.recv_frame['iqPlan'] = 0
  for _ in range(int(5 / DT_CTRL)):
    sm.step(silent=('radarState', 'iqPlan'))
  assert unresponsive_critical_services(sm) == []


@pytest.mark.parametrize('event', (EventName.commIssue, EventName.commIssueAvgFreq, EventName.selfdrivedLagging))
def test_generic_comm_issues_are_soft_warnings(event):
  events = Events()
  events.add(event)
  msg = events.to_msg()[0]
  assert not (msg.softDisable or msg.immediateDisable or msg.noEntry or msg.warning)
  assert events.create_alerts([ET.SOFT_DISABLE, ET.NO_ENTRY, ET.WARNING], [None, None, FakeSM(), False, 100, None]) == []


def test_unresponsive_model_warns_disengages_and_blocks_reengagement():
  sm = FakeSM()
  for _ in range(int(3 / DT_CTRL)):
    sm.step(silent=('modelV2',))
  events = Events()
  events.add(EventName.processNotRunning)
  machine = StateMachine()
  machine.state = State.enabled
  machine.update(events)
  assert machine.state == State.softDisabling
  alerts = events.create_alerts(machine.current_alert_types, [None, None, sm, False, machine.soft_disable_timer, None])
  assert any(a.alert_text_2 == "Driving Model Not Responding" and a.alert_size == log.SelfdriveState.AlertSize.full and
             a.audible_alert != car.CarControl.HUDControl.AudibleAlert.none for a in alerts)

  for _ in range(int(SOFT_DISABLE_TIME / DT_CTRL) + 1):
    machine.update(events)
  assert machine.state == State.disabled
  events.add(EventName.buttonEnable)
  machine.update(events)
  assert machine.state == State.disabled
  alerts = events.create_alerts(machine.current_alert_types, [None, None, sm, False, 0, None])
  assert any("Driving Model Not Responding" in (a.alert_text_1, a.alert_text_2) for a in alerts)


def test_dead_process_keeps_process_not_running_wording():
  sm = FakeSM(not_running=['plannerd'])
  for _ in range(10):
    sm.step()
  events = Events()
  events.add(EventName.processNotRunning)
  soft, = events.create_alerts([ET.SOFT_DISABLE], [None, None, sm, False, 100, None])
  entry, = events.create_alerts([ET.NO_ENTRY], [None, None, sm, False, 100, None])
  assert soft.alert_text_2 == "Process Not Running"
  assert "plannerd" in (entry.alert_text_1, entry.alert_text_2)
