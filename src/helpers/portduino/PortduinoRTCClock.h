#pragma once

#include <Mesh.h>
#include <time.h>

/**
 * The host's clock, through time(). Setting it moves this clock alone, by an
 * offset; the host's own is never changed.
 */
class PortduinoRTCClock : public mesh::RTCClock {
  int64_t _offset = 0;

public:
  uint32_t getCurrentTime() override { return (uint32_t)((int64_t)time(nullptr) + _offset); }
  void setCurrentTime(uint32_t t) override { _offset = (int64_t)t - (int64_t)time(nullptr); }
};
