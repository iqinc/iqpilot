"""
Copyright © IQ.Lvbs, apart of Project Teal Lvbs, All Rights Reserved, licensed under https://konn3kt.com/tos/
"""
from iqpilot.common.constants import CV

ACCEL_BOOST_MAX = 1.0
ACCEL_BOOST_RATE = 0.1
ACCEL_BOOST_MIN_SPEED = 10 * CV.MPH_TO_MS


class AccelBoost:
  def __init__(self, enabled: bool, dt: float):
    self.enabled = enabled
    self.dt = dt
    self.value = 0.0

  def update(self, engaged: bool, v_ego: float, gas_pressed: bool) -> float:
    if not self.enabled or not engaged:
      self.value = 0.0
    elif v_ego < ACCEL_BOOST_MIN_SPEED:
      self.value = max(0.0, self.value - ACCEL_BOOST_RATE * self.dt)
    elif gas_pressed:
      self.value = min(ACCEL_BOOST_MAX, self.value + ACCEL_BOOST_RATE * self.dt)
    return self.value
