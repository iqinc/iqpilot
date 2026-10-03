"""
Copyright © IQ.Lvbs, apart of Project Teal Lvbs, All Rights Reserved, licensed under https://konn3kt.com/tos/
"""

from iqpilot.selfdrive.ui.mici.widgets.stock_button import BigButton, BigParamControl
from iqpilot.selfdrive.ui.mici.layouts.settings.iq_widgets import MappedParamToggle
from iqpilot.selfdrive.ui.ui_state import ui_state
from iqpilot.system.ui.lib.application import gui_app
from iqpilot.system.ui.widgets.scroller import NavScroller
from iqpilot.system.ui.lib.multilang import tr


def _aol_modes() -> list[str]:
  return [tr("stay engaged"), tr("standby"), tr("disengage")]


def _has_limited_sab_options() -> bool:
  brand = ""
  if ui_state.is_offroad():
    bundle = ui_state.params.get("CarPlatformBundle")
    if bundle:
      brand = bundle.get("brand", "")
  if not brand:
    brand = ui_state.CP.brand if ui_state.CP else ""
  return brand == "rivian"


class SabSettingsPanel(NavScroller):
  def __init__(self):
    super().__init__()
    self._mode = MappedParamToggle(tr("Brake Response Mode"), "AolSteeringMode",
                                   _aol_modes(), [0, 1, 2], value_only=True)
    self._steer_override = BigParamControl(tr("Pause While You Steer"), "AolPauseOnSteeringOverride")
    self._scroller.add_widgets([self._mode, self._steer_override])

  def show_event(self):
    super().show_event()
    limited = _has_limited_sab_options()
    if limited:
      ui_state.params.remove("AolMainCruiseAllowed")
      ui_state.params.put_bool("AolUnifiedEngagementMode", True)
      ui_state.params.put("AolSteeringMode", 2)
    offroad = ui_state.is_offroad()
    for w in (self._mode,):
      w.refresh()
      w.set_enabled(offroad and not limited)
    self._steer_override.refresh()
    self._steer_override.set_enabled(offroad)


class LaneChangePanel(NavScroller):
  def __init__(self):
    super().__init__()
    self._timer = MappedParamToggle(tr("Auto Lane Change"), "IQLaneChangeTimer",
                                    [tr("nudge"), tr("no nudge")], [0, 1])
    self._edge_guard = BigParamControl(tr("Lane Edge Guard"), "IQEdgeGuard")
    self._scroller.add_widgets([self._timer, self._edge_guard])

  def show_event(self):
    super().show_event()
    self._timer.refresh()
    self._edge_guard.refresh()


class SteeringLayoutMici(NavScroller):
  def __init__(self):
    super().__init__()

    self._sab_panel = SabSettingsPanel()
    self._lc_panel = LaneChangePanel()

    self._aol = BigParamControl(tr("AOL"), "AolEnabled", toggle_callback=self._on_aol_toggled)
    self._sab_settings_button = BigButton(tr("steering assistance behavior"))
    self._sab_settings_button.set_click_callback(lambda: gui_app.push_widget(self._sab_panel))
    self._lane_change = BigButton(tr("lane change"))
    self._lane_change.set_click_callback(lambda: gui_app.push_widget(self._lc_panel))

    self._scroller.add_widgets([
      self._aol, self._sab_settings_button, self._lane_change,
    ])

  def _on_aol_toggled(self, checked: bool):
    if checked:
      ui_state.params.put_bool("AolUnifiedEngagementMode", True)

  def _refresh(self):
    offroad = ui_state.is_offroad()
    self._aol.refresh()
    self._aol.set_value(tr("on") if self._aol._checked else tr("off"))
    self._aol.set_enabled(offroad)
    self._sab_settings_button.set_enabled(offroad and self._aol._checked)

  def _update_state(self):
    super()._update_state()
    self._refresh()

  def show_event(self):
    super().show_event()
    self._refresh()
