from dataclasses import dataclass, field
from enum import IntFlag, StrEnum

from iqdbc.car import ACCELERATION_DUE_TO_GRAVITY, Bus, CarSpecs, DbcDict, PlatformConfig, Platforms, structs
from iqdbc.car.lateral import AngleSteeringLimits, AVERAGE_ROAD_ROLL, ISO_LATERAL_ACCEL
from iqdbc.car.docs_definitions import CarDocs, CarHarness, CarParts
from iqdbc.car.fw_query_definitions import FwQueryConfig, Request, StdQueries
from iqdbc.car.vin import Vin

Ecu = structs.CarParams.Ecu


class CarControllerParams:
  STEER_STEP = 2  # 50 Hz

  ANGLE_LIMITS: AngleSteeringLimits = AngleSteeringLimits(
    # 85 deg max command. The EPS faults out past ~90 deg (route 113: on a tight 11 km/h turn the
    # model wanted 360+ deg, openpilot drove the wheel past 90 and the ADAS latched for the rest of
    # the drive). 85 keeps openpilot's own command below the fault; tighter corners (5% of steering
    # frames want >90, all near full lock at 11-12 km/h) are handed to the driver. Was 390 (never
    # clamped).
    85.,  # deg
    # ANGLE_RATE_LIMIT_UP / DOWN: fast speed-interpolated rate curve (deg per 20 ms frame). Paired
    # with near-zero actuator delay and the 2 Hz command low-pass, the fast rate lets the command
    # track without the phase lag that oscillated the wheel; the low-pass keeps it smooth.
    ([0., 2., 5., 15.], [0.3, 1.0, 1.3, 1.0]),
    ([0., 2., 5., 15.], [0.5, 1.2, 1.5, 1.2]),

    # MUST mirror safety/lateral.h, which the panda enforces for every car; a tighter value here
    # only desyncs the two and makes the safety envelope untestable. Command smoothness is set by
    # the rate curve above, not by this.
    MAX_LATERAL_ACCEL=ISO_LATERAL_ACCEL + (ACCELERATION_DUE_TO_GRAVITY * AVERAGE_ROAD_ROLL),
    MAX_LATERAL_JERK=3.0 + (ACCELERATION_DUE_TO_GRAVITY * AVERAGE_ROAD_ROLL),

    # deg/20ms. The stock camera's own command never steps more than 1.30 deg/frame
    # (4783 engaged samples: p99 0.40, p99.9 0.80, max 1.30). At 2 we sat at the cap constantly
    # (route db: p90 = 2.0 deg/frame = 100 deg/s) and every disturbance produced a 100 deg/s
    # whip-crack that the driver felt as a sharp jerk and that de-actuated the EPS (17 of the
    # 10->9 drops landed right after a max-rate step). 1.0 keeps us under the stock max and above
    # its p99.9 (0.80), so turns still track but corrections are smooth.
    MAX_ANGLE_RATE=1.5,
  )
  # 1-pole low-pass on the steering command (Hz). This is the primary smoothing in the fast-rate /
  # near-zero-delay regime - it takes the residual oscillation ("barking") off stronger turns.
  STEER_LOWPASS_HZ = 2.0

  # Low-speed taper on the angle rate, in deg per STEER_STEP frame.
  #
  # The vehicle-model jerk limit scales as 1/v^2, so below a few m/s it stops binding and only
  # the flat MAX_ANGLE_RATE is left. The lateral planner is ill-conditioned at a standstill and
  # oscillates, and slewing the command at the full rate while the wheel is not moving walks the
  # EPS straight from state 9 to a latched 11. Measured 2026-08-05 at 0.29 m/s: the command swung
  # -5.9 to +2.4 deg in 220 ms against a stationary wheel and the EPS latched, taking LKAS with
  # it. A healthy engagement at 0.9 m/s held the command within 1.1 deg of measured.
  ANGLE_RATE_BP = [0.0, 2.0, 5.0]  # m/s
  ANGLE_RATE_V = [0.3, 1.0, 1.5]   # deg/frame, tops out under MAX_ANGLE_RATE

  # Below this the lateral planner is ill-conditioned and demands garbage angles (measured route
  # e3: 35 deg desired at 3 km/h with yawRate 0, car going straight). Rate-tapering only slowed the
  # swing - the command still ramped to 40+ deg. Below MIN_STEER_SPEED we do not chase the planner
  # at all; we hold the command on the measured wheel so it neither swings nor fights, and pick up
  # normal steering once past it. 2.0 m/s = 7.2 km/h, the speed where the VM jerk limit re-binds.
  # Speed gate with hysteresis: below MIN_STEER_SPEED openpilot does not actuate; it only resumes
  # once back above MIN_STEER_SPEED_RESUME. The band stops the gate chattering at ~7-8 km/h - a
  # single hard threshold at 2.0 m/s flipped on every 1 km/h speed wobble and toggled REQ (route
  # 116: 153 toggles = the "parkinsons" tremor).
  MIN_STEER_SPEED = 2.0         # m/s (7.2 km/h) - drop
  MIN_STEER_SPEED_RESUME = 2.8  # m/s (10 km/h) - resume

  # Angle gate with hysteresis: hand off (drop STEER_REQ) once the wheel is past MAX_STEER_ANGLE - a
  # tight turn heading for the ~90 deg EPS fault - and only re-engage once it comes back under
  # MAX_STEER_ANGLE_RESUME. Below the 85 deg command clamp so openpilot relaxes and the driver
  # finishes the corner; the resume band keeps it from chattering at the 80 deg edge.
  MAX_STEER_ANGLE = 80.0         # deg - drop
  MAX_STEER_ANGLE_RESUME = 65.0  # deg - resume

  # First-order low-pass on the commanded steering angle. DISABLED (1.0 = passthrough): it added
  # ~45 ms of loop delay that fed the high-speed weave without fixing the felt stutter (the stutter
  # is that weave, not the +-0.5 deg jitter this was chasing). The weave is addressed by
  # steerActuatorDelay instead. Re-enable a light value only if a real HF buzz remains after.
  ANGLE_FILTER_ALPHA = 0.5

  # STEERING_TORQUE.DRIVER_TORQUE thresholds, derived from a drive where openpilot actually
  # steered (route 0000000f, EPS state 10):
  #   |torque| while openpilot steered:  p50 1.2  p90 2.7  p95 3.3  p99 5.8  max 9.8
  #   |torque| while the human drove:    p50 0.2  p90 8.1  p95 17.2 p99 24.5 max 35.5
  # The old 3.0 sat below what openpilot generates while steering, so it tripped its own
  # override and dropped out within a few frames of every engage.
  # 12.0 sat inside the overlap between openpilot's own steering (max 9.8) and ordinary human
  # driving (p90 8.1, p95 17.2), so it tripped constantly on 13-15 Nm corrections. 18 is above
  # p95 and still well under a deliberate grab.
  # Override / grey-border threshold on DRIVER_EPS_TORQUE (STEER_MODULE_2 byte 2, raw 0-255, the
  # clean column sensor - see carstate). Normal openpilot steering keeps it near 0 and a route max
  # was 79; normal driver turns peak ~52. So 80 catches a real
  # takeover without tripping on openpilot's own steering. This finally lets the yield ride
  # steeringPressed (border-linked, as Gutek wanted) without the load-driven chatter of DRIVER_TORQUE.
  STEER_DRIVER_OVERRIDE = 80
  # Consecutive 0x1FC samples over the threshold before the override latches. Driver torque is
  # spiky: single frames touch 12+ with hands resting, and latching on one of them disengaged
  # constantly. MUST equal BYD_OVERRIDE_FRAMES in byd.h or the two latches desync.
  STEER_DRIVER_OVERRIDE_FRAMES = 5
  # Frames continuously back under the threshold before the override releases. Without this the
  # latch is permanent in practice: the car cycles CRUISE_STATE 1<->2 on its own and never
  # reaches 0, so normal manual driving pinned it on and engagement became impossible.
  # 100 frames at the 50 Hz 0x1FC rate = 2 s hands-settled.
  STEER_DRIVER_RELEASE_FRAMES = 100

  # |commanded - measured| that the stock camera runs while ICC steers (route
  # 0000007b--88dd577c32, 4796 engaged frames):
  #   p50 0.20  p95 1.00  p99 3.31  p99.9 10.61  max 19.70
  # So the EPS tolerates ~20 deg of transient tracking error. The old 6.0/5.0 pair was built on
  # the "EPS latches state 11 on divergence" model, which the stock capture refutes, and it was
  # self-defeating: the clamp parked the command in a 5-6 deg band that the bail then punished,
  # so every firm turn dropped STEER_REQ and chattered.
  #
  # INVARIANT: MAX_ANGLE_ERROR < ANGLE_ERROR_BAIL, or the clamp manufactures bail conditions.
  MAX_ANGLE_ERROR = 12.0  # deg, once actuating; just above stock p99.9

  # Hard bail: drop STEER_REQ once the wheel has genuinely run away from the command. Above the
  # stock maximum so it only catches a real runaway, never normal cornering. Deasserting REQ
  # walks the EPS 10 -> 9 cleanly, which is the safe exit; it re-arms on the next rising edge.
  ANGLE_ERROR_BAIL = 25.0  # deg

  # Drop STEER_REQ the instant driver torque spikes, no debounce. A violent jerk moves the
  # wheel faster than the angle-divergence bail can react (the EPS latched at ~12 deg before
  # 50 Hz control even saw 8), but torque spikes a few frames BEFORE the angle diverges.
  # Dropping REQ is safe and reversible (EPS 10 -> 9, re-arms on the next rising edge), so
  # unlike the disengage latch it needs no debounce. Also makes the wheel yield immediately
  # instead of fighting the driver until the override latch fires.
  TORQUE_BAIL = 20.0  # Nm, instantaneous

  # While the EPS is armed but NOT actuating (state 9) the wheel does not follow, so any command
  # offset just sits there as error until the EPS gives up and latches 11. Measured 2026-08-05:
  # 4.2 deg of standing error at 0.9 m/s in state 9 was enough. Keep the command on the wheel
  # until the EPS reports state 10, then let it depart normally.
  MAX_ANGLE_ERROR_INACTIVE = 1.0  # deg

  # comfort envelope, inside the safety cap of -3.5..+2.0
  ACCEL_MIN = -3.0
  ACCEL_MAX = 1.5

  JERK_UP = 2.5
  JERK_UP_LAUNCH = 4.0  # below 2 m/s, to beat the ~0.5s IPB lag off the line
  JERK_DOWN = 5.0


