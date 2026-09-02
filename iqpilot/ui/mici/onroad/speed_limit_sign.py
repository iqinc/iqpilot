"""
Copyright © IQ.Lvbs, apart of Project Teal Lvbs, All Rights Reserved, licensed under https://konn3kt.com/tos/
"""
import math

from iqpilot.common.filter_simple import FirstOrderFilter
from iqpilot.selfdrive.ui.ui_state import ui_state
from iqpilot.system.ui.iqwidgets.lib import canvas
from iqpilot.system.ui.lib.application import gui_app
from iqpilot.ui.onroad.hud_overlays import IQSpeedLimitOverlay, _SL_ASSIST, _SL_DARK, _dim

_SIGN_X = 16
_SIGN_Y = 108
_SIGN_W = 60
_SIGN_H = 64
_BADGE_SIDE = 24


class MiciSpeedLimitSign(IQSpeedLimitOverlay):
  def __init__(self):
    super().__init__()
    self._visible = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._obscured = False

  def set_obscured(self, obscured: bool) -> None:
    self._obscured = obscured

  def _badge(self) -> str:
    offset = round(self.speed_limit_offset)
    return f"{offset:+d}" if offset != 0 else ""

  def _render(self, rect):
    shown = ui_state.speed_limit_mode != 0 and ui_state.is_onroad() and not self._obscured
    alpha = self._visible.update(1.0 if shown else 0.0)
    if alpha < 1e-2:
      return
    if self.assist_state == _SL_ASSIST.preActive:
      self.assist_frame += 1
      pulse = 0.65 + 0.35 * math.sin(self.assist_frame * math.pi / gui_app.target_fps)
      alpha *= self._pulse_ema.update(pulse)
    else:
      self.assist_frame = 0
      self._pulse_ema.update(1.0)
    box = canvas.Box(rect.x + _SIGN_X, rect.y + _SIGN_Y, _SIGN_W, _SIGN_H)
    value, _, tint, has_limit = self._spec()
    badge = self._badge() if has_limit else ""
    (self._vienna if ui_state.is_metric else self._mutcd)(box, value, badge, tint, has_limit, alpha)

  def _vienna(self, rect, value, badge, tint, has_limit, alpha=1.0):
    hub = canvas.Pt(rect.x + rect.width / 2, rect.y + rect.height / 2)
    radius = rect.width / 2
    canvas.disc_at(hub, radius, _dim(canvas.WHITE, alpha))
    canvas.annulus(hub, radius * 0.78, radius, 0, 360, 36, _dim(canvas.RED, alpha))
    canvas.glyphs_centered(self._bold, value, 22 if len(value) >= 3 else 28, hub, _dim(tint, alpha))
    if badge:
      br = _BADGE_SIDE / 2
      bc = canvas.Pt(rect.x + rect.width - br * 0.35, rect.y + br * 0.35)
      canvas.disc_at(bc, br, _dim(canvas.BLACK, alpha))
      canvas.annulus(bc, br - 2, br, 0, 360, 24, _dim(_SL_DARK, alpha))
      canvas.glyphs_centered(self._bold, badge, 11 if len(badge) >= 3 else 13, bc, _dim(canvas.WHITE, alpha))

  def _mutcd(self, rect, value, badge, tint, has_limit, alpha=1.0):
    canvas.panel(rect, 0.25, 8, _dim(canvas.WHITE, alpha))
    inner = canvas.Box(rect.x + 4, rect.y + 4, rect.width - 8, rect.height - 8)
    canvas.panel_outline(inner, 0.25, 8, 2, _dim(canvas.BLACK, alpha))
    mid = rect.x + rect.width / 2
    canvas.glyphs_centered(self._demi, "SPEED", 12, canvas.Pt(mid, rect.y + 14), _dim(canvas.BLACK, alpha))
    canvas.glyphs_centered(self._demi, "LIMIT", 12, canvas.Pt(mid, rect.y + 25), _dim(canvas.BLACK, alpha))
    canvas.glyphs_centered(self._bold, value, 30 if len(value) <= 2 else 24, canvas.Pt(mid, rect.y + 45), _dim(tint, alpha))
    if badge:
      chip = canvas.Box(rect.x + rect.width - _BADGE_SIDE * 0.55, rect.y - _BADGE_SIDE * 0.55, _BADGE_SIDE, _BADGE_SIDE)
      canvas.panel(chip, 0.35, 8, _dim(canvas.BLACK, alpha))
      canvas.panel_outline(chip, 0.35, 8, 2, _dim(_SL_DARK, alpha))
      canvas.glyphs_centered(self._bold, badge, 11 if len(badge) >= 3 else 13,
                             canvas.Pt(chip.x + _BADGE_SIDE / 2, chip.y + _BADGE_SIDE / 2), _dim(canvas.WHITE, alpha))
