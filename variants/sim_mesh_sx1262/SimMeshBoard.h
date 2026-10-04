#pragma once

#include <helpers/portduino/PortduinoBoard.h>

/**
 * A station of sim-mesh: a Portduino board whose SX1262 is sim-mesh's
 * virtual chip, and whose banner says which station it is.
 */
class SimMeshBoard : public PortduinoBoard {
public:
  const char* getManufacturerName() const override { return "sim-mesh SX1262"; }

protected:
  void printBanner() override;
};
