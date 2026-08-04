from iqdbc.car.structs import CarParams
from iqdbc.car.byd.values import CAR

Ecu = CarParams.Ecu

# Placeholder until a real car is captured in bring-up B2. Ecu.engine is essential, so a version
# no car reports keeps the platform from ever exact-matching.
#
# Do NOT replace this with an empty ECU dict: match_fw_to_car_exact only invalidates a platform
# when an expected version fails to match, so an empty dict leaves the platform a candidate for
# every car on the road.
#
# Until populated, fingerprint explicitly: FINGERPRINT=BYD_SEALION_7
#
# ECUs answering 0xF195 on this platform: 0x704 MPC camera (non-essential), 0x782 brake/IPB,
# 0x783 EPS, 0x7f2 forward radar, 0x7e0 drive unit, 0x7f1 SRS airbag.
FW_VERSIONS = {
  CAR.BYD_SEALION_7: {
    (Ecu.engine, 0x7e0, None): [
      b'PLACEHOLDER_UNTIL_CAPTURED',
    ],
  },
}
