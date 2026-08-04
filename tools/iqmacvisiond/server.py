#!/usr/bin/env python3
"""
Copyright © IQ.Lvbs, apart of Project Teal Lvbs, All Rights Reserved, licensed under https://konn3kt.com/tos/
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("DEV", "METAL")
os.environ.setdefault("JIT_BATCH_SIZE", "0")

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import cv2
import numpy as np

from openpilot.iqpilot.iqvd_private_src.offload.protocol import (
  DISCOVERY_MAGIC, DISCOVERY_REPLY, DISCOVERY_PORT_DEFAULT, MSG_HELLO, MSG_HELLO_ACK, MSG_INFER,
  MSG_PING, MSG_PONG, MSG_RESULT, ProtocolError, recv_msg, send_msg,
)
from openpilot.iqpilot.iqvd_private_src.offload.perception import Detector

log = logging.getLogger("iqmacvisiond")

DEFAULT_PORT = 51998
STATUS_PORT = 51995
MODEL_NAME = "yolov8n"
SESSION_IDLE_TIMEOUT_S = 8.0

STATUS: dict = {
  "connected": False, "peer": "", "model": MODEL_NAME,
  "exec_p50_ms": 0.0, "exec_p99_ms": 0.0, "infer_count": 0,
  "last_seen": 0.0, "awake": False,
}


class KeepAwake:
  def __init__(self):
    self._proc: subprocess.Popen | None = None

  def start(self) -> None:
    if sys.platform != "darwin" or self._proc is not None:
      return
    try:
      self._proc = subprocess.Popen(["caffeinate", "-dimsu"])
      STATUS["awake"] = True
      log.info("keep-awake active (caffeinate pid=%d)", self._proc.pid)
    except OSError:
      log.warning("caffeinate unavailable; display may sleep")

  def stop(self) -> None:
    if self._proc is not None:
      self._proc.terminate()
      self._proc = None
      STATUS["awake"] = False


class DiscoveryResponder:
  def __init__(self, tcp_port: int, disc_port: int = DISCOVERY_PORT_DEFAULT):
    self.tcp_port = tcp_port
    self.disc_port = disc_port

  def start(self) -> None:
    threading.Thread(target=self._serve, daemon=True).start()

  def _serve(self) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
      sock.bind(("0.0.0.0", self.disc_port))
    except OSError:
      log.exception("discovery bind failed on udp/%d", self.disc_port)
      return
    reply = DISCOVERY_REPLY + f":{self.tcp_port}".encode()
    log.info("discovery responder on udp/%d -> tcp/%d", self.disc_port, self.tcp_port)
    while True:
      try:
        data, addr = sock.recvfrom(256)
      except OSError:
        continue
      if data.startswith(DISCOVERY_MAGIC):
        try:
          sock.sendto(reply, addr)
        except OSError:
          pass


class StatusServer:
  def __init__(self, port: int = STATUS_PORT):
    self.port = port

  def start(self) -> None:
    threading.Thread(target=self._serve, daemon=True).start()

  def _serve(self) -> None:
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
      def log_message(self, *a):
        pass

      def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

      def do_GET(self):
        if self.path.startswith("/status.json"):
          st = dict(STATUS)
          st["fresh"] = (time.time() - st["last_seen"]) < 6 if st["last_seen"] else False
          self._send(200, "application/json", json.dumps(st).encode())
        else:
          self._send(200, "text/html; charset=utf-8", _STATUS_HTML.encode())

    try:
      HTTPServer(("127.0.0.1", self.port), H).serve_forever()
    except OSError:
      log.exception("status server failed on %d", self.port)


_STATUS_HTML = """<!doctype html><html><head><meta charset=utf-8>
<title>IQ Vision</title><meta name=viewport content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:dark}
body{margin:0;font:15px -apple-system,system-ui,sans-serif;background:#0b0d10;color:#e6e9ef}
.wrap{max-width:520px;margin:0 auto;padding:32px 20px}
h1{font-size:20px;margin:0 0 20px;display:flex;align-items:center;gap:10px}
.dot{width:12px;height:12px;border-radius:50%;background:#555}
.dot.green{background:#28d2c8;box-shadow:0 0 10px #28d2c8}
.dot.red{background:#e74c3c}
.row{display:flex;justify-content:space-between;padding:12px 0;border-bottom:1px solid #1c2027}
.k{color:#8a91a0}.v{font-variant-numeric:tabular-nums}
</style></head><body><div class=wrap>
<h1><span class=dot id=dot></span><span id=title>IQ Vision</span></h1>
<div class=row><span class=k>Device</span><span class="v" id=peer>—</span></div>
<div class=row><span class=k>Model</span><span class="v" id=model>—</span></div>
<div class=row><span class=k>Inference (p50 / p99)</span><span class="v" id=exec>—</span></div>
<div class=row><span class=k>Frames served</span><span class="v" id=count>—</span></div>
<div class=row><span class=k>Keep awake</span><span class="v" id=awake>—</span></div>
</div><script>
async function tick(){
 try{
  const s=await (await fetch('/status.json')).json();
  const live=s.connected&&s.fresh;
  document.getElementById('dot').className='dot '+(live?'green':'red');
  document.getElementById('title').textContent=live?'IQ Vision — connected':'IQ Vision — waiting for device';
  document.getElementById('peer').textContent=s.peer||'not connected';
  document.getElementById('model').textContent=s.model||'—';
  document.getElementById('exec').textContent=s.exec_p50_ms?`${s.exec_p50_ms.toFixed(1)} / ${s.exec_p99_ms.toFixed(1)} ms`:'—';
  document.getElementById('count').textContent=s.infer_count?s.infer_count.toLocaleString():'—';
  document.getElementById('awake').textContent=s.awake?'on':'off';
 }catch(e){document.getElementById('dot').className='dot red';}
}
tick();setInterval(tick,1000);
</script></body></html>"""


class Session:
  def __init__(self, conn: socket.socket, detector: Detector):
    self.conn = conn
    self.detector = detector
    try:
      self.peer = conn.getpeername()[0]
    except OSError:
      self.peer = ""
    self.infer_count = 0
    self.exec_ms: list[float] = []

  def handshake(self) -> bool:
    msg_type, header, _ = recv_msg(self.conn)
    if msg_type != MSG_HELLO:
      raise ProtocolError(f"expected HELLO, got {msg_type}")
    send_msg(self.conn, MSG_HELLO_ACK, {"ok": True, "model": MODEL_NAME, "hostname": socket.gethostname()})
    STATUS.update(connected=True, peer=self.peer, last_seen=time.time())
    log.info("device connected: %s dongle=%s", self.peer, header.get("dongle_id", ""))
    return True

  def serve(self) -> None:
    while True:
      msg_type, header, blob = recv_msg(self.conn)
      if msg_type == MSG_INFER:
        self._infer(header, blob)
      elif msg_type == MSG_PING:
        send_msg(self.conn, MSG_PONG, {})
      else:
        raise ProtocolError(f"unexpected message type {msg_type}")

  def _infer(self, header: dict, jpeg: bytes) -> None:
    st = time.perf_counter()
    tracks = []
    try:
      rgb = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
      if rgb is not None:
        tracks = self.detector.detect(cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB))
    except Exception:
      log.exception("inference failed for frame %s", header.get("frame_id"))
    dt = (time.perf_counter() - st) * 1e3
    send_msg(self.conn, MSG_RESULT, {"frame_id": header.get("frame_id", 0), "tracks": tracks,
                                     "exec_ms": dt})
    self.infer_count += 1
    self.exec_ms.append(dt)
    if len(self.exec_ms) > 400:
      del self.exec_ms[:200]
    if self.infer_count % 10 == 0 or self.infer_count == 1:
      recent = self.exec_ms[-200:]
      STATUS.update(connected=True, infer_count=self.infer_count, last_seen=time.time(),
                    exec_p50_ms=float(np.percentile(recent, 50)),
                    exec_p99_ms=float(np.percentile(recent, 99)))


def _weights_ok() -> bool:
  from openpilot.iqpilot.iqvd_private_src.offload.perception import _weights_dir
  return (_weights_dir() / "yolov8n.safetensors").exists()


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--host", default="0.0.0.0")
  parser.add_argument("--port", type=int, default=DEFAULT_PORT)
  parser.add_argument("--no-keep-awake", action="store_true")
  args = parser.parse_args()

  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

  if not _weights_ok():
    log.error("yolov8n.safetensors not found; the app ships the model with it")
    sys.exit(1)

  keep_awake = KeepAwake()
  if not args.no_keep_awake:
    keep_awake.start()

  def _shutdown(*_):
    keep_awake.stop()
    sys.exit(0)
  try:
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
  except ValueError:
    pass

  t0 = time.perf_counter()
  detector = Detector(None)
  for _ in range(3):
    detector.detect(np.zeros((416, 640, 3), dtype=np.uint8))
  log.info("model warm in %.1fs", time.perf_counter() - t0)

  DiscoveryResponder(args.port).start()
  StatusServer().start()
  log.info("status dashboard on http://127.0.0.1:%d", STATUS_PORT)

  server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  server.bind((args.host, args.port))
  server.listen(1)
  log.info("READY listening on %s:%d", args.host, args.port)

  try:
    while True:
      conn, addr = server.accept()
      conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
      conn.settimeout(SESSION_IDLE_TIMEOUT_S)
      try:
        session = Session(conn, detector)
        if session.handshake():
          session.serve()
      except (ConnectionError, ProtocolError, OSError) as e:
        log.info("session ended: %s", e)
      finally:
        conn.close()
        STATUS.update(connected=False, peer="")
  finally:
    keep_awake.stop()


if __name__ == "__main__":
  main()
