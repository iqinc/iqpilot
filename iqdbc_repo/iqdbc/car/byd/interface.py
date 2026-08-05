from iqdbc.car import get_safety_config, structs
from iqdbc.car.interfaces import CarInterfaceBase
from iqdbc.car.byd.carcontroller import CarController
from iqdbc.car.byd.carstate import CarState
from iqdbc.car.byd.values import BydFlags, BydSafetyFlags


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController

  @staticmethod
  def _get_params(ret: structs.CarParams, candidate, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    ret.brand = "byd"

    ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.byd)]

    ret.steerControlType = structs.CarParams.SteerControlType.angle
    ret.steerActuatorDelay = 0.1
    ret.steerLimitTimer = 0.4

    # the Veoneer tracks live on a private CAN-FD pair that the BYD-6 harness jumpers straight
    # through, so the panda never sees them
    ret.radarUnavailable = True

    # Two harness types exist for this car, and they differ in what can be filtered:
    #
    #   camera harness  - the relay only intercepts the MPC camera. 0x1E2/0x316 are camera
    #                     frames so lateral works, but the ADAS/ACC ECU sits on the chassis bus
    #                     in front of the relay: its 0x32E cannot be blocked and openpilot would
    #                     contend with the stock ACC on the same address. Stock long only.
    #   gateway harness - the ACC ECU is behind the relay, so 0x32E is filterable and openpilot
    #                     longitudinal is possible.
    #
    # These CANNOT be told apart from the fingerprint: fingerprinting runs with the relay
    # closed, which ties bus 2 to bus 0, so bus 2 shows the whole car on either harness. The
    # difference is only observable once the relay opens, which is after CarParams is fixed.
    # Measured on a camera harness with the relay open: bus 2 carries 11 camera addresses and
    # neither 0x32D nor 0x32E is among them.
    #
    # So default to the camera harness and keep longitudinal off. Setting GATEWAY_HARNESS is an
    # explicit opt-in that must not be inferred - see BYD_SEALION7_PORT_PLAN.md.
    gateway_harness = bool(ret.flags & BydFlags.GATEWAY_HARNESS)

    ret.alphaLongitudinalAvailable = gateway_harness
    if alpha_long and gateway_harness:
      ret.openpilotLongitudinalControl = True
      ret.safetyConfigs[0].safetyParam |= BydSafetyFlags.LONG_CONTROL.value

      ret.longitudinalActuatorDelay = 0.5  # the IPB closes its own loop
      ret.vEgoStarting = 0.3
      ret.stopAccel = -0.5
      ret.startAccel = 1.5
      # without this longcontrol goes stopping -> pid directly and neither the startAccel kick
      # nor the STANDSTILL_RESUME pulse ever fires
      ret.startingState = True

      # ACCEL_CMD is feedforward; high feedback gain on a 0.5s-lag actuator winds up the
      # integrator before the car responds
      ret.longitudinalTuning.kpBP = [0.0, 5.0, 35.0]
      ret.longitudinalTuning.kpV = [0.5, 0.4, 0.3]
      ret.longitudinalTuning.kiBP = [0.0, 35.0]
      ret.longitudinalTuning.kiV = [0.03, 0.02]

    return ret

  @staticmethod
  def _get_params_iq(stock_cp: structs.CarParams, ret: structs.IQCarParams, candidate, fingerprint,
                     car_fw, alpha_long: bool, is_release_iq: bool, docs: bool) -> structs.IQCarParams:
    if stock_cp.openpilotLongitudinalControl:
      ret.longitudinalStoppingSpeedOverride = 0.3

    return ret
