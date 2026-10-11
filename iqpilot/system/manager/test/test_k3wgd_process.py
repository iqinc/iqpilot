# Copyright © IQ.Lvbs, apart of Project Teal Lvbs, All Rights Reserved, licensed under https://konn3kt.com/tos/
from pathlib import Path

from iqpilot.selfdrive.selfdrived.selfdrived import NON_BLOCKING_PROCESSES
from iqpilot.system.manager.process_config import always_run, managed_processes

WIREGUARD_GO = Path(__file__).resolve().parents[3] / "third_party" / "wireguard-go" / "larch64" / "wireguard-go"


def test_tunnel_daemon_never_blocks_engagement():
  assert "k3wgd" in NON_BLOCKING_PROCESSES


def test_tunnel_daemon_always_runs_from_the_private_bundle():
  proc = managed_processes["k3wgd"]
  assert proc.should_run is always_run
  assert proc.bundle == "iqpilot_hephaestusd_private"


def test_wireguard_go_ships_with_the_tree():
  assert WIREGUARD_GO.is_file()
  assert WIREGUARD_GO.stat().st_size > 1_000_000
