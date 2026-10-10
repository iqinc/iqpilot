#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "common/params.h"
#include "selfdrive/pandad/panda.h"

void pandad_main_thread(std::vector<std::string> serials);

// marginal harness ignition lines read low for up to 2.3 s while driving once the CAN transceivers power up
constexpr uint64_t IGNITION_LINE_HOLD_NS = 5ULL * 1000ULL * 1000ULL * 1000ULL;

class IgnitionLineHold {
public:
  bool update(bool line, uint64_t now_ns) {
    if (line) {
      last_on_ns_ = now_ns;
      seen_on_ = true;
      return true;
    }
    return seen_on_ && (now_ns - last_on_ns_) < IGNITION_LINE_HOLD_NS;
  }

private:
  uint64_t last_on_ns_ = 0;
  bool seen_on_ = false;
};

// deprecated devices
static const std::vector<cereal::PandaState::PandaType> SUPPORTED_PANDA_TYPES = {
  cereal::PandaState::PandaType::RED_PANDA,
  cereal::PandaState::PandaType::TRES,
  cereal::PandaState::PandaType::CUATRO,
};


class PandaSafety {
public:
  PandaSafety(const std::vector<Panda *> &pandas, const std::string &params_path = {}) : pandas_(pandas), params_(params_path) {}
  void configureSafetyMode(bool is_onroad);
  bool getOffroadMode();

private:
  void updateMultiplexingMode();
  struct DeviceSafety {
    cereal::CarParams::SafetyModel model = cereal::CarParams::SafetyModel::SILENT;
    uint16_t parameter = 0;
  };

  struct SafetyConfiguration {
    uint16_t alternative_experience;
    uint16_t iq_flags;
    std::vector<DeviceSafety> devices;
  };

  std::optional<SafetyConfiguration> readSafetyConfiguration();
  void applySafetyConfiguration(const SafetyConfiguration &configuration);

  bool initialized_ = false;
  bool log_once_ = false;
  bool safety_configured_ = false;
  bool prev_obd_multiplexing_ = false;
  std::vector<Panda *> pandas_;
  Params params_;
};
