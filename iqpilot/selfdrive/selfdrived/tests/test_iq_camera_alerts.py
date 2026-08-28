from types import SimpleNamespace

from iqpilot.cereal import car, custom
from iqpilot.selfdrive.selfdrived import iq_events


def alert(camera_type, *, report_id="", chime=False):
  nav = SimpleNamespace(
    cameraType=camera_type,
    cameraDistance=300.0,
    cameraSpeedLimit=25.0,
    cameraAlertId=report_id,
    cameraChime=chime,
  )
  return iq_events.speed_camera_alert(None, None, {"iqNavState": nav}, False, 0, None)


def test_existing_camera_audio_is_unchanged():
  result = alert(custom.IQNavState.CameraType.fixedSpeed)
  assert result.audible_alert == car.CarControl.HUDControl.AudibleAlert.prompt


def test_police_visual_mode_is_silent():
  result = alert(custom.IQNavState.CameraType.police, report_id="visual", chime=False)
  assert result.audible_alert == car.CarControl.HUDControl.AudibleAlert.none


def test_police_chime_is_deduplicated_by_report():
  iq_events._POLICE_CHIMED_IDS.clear()
  first = alert(custom.IQNavState.CameraType.police, report_id="police-a", chime=True)
  second = alert(custom.IQNavState.CameraType.police, report_id="police-a", chime=True)
  assert first.audible_alert == car.CarControl.HUDControl.AudibleAlert.prompt
  assert second.audible_alert == car.CarControl.HUDControl.AudibleAlert.none
