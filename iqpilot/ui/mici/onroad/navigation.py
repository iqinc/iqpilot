import math
import time

import pyray as rl

from iqpilot.selfdrive.ui.ui_state import ui_state
from iqpilot.system.ui.lib.application import FontWeight, gui_app
from iqpilot.system.ui.lib.text_measure import measure_text_cached
from iqpilot.system.ui.lib.wrap_text import wrap_text


class MiciNavigation:
  def __init__(self):
    self.active = False
    self._destination = None
    self._checked_at = -math.inf
    self._distance = ""
    self._street = "Calculating route..."
    self._icon = None
    self._state_key = None
    self._layout_key = None
    self._lines = []
    self._distance_font = gui_app.font(FontWeight.BOLD)
    self._street_font = gui_app.font(FontWeight.MEDIUM)

  def update(self):
    now = time.monotonic()
    if now - self._checked_at >= 0.5:
      self._checked_at = now
      destination = ui_state.params.get("NavigationDestination")
      try:
        lat, lon = float(destination['latitude']), float(destination['longitude'])
        self._destination = (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None
      except (KeyError, TypeError, ValueError):
        self._destination = None
    self.active = bool(self._destination) and ui_state.started
    if not self.active:
      return

    sm = ui_state.sm
    nav = sm['iqNavState']
    fresh = sm.alive['iqNavState'] and sm.valid['iqNavState']
    key = (sm.recv_frame['iqNavState'], fresh, self._destination, ui_state.is_metric)
    if key == self._state_key:
      return
    self._state_key = key
    matches = (nav.destinationValid and
               abs(nav.destinationLatitude - self._destination[0]) < 1e-5 and
               abs(nav.destinationLongitude - self._destination[1]) < 1e-5)
    distance = nav.nextManeuverDistance
    if not (fresh and matches and nav.nextManeuverValid and math.isfinite(distance) and distance >= 0):
      self._distance, self._icon = "", None
      self._street = "Calculating route..." if fresh else "Waiting for navigation..."
      return

    if ui_state.is_metric:
      self._distance = f"{distance / 1000:.1f} km" if distance >= 1000 else f"{round(distance / 10) * 10} m"
    else:
      feet = distance / 0.3048
      self._distance = f"{distance / 1609.344:.1f} mi" if feet >= 1000 else f"{round(feet / 10) * 10} ft"
    self._street = " ".join(nav.nextManeuverDescription.split()) or "Continue on route"
    direction = 'left' if nav.nextManeuverDirection.raw == 1 else 'right'
    kind = {1: 'turn', 2: 'off_ramp', 3: 'merge', 4: 'fork', 7: 'turn'}.get(nav.nextManeuverType.raw, 'continue')
    self._icon = 'direction_arrive.png' if nav.nextManeuverType.raw == 6 else f'direction_{kind}_{direction}.png'

  def render(self, rect):
    rl.draw_rectangle_rec(rect, rl.BLACK)
    text_x = rect.x + (140 if self._icon else 24)
    width = max(1, rect.width - (text_x - rect.x) - 20)
    key = (self._street, width)
    if key != self._layout_key:
      self._layout_key = key
      lines = wrap_text(self._street_font, self._street, 30, width)
      self._lines = lines[:2]
      if len(lines) > 2:
        line = self._lines[-1]
        while line and measure_text_cached(self._street_font, line + '...', 30).x > width:
          line = line[:-1]
        self._lines[-1] = line + '...'
    if self._icon:
      icon = gui_app.texture('navigation/' + self._icon, 96, 96)
      rl.draw_texture(icon, int(rect.x + 24), int(rect.y + (rect.height - 96) / 2), rl.WHITE)
    top = rect.y + (rect.height - (64 if self._distance else 0) - 36 * len(self._lines)) / 2
    if self._distance:
      rl.draw_text_ex(self._distance_font, self._distance, rl.Vector2(text_x, top), 54, 0, rl.WHITE)
      top += 64
    for line in self._lines:
      rl.draw_text_ex(self._street_font, line, rl.Vector2(text_x, top), 30, 0, rl.WHITE)
      top += 36
