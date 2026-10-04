#pragma once

#include <Arduino.h>
#include <deque>
#include <mutex>
#include <string>

/**
 * A serial port on a pair of descriptors, 0 and 1 by default: reads never
 * block, writes are unbuffered. A terminal on the input descriptor is put in
 * raw mode, so bytes arrive as a UART gives them.
 *
 * With MESHCORE_SERIAL_LISTEN=addr:port in the environment it is instead the
 * one TCP connection accepted on a listener bound there: while no client is
 * connected nothing is available and writes are dropped, and a client that
 * goes makes room for the next.
 *
 * A reader thread waits on the descriptor (or the listener), keeps what
 * arrives for read(), and calls rnode_wake(), so input ends the board's
 * idle() at once.
 */
class PosixSerial : public HardwareSerial {
public:
  PosixSerial(int in_fd = 0, int out_fd = 1) : _in(in_fd), _out(out_fd) { }

  void begin(unsigned long baud) override { begin(baud, SERIAL_8N1); }
  void begin(unsigned long baud, uint16_t config) override;
  void end() override { }
  int available() override;
  int peek() override;
  int read() override;
  void flush() override { }
  size_t write(uint8_t c) override { return write(&c, 1); }
  size_t write(const uint8_t* buf, size_t len) override;
  using Print::write;
  operator bool() override { return true; }

  // A client is connected: always, on descriptors.
  bool connected() const { return _listen.empty() || _client >= 0; }

private:
  void readDescriptor();
  void serveListener();
  void received(const uint8_t* buf, size_t len);

  int _in, _out;
  std::string _listen;
  int _listener = -1;
  volatile int _client = -1;
  bool _started = false;
  std::mutex _rx_mu, _tx_mu;
  std::deque<uint8_t> _rx;
};
