#pragma once

#include <helpers/portduino/PosixSerial.h>
#include <string>

/**
 * The station's console: a PosixSerial that, in a build with a command line
 * (simConsoleCommand linked in), takes framed RPC frames out of what it reads
 * before anything else sees it, runs each frame's command line and frames the
 * reply back (spangap-core's docs/framed-rpc.md):
 *
 *   station → sim-mesh   "serial: framed rpc v1"           at begin(), once
 *   sim-mesh → station   F5 53 47 01 <id> <len:2> <command line>
 *   station → sim-mesh   F5 53 47 01 <id> <len:2> <reply>
 *
 * Every other byte passes through as it came, so a person typing at the
 * console and a driver asking over frames never interleave.
 */
class SimConsole : public PosixSerial {
public:
  void begin(unsigned long baud) override { begin(baud, SERIAL_8N1); }
  void begin(unsigned long baud, uint16_t config) override;
  int available() override;
  int peek() override;
  int read() override;

private:
  enum State { IDLE, MAGIC, ID, LEN_HI, LEN_LO, PAYLOAD };

  void pump();
  void take(uint8_t c);
  void passHeld();
  void run();

  bool _framed = false;
  State _state = IDLE;
  uint8_t _matched = 0;
  uint8_t _id = 0;
  uint16_t _len = 0;
  uint16_t _got = 0;
  unsigned long _last_byte = 0;
  std::string _held, _payload, _pass;
};

/**
 * One command line run on the build's command line, its reply in `reply`
 * (at least 1024 bytes). Defined by a build that has one; the console speaks
 * no frames without it.
 */
extern bool simConsoleCommand(char* line, char* reply) __attribute__((weak));
