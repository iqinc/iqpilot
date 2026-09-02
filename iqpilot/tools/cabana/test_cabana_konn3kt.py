import json
import subprocess
from pathlib import Path

CABANA_DIR = Path(__file__).parent
CABANA_BIN = CABANA_DIR / "_cabana"


def read(name):
  return (CABANA_DIR / name).read_text()


class TestCabanaBinary:
  def test_help(self):
    assert CABANA_BIN.exists(), "cabana not built (scons -u iqpilot/tools/cabana/_cabana)"
    result = subprocess.run([str(CABANA_BIN), "--help"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stderr

  def test_help_documents_both_live_can_modes(self):
    assert CABANA_BIN.exists(), "cabana not built (scons -u iqpilot/tools/cabana/_cabana)"
    # --zmq attaches directly (konn3kt_canproxy); --bridge is upstream's local-bridge path
    out = subprocess.run([str(CABANA_BIN), "--help"], capture_output=True, text=True, timeout=60).stderr
    assert "--zmq" in out
    assert "--bridge" in out
    assert "konn3kt_canproxy.py" in out

  def test_launcher_builds_the_right_targets(self):
    launcher = read("cabana")
    # iqpilot is not nested under openpilot/
    assert "scons -u iqpilot/tools/cabana/_cabana iqpilot/cereal/messaging/bridge" in launcher
    assert "openpilot/tools/cabana" not in launcher


class TestKonn3ktIntegration:
  def test_routes_dialog_uses_konn3kt_api(self):
    # upstream shells into tools/lib via PyDownloader; iqpilot goes through CommaApi2
    routes = read("streams/routes.cc")
    assert "PyDownloader::" not in routes
    assert "py_downloader.h" not in routes
    assert "CommaApi2::getDevices()" in routes
    assert "CommaApi2::getDeviceRoutes(" in routes

  def test_no_duplicate_qt_api_client(self):
    # the old Qt HttpRequest/JWT client was folded into tools/replay/api.cc
    assert not (CABANA_DIR / "utils" / "api.cc").exists()
    assert not (CABANA_DIR / "utils" / "api.h").exists()

  def test_no_comma_endpoints(self):
    for name in ("streams/routes.cc", "cabana.cc", "README.md"):
      body = read(name)
      assert "connect.comma.ai" not in body, name
      assert "api.comma.ai" not in body, name

  def test_dbc_menu_points_at_iqdbc(self):
    mainwin = read("mainwin.cc")
    assert "commaai/iqdbc" in mainwin
    assert "commaai/opendbc" not in mainwin

  def test_dbc_json_generator_imports_iqdbc(self):
    gen = read("dbc/generate_dbc_json.py")
    assert "from iqdbc.car" in gen
    assert "from opendbc.car" not in gen

  def test_generated_dbc_json_covers_iqdbc_platforms(self):
    # built by scons; cabana reads it to auto-select a DBC per car fingerprint
    path = CABANA_DIR / "dbc" / "car_fingerprint_to_dbc.json"
    assert path.is_file(), "run scons -u iqpilot/tools/cabana"
    mapping = json.loads(path.read_text())
    assert len(mapping) > 100
    assert all(isinstance(v, str) and v for v in mapping.values())

  def test_canproxy_targets_konn3kt(self):
    proxy = read("konn3kt_canproxy.py")
    assert "konn3kt" in proxy
    # the proxy publishes on a local ZMQ 'can' socket that --zmq attaches to
    assert 'os.environ["ZMQ"] = "1"' in proxy


class TestDeviceStreamModes:
  """The ZMQ attach is what konn3kt_canproxy feeds; upstream #38484 replaced it with a
  bridge fork. Both must exist, and only the direct attach may set ZMQ=1."""

  def test_three_modes_exist(self):
    header = read("streams/devicestream.h")
    assert "enum class Mode { Msgq, Zmq, Bridge };" in header

  def test_only_zmq_mode_talks_zmq(self):
    src = read("streams/devicestream.cc")
    assert 'mode_ == Mode::Zmq ? setenv("ZMQ", "1", 1) : unsetenv("ZMQ")' in src

  def test_zmq_mode_subscribes_to_the_given_address(self):
    # regression guard: upstream hardcodes 127.0.0.1 and relies on the bridge fork,
    # which never reads the canproxy publisher
    src = read("streams/devicestream.cc")
    assert 'const std::string address = mode_ == Mode::Zmq ? address_.toStdString() : "127.0.0.1";' in src

  def test_only_bridge_mode_forks_the_bridge(self):
    src = read("streams/devicestream.cc")
    assert "if (mode_ == Mode::Bridge) {" in src
