"""
Copyright © IQ.Lvbs, apart of Project Teal Lvbs, All Rights Reserved, licensed under https://konn3kt.com/tos/
"""
from __future__ import annotations

import argparse
import os
import pickle
import time

os.environ.setdefault("DEV", "USB+AMD:LLVM")
os.environ.setdefault("FLOAT16", "1")
os.environ.setdefault("JIT_BATCH_SIZE", "0")
os.environ.setdefault("GMMU", "0")

import numpy as np

from iqpilot.selfdrive.iqmodeld.egpu_helpers import egpu_pkl_path, local_onnx, patch_tinygrad_fetch_fw
from iqpilot.selfdrive.iqmodeld.egpu_model import EGPU_MODELS, get_egpu_model, resolve_egpu_model
from iqpilot.selfdrive.iqmodeld.temporal_state import MODEL_INPUT_SPEC, spec_from_meta

INPUT_SPEC = dict(MODEL_INPUT_SPEC)

patch_tinygrad_fetch_fw()

SEED = 42


def set_input_spec(meta: dict) -> None:
  spec = spec_from_meta(meta)
  if spec is not None:
    INPUT_SPEC.clear()
    INPUT_SPEC.update(spec)


def make_run_model(model_runner):
  def run_model(**inputs):
    out = next(iter(model_runner({k: inputs[k] for k in INPUT_SPEC}).values())).cast("float32")
    return out.reshape(-1),
  return run_model


def _random_inputs(seed: int):
  from tinygrad.device import Device
  from tinygrad.tensor import Tensor
  rng = np.random.default_rng(seed)
  out = {}
  for name, (shape, dtype) in INPUT_SPEC.items():
    if dtype == "uint8":
      arr = rng.integers(0, 256, shape).astype(np.uint8)
    else:
      arr = rng.standard_normal(shape).astype(np.float32)
    out[name] = Tensor(arr, device=Device.DEFAULT).realize()
  return out


def _run(fn, seed: int) -> np.ndarray:
  from tinygrad.device import Device
  st = time.perf_counter()
  outs = fn(**_random_inputs(seed))
  Device.default.synchronize()
  print(f"  run(seed={seed}) {(time.perf_counter() - st) * 1e3:6.1f} ms")
  return outs[0].numpy().reshape(-1)


def compile_model(meta: dict, onnx_path: str, out_path: str) -> str:
  from tinygrad.device import Device
  from tinygrad.engine.jit import TinyJit
  from tinygrad.nn.onnx import OnnxRunner

  if meta.get("split"):
    raise RuntimeError(f"model {meta['key']} is a split model; eGPU v1 compiles fused models only")

  jit = TinyJit(make_run_model(OnnxRunner(onnx_path)), prune=True)

  print("capture + replay")
  for _ in range(2):
    baseline = _run(jit, SEED)
  if baseline.shape[0] != meta["output_len"]:
    raise RuntimeError(f"model output length {baseline.shape[0]} != registry {meta['output_len']}")
  if not np.isfinite(baseline).all():
    raise RuntimeError("compiled model produced non-finite outputs")

  print("pickle round trip")
  jit = pickle.loads(pickle.dumps(jit))
  if not np.array_equal(_run(jit, SEED), baseline):
    raise RuntimeError("outputs differ from baseline after pickle round trip")
  if np.array_equal(_run(jit, SEED + 1), baseline):
    raise RuntimeError("outputs insensitive to inputs after pickle round trip")

  from tinygrad.tensor import Tensor
  zeros = {name: Tensor(np.zeros(shape, dtype=dtype), device=Device.DEFAULT).realize()
           for name, (shape, dtype) in INPUT_SPEC.items()}
  flat = jit(**zeros)[0].numpy().reshape(-1)
  from iqpilot.selfdrive.iqmodeld.parser import PhaseParser
  from iqpilot.selfdrive.iqmodeld.tools.compile_supercombo import _slice_outputs, _validate_pose_outputs
  _validate_pose_outputs(PhaseParser().parse_vision_outputs(_slice_outputs(flat, meta["output_slices"])))

  bundle = {
    "run_model": jit,
    "model_key": meta["key"],
    "model_sha256": meta["sha256"],
    "output_len": int(meta["output_len"]),
    "frame_skip": int(meta["frame_skip"]),
    "input_spec": {name: (tuple(shape), dtype) for name, (shape, dtype) in INPUT_SPEC.items()},
    "input_device": Device.DEFAULT,
  }
  os.makedirs(os.path.dirname(out_path), exist_ok=True)
  tmp = out_path + ".part"
  with open(tmp, "wb") as f:
    pickle.dump(bundle, f, protocol=pickle.HIGHEST_PROTOCOL)
  os.replace(tmp, out_path)
  return out_path


def main() -> None:
  p = argparse.ArgumentParser()
  p.add_argument("--model", default=None, help=f"registry key, one of {sorted(EGPU_MODELS)}")
  p.add_argument("--onnx", default=None)
  p.add_argument("--output", default=None)
  args = p.parse_args()

  if args.model is not None:
    if args.model in EGPU_MODELS:
      meta = get_egpu_model(args.model)
    else:
      from iqpilot.common.params import Params
      meta = resolve_egpu_model(Params(), args.model)
      if meta is None:
        raise SystemExit(f"unknown model {args.model!r}: not a built-in ({sorted(EGPU_MODELS)}) and not in the synced catalog")
  else:
    meta = get_egpu_model()
  set_input_spec(meta)

  onnx_path = args.onnx or local_onnx(meta)
  if onnx_path is None or not os.path.isfile(onnx_path):
    raise SystemExit(f"onnx not found for {meta['key']}; pass --onnx or let iqegpumodeld download it first")

  out = compile_model(meta, onnx_path, args.output or egpu_pkl_path(meta))
  print(f"saved eGPU jit to {out} ({os.path.getsize(out) / 1e6:.2f} MB)")


if __name__ == "__main__":
  main()
