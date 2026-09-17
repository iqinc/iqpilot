from iqdbc.car.volkswagen.interface import _moduleless_acc_available
from iqdbc.car.volkswagen.values import VolkswagenFlagsIQ, VolkswagenSafetyFlags

MSG_GRA_NEU = 0x38A
NO_RADAR = VolkswagenFlagsIQ.IQ_CC_ONLY_NO_RADAR.value
CC_ONLY_WITH_RADAR = VolkswagenFlagsIQ.IQ_CC_ONLY.value


class FakeParams:
  def __init__(self, armed):
    self.armed = armed
    self.reads = []

  def get_bool(self, key):
    self.reads.append(key)
    return self.armed and key == "VwPqModulelessAcc"


class UnknownKeyParams:
  def get_bool(self, key):
    from iqpilot.common.params import UnknownKeyName
    raise UnknownKeyName(key)


def _fingerprint(bus1_has_gra=True):
  return {0: {}, 1: {MSG_GRA_NEU: 4} if bus1_has_gra else {}, 2: {}}


def test_armed_only_with_param_and_no_acc_module():
  assert _moduleless_acc_available(FakeParams(True), NO_RADAR, _fingerprint())


def test_param_off_never_arms():
  assert not _moduleless_acc_available(FakeParams(False), NO_RADAR, _fingerprint())


def test_car_with_acc_module_never_arms():
  assert not _moduleless_acc_available(FakeParams(True), 0, _fingerprint())


def test_cc_only_car_that_has_a_radar_never_arms():
  assert not _moduleless_acc_available(FakeParams(True), CC_ONLY_WITH_RADAR, _fingerprint())


def test_car_without_acc_capable_stalk_never_arms():
  assert not _moduleless_acc_available(FakeParams(True), NO_RADAR, _fingerprint(bus1_has_gra=False))


def test_unknown_param_key_never_arms():
  assert not _moduleless_acc_available(UnknownKeyParams(), NO_RADAR, _fingerprint())


def test_gate_reads_only_its_own_param():
  params = FakeParams(True)
  _moduleless_acc_available(params, NO_RADAR, _fingerprint())
  assert params.reads == ["VwPqModulelessAcc"]


def test_safety_flag_matches_the_c_layer():
  assert VolkswagenSafetyFlags.PQ_MODULELESS.value == 2048
