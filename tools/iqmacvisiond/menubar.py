#!/usr/bin/env python3
"""
Copyright © IQ.Lvbs, apart of Project Teal Lvbs, All Rights Reserved, licensed under https://konn3kt.com/tos/
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import rumps

HERE = Path(__file__).resolve().parent
STATUS_URL = "http://127.0.0.1:51995/status.json"
DASHBOARD_URL = "http://127.0.0.1:51995"


class IQVisionApp(rumps.App):
  def __init__(self):
    super().__init__("IQ Vision", title="◎", quit_button=None)
    self.item_state = rumps.MenuItem("Starting…")
    self.item_device = rumps.MenuItem("Device: —")
    self.item_frames = rumps.MenuItem("Frames: —")
    self.item_exec = rumps.MenuItem("Inference: —")
    self.item_awake = rumps.MenuItem("Keep awake: —")
    self.menu = [
      self.item_state, None,
      self.item_device, self.item_frames, self.item_exec, self.item_awake, None,
      rumps.MenuItem("Open Dashboard", callback=self.open_dashboard),
      rumps.MenuItem("Quit IQ Vision", callback=self.quit_app),
    ]
    self.proc: subprocess.Popen | None = None
    self._start_server()
    self.timer = rumps.Timer(self.refresh, 1)
    self.timer.start()

  def _start_server(self) -> None:
    env = dict(os.environ)
    self.proc = subprocess.Popen([sys.executable, str(HERE / "server.py")], env=env)

  def refresh(self, _) -> None:
    if self.proc is not None and self.proc.poll() is not None:
      self.title = "◎!"
      self.item_state.title = "Server stopped — reopen the app"
      return
    try:
      with urllib.request.urlopen(STATUS_URL, timeout=0.8) as r:
        s = json.load(r)
    except Exception:
      self.title = "◎"
      self.item_state.title = "Warming up…"
      return
    live = s.get("connected") and s.get("fresh")
    self.title = "◉" if live else "◎"
    self.item_state.title = "Connected" if live else "Waiting for device"
    self.item_device.title = f"Device: {s.get('peer') or '—'}"
    self.item_frames.title = f"Frames: {s.get('infer_count', 0):,}"
    p50, p99 = s.get("exec_p50_ms", 0.0), s.get("exec_p99_ms", 0.0)
    self.item_exec.title = f"Inference: {p50:.0f} / {p99:.0f} ms" if p50 else "Inference: —"
    self.item_awake.title = f"Keep awake: {'on' if s.get('awake') else 'off'}"

  def open_dashboard(self, _) -> None:
    subprocess.Popen(["open", DASHBOARD_URL])

  def quit_app(self, _) -> None:
    if self.proc is not None:
      self.proc.terminate()
      try:
        self.proc.wait(timeout=3)
      except subprocess.TimeoutExpired:
        self.proc.kill()
    rumps.quit_application()


if __name__ == "__main__":
  IQVisionApp().run()
