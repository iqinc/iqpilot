#pragma once

#include "tools/cabana/streams/livestream.h"

#include <sys/types.h>

// IQ.Pilot patch: upstream (#38484) folded the ZMQ path into "fork a local bridge and
// read msgq". iqpilot needs the direct ZMQ attach kept as a first-class mode, because
// tools/cabana/konn3kt_canproxy.py publishes a remote device's CAN onto a LOCAL ZMQ
// "can" socket and Cabana attaches to it — see that script's header for the topology.
// So the mode is explicit rather than inferred from whether an address was entered:
//
//   Msgq   - local msgq, no address                     (cabana running on the device)
//   Zmq    - ZMQ subscribe straight to <address>        (konn3kt_canproxy, or `bridge` on the device)
//   Bridge - fork cereal/messaging/bridge <address>,    (upstream's convenience path)
//            which ZMQ-subscribes there and republishes
//            to local msgq, then read msgq
class DeviceStream : public LiveStream {
  Q_OBJECT
public:
  enum class Mode { Msgq, Zmq, Bridge };

  DeviceStream(QObject *parent, Mode mode = Mode::Msgq, QString address = {});
  ~DeviceStream();
  inline std::string routeName() const override {
    return "Live Streaming From " + address_.toStdString();
  }

protected:
  void start() override;
  void streamThread() override;
  void stopBridge();
  pid_t bridge_pid = -1;
  const Mode mode_;
  const QString address_;
};

class OpenDeviceWidget : public AbstractOpenStreamWidget {
  Q_OBJECT

public:
  OpenDeviceWidget(QWidget *parent = nullptr);
  AbstractStream *open() override;

private:
  QLineEdit *ip_address;
  QButtonGroup *group;
};
