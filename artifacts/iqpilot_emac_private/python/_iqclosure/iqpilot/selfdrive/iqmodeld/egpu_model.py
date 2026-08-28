"""
Copyright © IQ.Lvbs, apart of Project Teal Lvbs, All Rights Reserved, licensed under https://konn3kt.com/tos/
"""
from __future__ import annotations

EGPU_MODELS: dict[str, dict] = {
  "lebrowski": {
    "model_name": "big_driving_supercombo",
    "sha256": "a501760a9d1d5fef0eab2b8c5d122d06124fc26dc8e0782e0aa94b82a208f0ff",
    "output_len": 2580,
    "frame_skip": 4,
    "download": {"kind": "comma_lfs", "size": 1757355221},
    "input_shapes": {
      "img": (1, 12, 128, 256),
      "big_img": (1, 12, 128, 256),
      "desire_pulse": (1, 25, 8),
      "traffic_convention": (1, 2),
      "action_t": (1, 2),
      "features_buffer": (1, 24, 512),
    },
    "output_slices": {
      "lane_lines": slice(0, 528),
      "lane_lines_prob": slice(528, 536),
      "road_edges": slice(536, 800),
      "meta": slice(800, 855),
      "desire_pred": slice(855, 887),
      "pose": slice(887, 899),
      "wide_from_device_euler": slice(899, 905),
      "road_transform": slice(905, 917),
      "plan": slice(917, 1907),
      "lead": slice(1907, 2051),
      "lead_prob": slice(2051, 2054),
      "desire_state": slice(2054, 2062),
      "action": slice(2062, 2066),
      "hidden_state": slice(2066, 2578),
      "pad": slice(-2, None),
    },
  },
  "comma_small": {
    "model_name": "driving_supercombo",
    "sha256": "659727c4d4839adc4992a254409a54259a8756a743f2d567bf5fdc6579f8009b",
    "output_len": 2576,
    "frame_skip": 4,
    "download": {"kind": "comma_lfs", "size": 60881999},
    "output_slices": {
      "meta": slice(0, 55),
      "desire_pred": slice(55, 87),
      "pose": slice(87, 99),
      "wide_from_device_euler": slice(99, 105),
      "road_transform": slice(105, 117),
      "lane_lines": slice(117, 645),
      "lane_lines_prob": slice(645, 653),
      "road_edges": slice(653, 917),
      "lead": slice(917, 1061),
      "lead_prob": slice(1061, 1064),
      "hidden_state": slice(1064, 1576),
      "plan": slice(1576, 2566),
      "desire_state": slice(2566, 2574),
      "pad": slice(-2, None),
    },
  },
}

DEFAULT_EGPU_MODEL = "lebrowski"

BIG_MODEL_NAME = "big_driving_supercombo"


def get_egpu_model(key: str | None = None) -> dict:
  name = key or DEFAULT_EGPU_MODEL
  if name not in EGPU_MODELS:
    raise KeyError(f"unknown eGPU model {name!r}; known: {sorted(EGPU_MODELS)}")
  return {"key": name, **EGPU_MODELS[name]}


def resolve_egpu_model(params, selected: str | bytes | None = None, allow_refresh: bool = True) -> dict | None:
  name = selected if selected is not None else (params.get("IQEmacModel") if params is not None else None)
  name = name.decode() if isinstance(name, bytes) else (name or "")
  if not name or name == DEFAULT_EGPU_MODEL:
    return get_egpu_model()
  from iqpilot.selfdrive.iqmodeld.big_catalog import cached_catalog, refresh_catalog
  catalog = cached_catalog(params)
  if name not in catalog and params is not None and allow_refresh:
    refresh_catalog(params)
    catalog = cached_catalog(params)
  meta = catalog.get(name)
  if meta is None or meta.get("model_name") != BIG_MODEL_NAME:
    return None
  return {"key": name, **meta}


def download_descriptor(meta: dict) -> tuple[str, int]:
  dl = meta.get("download")
  if not dl:
    return "", 0
  if dl["kind"] == "comma_lfs":
    return f"commalfs:{meta['sha256']}", int(dl["size"])
  if dl["kind"] == "url":
    return str(dl["url"]), int(dl.get("size", 0))
  return "", 0
