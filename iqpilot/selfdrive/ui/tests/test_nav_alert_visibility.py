# Copyright (c) 2026 IQ.Lvbs. All rights reserved.
from types import SimpleNamespace

import pyray as rl
import pytest

from iqpilot.selfdrive.ui.onroad.alert_renderer import AlertRenderer, AlertSize
from iqpilot.selfdrive.ui.onroad import alert_renderer
from iqpilot.ui.onroad import hud_renderer


@pytest.mark.parametrize('alert_size', [0, AlertSize.small, AlertSize.mid, AlertSize.full])
@pytest.mark.parametrize('maps_enabled', [False, True])
def test_alerts_do_not_disable_navigation(monkeypatch, alert_size, maps_enabled):
  details = []
  maps = []
  panel = SimpleNamespace(maps_enabled=lambda: maps_enabled, render_details=details.append, render_split=maps.append)
  hud = SimpleNamespace(nav_map_panel=panel)
  monkeypatch.setattr(hud_renderer, 'ui_state', SimpleNamespace(sm={'selfdriveState': SimpleNamespace(alertSize=alert_size)}))
  visible = hud_renderer.IQHudRenderer.render_navigation(hud, rl.Rectangle(30, 30, 2100, 1020))
  assert visible is maps_enabled
  assert len(details) == int(maps_enabled)
  assert len(maps) == int(maps_enabled)


@pytest.mark.parametrize('alert_size', [AlertSize.small, AlertSize.mid])
def test_alert_banner_leaves_map_and_instructions_visible(alert_size):
  rect = rl.Rectangle(30, 30, 2100, 1020)
  renderer = SimpleNamespace(navigation_visible=True)
  banner = AlertRenderer._get_alert_rect(renderer, rect, alert_size)
  map_left = rect.x + rect.width - 456
  instruction_bottom = rect.y + 36 + 200
  assert banner.x + banner.width < map_left
  assert banner.y > instruction_bottom


def test_banner_remains_compact_without_navigation():
  renderer = SimpleNamespace(navigation_visible=False)
  rect = rl.Rectangle(30, 30, 2100, 1020)
  banner = AlertRenderer._get_alert_rect(renderer, rect, AlertSize.small)
  assert banner.width == 860
  assert banner.x + banner.width / 2 == rect.x + rect.width / 2


@pytest.mark.parametrize('navigation_visible', [False, True])
def test_full_takeover_alert_keeps_full_screen_priority(navigation_visible):
  renderer = SimpleNamespace(navigation_visible=navigation_visible)
  rect = rl.Rectangle(30, 30, 2100, 1020)
  assert AlertRenderer._get_alert_rect(renderer, rect, AlertSize.full) is rect


def test_long_alert_text_fits_compact_card(monkeypatch):
  drawn = []
  monkeypatch.setattr(alert_renderer, 'measure_text_cached', lambda font, text, size: rl.Vector2(len(text) * size, size))
  monkeypatch.setattr(alert_renderer.rl, 'draw_text_ex', lambda *args: drawn.append(args))
  rect = rl.Rectangle(100, 100, 804, 144)
  renderer = SimpleNamespace(font_bold=None, font_regular=None)
  alert = alert_renderer.Alert(text1='Long driver attention warning', text2='Keep your eyes on the road', size=AlertSize.small)
  AlertRenderer._draw_text(renderer, rect, alert)
  assert len(drawn) == 2
  for _, text, position, size, _, _ in drawn:
    assert position.x >= rect.x
    assert position.x + len(text) * size <= rect.x + rect.width
