"""Startup policy for settings that are no longer user configurable."""


def apply_ui_defaults(params) -> None:
  for key, value in {
    "DashcamEnabled": True,
    "IQAutoUnits": True,
    "IQDynamicMode": False,
    "IQExpandedStatus": True,
    "IQSteerEffortArc": True,
    "IQBlinkerIndicators": True,
    "IQAccelMeter": False,
    "IQEdgeGuard": False,
    "LaneChangeContinuous": False,
    "AolUnifiedEngagementMode": True,
  }.items():
    if params.get_bool(key) != value:
      params.put_bool(key, value)
  for key, value in {"OnroadScreenOffBrightness": 0, "IQLaneTurnValue": 19.0}.items():
    if params.get(key, return_default=True) != value:
      params.put(key, value)
  params.remove("NeuralNetworkFeedForward")
  timer = int(params.get("IQLaneChangeTimer", return_default=True))
  if timer not in (0, 1):
    params.put("IQLaneChangeTimer", int(timer > 0))
  if params.get("IQSpeedAssistMode", return_default=True) not in (1, 3):
    params.put("IQSpeedAssistMode", 1)