class BydSafetyFlags(IntFlag):
  LONG_CONTROL = 1


class BydFlags(IntFlag):
  # The ADAS/ACC ECU is behind the relay, so its 0x32E ACC_CMD can be blocked and replaced.
  # Set when ACC_CMD is fingerprinted on the camera-side bus.
  GATEWAY_HARNESS = 1


# addresses used to tell the two harness types apart
ACC_CMD_ADDR = 0x32E


class WMI(StrEnum):
  BYD_AUTO = "LGX"


class ModelYear(StrEnum):
  R_2024 = "R"
  S_2025 = "S"
  T_2026 = "T"


@dataclass
class BydCarDocs(CarDocs):
  package: str = "All"
  car_parts: CarParts = field(default_factory=CarParts.common([CarHarness.custom]))


@dataclass
class BydPlatformConfig(PlatformConfig):
  dbc_dict: DbcDict = field(default_factory=lambda: {Bus.pt: 'byd_sealion_7'})
  wmis: set[WMI] = field(default_factory=set)
  years: set[ModelYear] = field(default_factory=set)
  vds_prefixes: set[str] = field(default_factory=set)


class CAR(Platforms):
  BYD_SEALION_7 = BydPlatformConfig(
    [BydCarDocs("BYD Sealion 7 2024-25")],
    CarSpecs(mass=2090., wheelbase=2.93, steerRatio=16.0, centerToFrontRatio=0.44),
    wmis={WMI.BYD_AUTO},
    years={ModelYear.R_2024, ModelYear.S_2025, ModelYear.T_2026},
  )


