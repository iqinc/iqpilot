#include "tools/cabana/streams/devicestream.h"

#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <memory>
#include <string>
#include <thread>
#include <unistd.h>
#include <sys/wait.h>

#include "cereal/services.h"

#include <QButtonGroup>
#include <QFormLayout>
#include <QMessageBox>
#include <QRadioButton>

#include "tools/cabana/utils/util.h"

// DeviceStream

DeviceStream::DeviceStream(QObject *parent, Mode mode, QString address)
    : mode_(mode), address_(address.isEmpty() ? "127.0.0.1" : address), LiveStream(parent) {
}

DeviceStream::~DeviceStream() {
  stop();
  stopBridge();
}

void DeviceStream::stopBridge() {
  if (bridge_pid <= 0) return;

  ::kill(bridge_pid, SIGTERM);
  for (int i = 0; i < 30; ++i) {
    int status = 0;
    pid_t r = ::waitpid(bridge_pid, &status, WNOHANG);
    if (r == bridge_pid || (r < 0 && errno == ECHILD)) {
      bridge_pid = -1;
      return;
    }
    usleep(100000);  // 100ms, up to ~3s
  }
  ::kill(bridge_pid, SIGKILL);
  ::waitpid(bridge_pid, nullptr, 0);
  bridge_pid = -1;
}

void DeviceStream::start() {
  if (mode_ == Mode::Bridge) {
    stopBridge();
    const std::string path = (std::filesystem::path(QCoreApplication::applicationDirPath().toStdString()) /
                              "../../cereal/messaging/bridge").lexically_normal().string();
    const std::string addr = address_.toStdString();
    const char *can_filter = "/\"can/\"";

    // Self-pipe: write end is CLOEXEC so it closes on successful exec. If exec
    // fails, the child writes errno and the parent aborts stream start.
    int err_pipe[2] = {-1, -1};
    if (::pipe(err_pipe) != 0) {
      QMessageBox::warning(nullptr, tr("Error"),
                           tr("Failed to start bridge: %1").arg(QString::fromLocal8Bit(strerror(errno))));
      return;
    }

    pid_t pid = ::fork();
    if (pid == 0) {
      ::close(err_pipe[0]);
      ::fcntl(err_pipe[1], F_SETFD, FD_CLOEXEC);
      execl(path.c_str(), path.c_str(), addr.c_str(), can_filter, static_cast<char *>(nullptr));
      const int err = errno;
      (void)!::write(err_pipe[1], &err, sizeof(err));
      _exit(127);
    }

    ::close(err_pipe[1]);
    if (pid < 0) {
      ::close(err_pipe[0]);
      QMessageBox::warning(nullptr, tr("Error"),
                           tr("Failed to start bridge: %1").arg(QString::fromLocal8Bit(strerror(errno))));
      return;
    }

    int exec_errno = 0;
    const ssize_t n = ::read(err_pipe[0], &exec_errno, sizeof(exec_errno));
    ::close(err_pipe[0]);
    if (n == static_cast<ssize_t>(sizeof(exec_errno))) {
      // Child failed to exec; reap and surface the error.
      int status = 0;
      ::waitpid(pid, &status, 0);
      QMessageBox::warning(nullptr, tr("Error"),
                           tr("Failed to start bridge: %1").arg(QString::fromLocal8Bit(strerror(exec_errno))));
      return;
    }

    bridge_pid = pid;
  }

  LiveStream::start();
}

void DeviceStream::streamThread() {
  // Bridge mode republishes into local msgq, so only the direct Zmq mode talks ZMQ.
  // (Upstream sets ZMQ=1 for its bridge path too, which reads nothing — the bridge
  // publishes to msgq.)
  mode_ == Mode::Zmq ? setenv("ZMQ", "1", 1) : unsetenv("ZMQ");
  const std::string address = mode_ == Mode::Zmq ? address_.toStdString() : "127.0.0.1";

  std::unique_ptr<Context> context(Context::create());
  std::unique_ptr<SubSocket> sock(SubSocket::create(context.get(), "can", address, false, true, services.at("can").queue_size));
  assert(sock != NULL);
  // run as fast as messages come in
  while (!exit_) {
    std::unique_ptr<Message> msg(sock->receive(true));
    if (!msg) {
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
      continue;
    }
    handleEvent(kj::ArrayPtr<capnp::word>((capnp::word*)msg->getData(), msg->getSize() / sizeof(capnp::word)));
  }
}

// OpenDeviceWidget

OpenDeviceWidget::OpenDeviceWidget(QWidget *parent) : AbstractOpenStreamWidget(parent) {
  QRadioButton *msgq = new QRadioButton(tr("MSGQ"));
  QRadioButton *zmq = new QRadioButton(tr("ZMQ"));
  QRadioButton *bridge = new QRadioButton(tr("Bridge"));
  zmq->setToolTip(tr("Subscribe directly to a ZMQ 'can' publisher: a device running "
                     "cereal/messaging/bridge, or konn3kt_canproxy.py on 127.0.0.1."));
  bridge->setToolTip(tr("Run cereal/messaging/bridge locally against the device and read msgq."));
  ip_address = new QLineEdit(this);
  ip_address->setPlaceholderText(tr("Enter device Ip Address"));
  ip_address->setValidator(new IpAddressValidator(this));

  group = new QButtonGroup(this);
  group->addButton(msgq, static_cast<int>(DeviceStream::Mode::Msgq));
  group->addButton(zmq, static_cast<int>(DeviceStream::Mode::Zmq));
  group->addButton(bridge, static_cast<int>(DeviceStream::Mode::Bridge));

  QFormLayout *form_layout = new QFormLayout(this);
  form_layout->addRow(msgq);
  form_layout->addRow(zmq, ip_address);
  form_layout->addRow(bridge);
  QObject::connect(group, qOverload<QAbstractButton *, bool>(&QButtonGroup::buttonToggled), [=](QAbstractButton *button, bool checked) {
    if (checked) ip_address->setEnabled(button != msgq);
  });
  zmq->setChecked(true);
}

AbstractStream *OpenDeviceWidget::open() {
  auto mode = static_cast<DeviceStream::Mode>(group->checkedId());
  return new DeviceStream(qApp, mode, mode == DeviceStream::Mode::Msgq ? "" : ip_address->text());
}
