# iqmacvisiond — IQ Vision offload server

Runs the iqvd perception model on an Apple-Silicon Mac and serves 2D detections
to one IQ device over wifi. The device (`iqvd`) ships the camera frame, the Mac
runs YOLO on the Metal GPU/NPU, and only boxes come back — the device does no
inference, so vision dots no longer contend with the driving model.

## Wire path

```
IQ device (auto-hotspot AP)  ──wifi──▶  Mac (IQ Vision.app)
  iqvd VisionClient                       iqmacvisiond server
  read frame → downscale 640w             cv2 decode → YOLOv8n (Metal)
  JPEG encode → INFER  ───────────────▶   detect
  RESULT ◀───────────────────────────     {tracks: [2D boxes]}
  publish iqVehicleTracks (dots)
  publish iqEnvironment (3D via ground-plane + calibration)
```

- Discovery: the device UDP-broadcasts `IQVISION_DISCOVER_V1` on the subnet; the
  Mac replies `IQVISION_HERE_V1:<tcp_port>`. No config, no pairing.
- Protocol: `iqvd_private_src/offload/protocol.py` (length-prefixed frames, JSON
  header + optional binary blob). Shared verbatim by both sides — it is the ABI.
- Ports: tcp/51998 inference, udp/51999 discovery, tcp/51995 localhost status.

## The Mac app

- Menu-bar app (`◎` waiting, `◉` connected). Menu shows device, frames served,
  inference p50/p99, and **Quit**.
- Keeps the Mac awake while running (`caffeinate`).
- Ships the model in the dmg — no download on first run.
- First launch creates a small venv (numpy, opencv-headless, rumps); tinygrad is
  bundled.

## Build

```
tools/iqmacvisiond/macos/build_dmg.sh
```

Produces `IQ Vision.app` and `IQVision.dmg`. See `macos/SIGNING.md` for signing +
notarization.

## Run from source (dev)

```
DEV=METAL python3 tools/iqmacvisiond/server.py          # server only
python3 tools/iqmacvisiond/menubar.py                    # menu-bar wrapper
python3 tools/iqmacvisiond/test_offload.py               # protocol/geometry/loopback
```

Gating on the device: `VisionVehicleTracks` enables iqvd; when `maciqmodeld` (the
eMac driving offload) is running, iqvd is Mac-or-nothing — it never runs local
inference. On non-eMac setups iqvd falls back to on-device YOLO if no Mac is found.
