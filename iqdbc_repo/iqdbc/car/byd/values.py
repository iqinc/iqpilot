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
    390,  # deg
    ([], []),
    ([], []),

    MAX_LATERAL_ACCEL=ISO_LATERAL_ACCEL + (ACCELERATION_DUE_TO_GRAVITY * AVERAGE_ROAD_ROLL),
    MAX_LATERAL_JERK=3.0 + (ACCELERATION_DUE_TO_GRAVITY * AVERAGE_ROAD_ROLL),

    # deg/20ms. EPS faults at 12 at a standstill; 5 caused command spikes, 2 under-tracked
    # sharp curves. Stock Veoneer max is 4.8.
    MAX_ANGLE_RATE=3,
  )

  # STEERING_TORQUE.DRIVER_TORQUE units (Nm). PROVISIONAL, re-derive in bring-up B8.
  STEER_DRIVER_OVERRIDE = 3.0
  STEER_DRIVER_DISENGAGE = 8.0

  # comfort envelope, inside the safety cap of -3.5..+2.0
  ACCEL_MIN = -3.0
  ACCEL_MAX = 1.5

  JERK_UP = 2.5
  JERK_UP_LAUNCH = 4.0  # below 2 m/s, to beat the ~0.5s IPB lag off the line
  JERK_DOWN = 5.0


class BydSafetyFlags(IntFlag):
  LONG_CONTROL = 1


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
