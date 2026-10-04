#pragma once

#include <MeshCore.h>
#include <Arduino.h>

#ifndef PORTDUINO_BATT_MILLIVOLTS
  #define PORTDUINO_BATT_MILLIVOLTS  4200
#endif

/**
 * MeshCore as a Linux process on Portduino (Meshtastic's Arduino API for Linux).
 *
 * reboot() ends the process with status 0: whatever runs it (a supervisor, a
 * service manager) starts it again on the same state directory.
 *
 * idle(max_ms) is rnode_idle(max_ms): a wait of at most max_ms that the radio's
 * DIO1 or rnode_wake() ends early. Both are weak functions here, a timed wait
 * that only rnode_wake() ends, so a radio backend linked into the program
 * supplies its own.
 */
class PortduinoBoard : public mesh::MainBoard {
public:
  virtual void begin();

  uint16_t getBattMilliVolts() override { return PORTDUINO_BATT_MILLIVOLTS; }
  const char* getManufacturerName() const override { return "Portduino"; }
  void reboot() override;
  uint8_t getStartupReason() const override { return BD_STARTUP_NORMAL; }
  void idle(uint32_t max_ms) override;

protected:
  // The boot banner, on stdout: one "key: value" line each.
  virtual void printBanner();
};

void rnode_idle(uint32_t max_ms);
void rnode_wake();
