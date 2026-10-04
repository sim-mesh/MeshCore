#include "SimSerialPort.h"

#include <cstring>

namespace {

const uint8_t MAGIC_BYTES[4] = {0xF5, 'S', 'G', 0x01};
const uint16_t COMMAND_MAX = 4096;
const unsigned long STALL_MS = 1000;     // no progress this long abandons a frame
const size_t REPLY_MAX = 1024;

}

void SimConsole::begin(unsigned long baud, uint16_t config) {
  PosixSerial::begin(baud, config);
  _framed = simConsoleCommand != nullptr;
  if (_framed) {
    static const char marker[] = "serial: framed rpc v1\r\n";
    PosixSerial::write((const uint8_t*)marker, sizeof(marker) - 1);
  }
}

void SimConsole::pump() {
  if (!_framed) return;
  if (_state != IDLE && millis() - _last_byte > STALL_MS) {
    if (_state == MAGIC) passHeld();
    _state = IDLE;
  }
  while (PosixSerial::available() > 0) {
    take((uint8_t)PosixSerial::read());
    _last_byte = millis();
  }
}

void SimConsole::passHeld() {
  _pass += _held;
  _held.clear();
  _matched = 0;
}

void SimConsole::take(uint8_t c) {
  switch (_state) {
    case IDLE:
      if (c == MAGIC_BYTES[0]) {
        _held.assign(1, (char)c);
        _matched = 1;
        _state = MAGIC;
      } else {
        _pass += (char)c;
      }
      break;
    case MAGIC:
      if (c == MAGIC_BYTES[_matched]) {
        _held += (char)c;
        if (++_matched == sizeof(MAGIC_BYTES)) {
          _held.clear();
          _matched = 0;
          _state = ID;
        }
      } else {
        passHeld();       // not a frame: what was held belongs to the console
        _state = IDLE;
        take(c);
      }
      break;
    case ID:
      _id = c;
      _state = LEN_HI;
      break;
    case LEN_HI:
      _len = (uint16_t)c << 8;
      _state = LEN_LO;
      break;
    case LEN_LO:
      _len |= c;
      _got = 0;
      _payload.clear();
      if (_len == 0) {
        run();
      } else {
        _state = PAYLOAD;
      }
      break;
    case PAYLOAD:
      if (_got < COMMAND_MAX) _payload += (char)c;
      if (++_got == _len) run();
      break;
  }
}

void SimConsole::run() {
  _state = IDLE;
  static char reply[REPLY_MAX];
  reply[0] = 0;
  if (_len > COMMAND_MAX) {
    strcpy(reply, "rpc: command over 4096 bytes");
  } else {
    static char line[COMMAND_MAX + 1];
    size_t n = _payload.size();
    while (n > 0 && (_payload[n - 1] == '\n' || _payload[n - 1] == '\r')) n--;
    memcpy(line, _payload.data(), n);
    line[n] = 0;
    simConsoleCommand(line, reply);
  }
  reply[REPLY_MAX - 1] = 0;
  size_t n = strlen(reply);
  std::string frame((const char*)MAGIC_BYTES, sizeof(MAGIC_BYTES));
  frame += (char)_id;
  frame += (char)(n >> 8);
  frame += (char)(n & 0xFF);
  frame.append(reply, n);
  PosixSerial::write((const uint8_t*)frame.data(), frame.size());
}

int SimConsole::available() {
  if (!_framed) return PosixSerial::available();
  pump();
  return (int)_pass.size();
}

int SimConsole::peek() {
  if (!_framed) return PosixSerial::peek();
  pump();
  return _pass.empty() ? -1 : (uint8_t)_pass[0];
}

int SimConsole::read() {
  if (!_framed) return PosixSerial::read();
  pump();
  if (_pass.empty()) return -1;
  int c = (uint8_t)_pass[0];
  _pass.erase(0, 1);
  return c;
}