def match_fw_to_car_fuzzy(live_fw_versions, vin, offline_fw_versions) -> set[str]:
  # VIN: LGX (WMI) + <VDS> + <year><plant><seq> (VIS). Matching on WMI + year alone would claim
  # every BYD of that year, so a platform only matches once its VDS prefix is known.
  vin_obj = Vin(vin)
  year = vin_obj.vis[:1]

  candidates = set()
  for platform in CAR:
    cfg = platform.config
    if not cfg.vds_prefixes or vin_obj.wmi not in cfg.wmis or year not in cfg.years:
      continue
    if any(vin_obj.vds.startswith(p) for p in cfg.vds_prefixes):
      candidates.add(platform)

  return {str(c) for c in candidates}


FW_QUERY_CONFIG = FwQueryConfig(
  # BYD ECUs NRC 0xF188 (openpilot's default) but answer 0xF195
  requests=[
    Request(
      [StdQueries.SUPPLIER_SOFTWARE_VERSION_REQUEST],
      [StdQueries.SUPPLIER_SOFTWARE_VERSION_RESPONSE],
      bus=0,
    ),
  ],
  # the MPC camera answers OBD DTC scans but not the bus-0 DID sweep
  non_essential_ecus={Ecu.fwdCamera: list(CAR)},
  match_fw_to_car_fuzzy=match_fw_to_car_fuzzy,
)


DBC = CAR.create_dbc_map()
