#include "PosixSerial.h"
#include "PortduinoBoard.h"

#include <logging.h>
#include <arpa/inet.h>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <termios.h>
#include <thread>
#include <unistd.h>

void PosixSerial::begin(unsigned long, uint16_t) {
  if (_started) return;
  _started = true;

  const char* listen_at = getenv("MESHCORE_SERIAL_LISTEN");
  if (!listen_at || !*listen_at) {
    // A terminal passes bytes as a UART does: no line editing, no echo, CR as CR.
    struct termios t;
    if (isatty(_in) && tcgetattr(_in, &t) == 0) {
      t.c_iflag &= ~(IGNBRK | BRKINT | PARMRK | ISTRIP | INLCR | IGNCR | ICRNL | IXON);
      t.c_lflag &= ~(ECHO | ECHONL | ICANON | ISIG | IEXTEN);
      t.c_cc[VMIN] = 1;
      t.c_cc[VTIME] = 0;
      tcsetattr(_in, TCSANOW, &t);
    }
    std::thread([this] { readDescriptor(); }).detach();
    return;
  }

  _listen = listen_at;
  size_t colon = _listen.rfind(':');
  struct sockaddr_in sa = {};
  sa.sin_family = AF_INET;
  if (colon == std::string::npos || inet_pton(AF_INET, _listen.substr(0, colon).c_str(), &sa.sin_addr) != 1) {
    log_e("serial: MESHCORE_SERIAL_LISTEN=%s is not addr:port", listen_at);
    exit(1);
  }
  sa.sin_port = htons((uint16_t)atoi(_listen.c_str() + colon + 1));

  _listener = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
  int on = 1;
  setsockopt(_listener, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));
  if (_listener < 0 || bind(_listener, (struct sockaddr*)&sa, sizeof(sa)) != 0 || ::listen(_listener, 1) != 0) {
    log_e("serial: cannot listen on %s: %s", listen_at, strerror(errno));
    exit(1);
  }
  log_i("serial: listening on %s", listen_at);
  std::thread([this] { serveListener(); }).detach();
}

void PosixSerial::received(const uint8_t* buf, size_t len) {
  {
    std::lock_guard<std::mutex> lock(_rx_mu);
    _rx.insert(_rx.end(), buf, buf + len);
  }
  rnode_wake();
}

void PosixSerial::readDescriptor() {
  uint8_t buf[256];
  for (;;) {
    ssize_t n = ::read(_in, buf, sizeof(buf));
    if (n > 0) {
      received(buf, n);
    } else if (n == 0 || errno != EINTR) {
      return;     // the console has closed
    }
  }
}

void PosixSerial::serveListener() {
  uint8_t buf[256];
  for (;;) {
    int fd = accept4(_listener, nullptr, nullptr, SOCK_CLOEXEC);
    if (fd < 0) {
      if (errno == EINTR || errno == ECONNABORTED) continue;
      log_e("serial: accept on %s: %s", _listen.c_str(), strerror(errno));
      return;
    }
    int on = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &on, sizeof(on));
    {
      std::lock_guard<std::mutex> lock(_rx_mu);
      _rx.clear();
    }
    _client = fd;
    for (;;) {
      ssize_t n = ::recv(fd, buf, sizeof(buf), 0);
      if (n > 0) {
        received(buf, n);
      } else if (n == 0 || errno != EINTR) {
        break;
      }
    }
    std::lock_guard<std::mutex> lock(_tx_mu);
    _client = -1;
    ::close(fd);
  }
}

int PosixSerial::available() {
  std::lock_guard<std::mutex> lock(_rx_mu);
  return (int)_rx.size();
}

int PosixSerial::peek() {
  std::lock_guard<std::mutex> lock(_rx_mu);
  return _rx.empty() ? -1 : _rx.front();
}

int PosixSerial::read() {
  std::lock_guard<std::mutex> lock(_rx_mu);
  if (_rx.empty()) return -1;
  int c = _rx.front();
  _rx.pop_front();
  return c;
}

size_t PosixSerial::write(const uint8_t* buf, size_t len) {
  std::lock_guard<std::mutex> lock(_tx_mu);
  int fd = _listen.empty() ? _out : _client;
  if (fd < 0) return len;     // no client: dropped
  size_t done = 0;
  while (done < len) {
    ssize_t n = _listen.empty() ? ::write(fd, buf + done, len - done)
                                : ::send(fd, buf + done, len - done, MSG_NOSIGNAL);
    if (n < 0) {
      if (errno == EINTR) continue;
      break;
    }
    done += n;
  }
  return len;
}
