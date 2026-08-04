#!/usr/bin/env python3
"""
Copyright © IQ.Lvbs, apart of Project Teal Lvbs, All Rights Reserved, licensed under https://konn3kt.com/tos/
"""
import socket
import sys
import threading
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from openpilot.iqpilot.iqvd_private_src.offload import protocol
from openpilot.iqpilot.iqvd_private_src.offload.client import VisionClient, discover_server
from openpilot.iqpilot.iqvd_private_src.offload.geometry import pixel_to_ground, tracks_to_objects


def test_protocol_roundtrip():
  a, b = socket.socketpair()
  protocol.send_msg(a, protocol.MSG_INFER, {"frame_id": 7, "w": 640}, b"\x00\x01\x02payload")
  mt, header, blob = protocol.recv_msg(b)
  assert mt == protocol.MSG_INFER
  assert header == {"frame_id": 7, "w": 640}
  assert blob == b"\x00\x01\x02payload"
  a.close()
  b.close()


def test_protocol_empty_blob():
  a, b = socket.socketpair()
  protocol.send_msg(a, protocol.MSG_HELLO_ACK, {"ok": True, "model": "yolov8n"})
  mt, header, blob = protocol.recv_msg(b)
  assert mt == protocol.MSG_HELLO_ACK and header["model"] == "yolov8n" and blob == b""
  a.close()
  b.close()


def test_geometry_center_projects_forward():
  intr = np.array([[900.0, 0.0, 960.0], [0.0, 900.0, 604.0], [0.0, 0.0, 1.0]])
  device_from_calib = np.eye(3)
  p = pixel_to_ground(960.0, 900.0, intr, device_from_calib, 1.22)
  assert p is not None
  assert p[0] > 0
  assert abs(p[1]) < 1.0


def test_geometry_above_horizon_none():
  intr = np.array([[900.0, 0.0, 960.0], [0.0, 900.0, 604.0], [0.0, 0.0, 1.0]])
  assert pixel_to_ground(960.0, 100.0, intr, np.eye(3), 1.22) is None


def test_tracks_to_objects():
  intr = np.array([[900.0, 0.0, 960.0], [0.0, 900.0, 604.0], [0.0, 0.0, 1.0]])
  tracks = [{"x1": 0.45, "y1": 0.5, "x2": 0.55, "y2": 0.75, "prob": 0.9, "label": "car"}]
  objs = tracks_to_objects(tracks, 1928, 1208, intr, [0.0, 0.0, 0.0])
  assert len(objs) == 1
  assert objs[0]["x"] > 0 and objs[0]["label"] == "car"


class _StubDetector:
  def detect(self, rgb):
    return [{"x1": 0.1, "y1": 0.2, "x2": 0.3, "y2": 0.5, "prob": 0.8, "label": "car"}]


def _run_server(port, ready):
  from openpilot.iqpilot.iqvd_private_src.offload import protocol as p
  import tools.iqmacvisiond.server as srv
  disc = srv.DiscoveryResponder(port)
  disc.start()
  server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  server.bind(("0.0.0.0", port))
  server.listen(1)
  ready.set()
  conn, _ = server.accept()
  conn.settimeout(5)
  session = srv.Session(conn, _StubDetector())
  session.handshake()
  try:
    session.serve()
  except (p.ProtocolError, OSError):
    pass


def test_discovery_and_loopback():
  port = 52050
  ready = threading.Event()
  threading.Thread(target=_run_server, args=(port, ready), daemon=True).start()
  assert ready.wait(5)
  time.sleep(0.2)

  found = discover_server(timeout=2.0)
  assert found is not None, "discovery failed"
  assert found[1] == port

  import cv2
  ok, jpeg = cv2.imencode(".jpg", np.zeros((400, 640, 3), dtype=np.uint8))
  assert ok

  client = VisionClient("test-dongle")
  assert client.connect(), "connect failed"
  meta = {"frame_id": 42, "wide": False, "w": 640, "h": 400}
  tracks = client.infer(jpeg.tobytes(), meta)
  assert tracks is not None and len(tracks) == 1
  assert tracks[0]["label"] == "car"
  client.close()


if __name__ == "__main__":
  fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
  for fn in fns:
    fn()
    print(f"ok {fn.__name__}")
  print(f"\n{len(fns)} passed")
