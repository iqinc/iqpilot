#!/bin/bash
# Copyright © IQ.Lvbs, apart of Project Teal Lvbs, All Rights Reserved, licensed under https://konn3kt.com/tos/
set -euo pipefail

RES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null && pwd)"
SUPPORT="$HOME/Library/Application Support/IQVision"
VENV="$SUPPORT/venv"
PY="$VENV/bin/python"

mkdir -p "$SUPPORT"

if [ ! -x "$PY" ]; then
  echo "Creating IQ Vision environment (one time)…"
  /usr/bin/python3 -m venv "$VENV"
  "$PY" -m pip install --upgrade --quiet pip
  "$PY" -m pip install --quiet numpy "opencv-python-headless>=4.8" rumps
fi

export PYTHONPATH="$RES:$RES/tinygrad_repo"
export DEV=METAL
exec "$PY" "$RES/tools/iqmacvisiond/menubar.py"
