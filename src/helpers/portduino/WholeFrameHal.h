#pragma once

#include <RadioLib.h>
#include <string.h>

/**
 * RadioLib's Arduino HAL, handing each SPI transaction to the bus as one
 * transfer. RadioLib assembles a transaction whole and passes it to
 * spiTransfer between chip select going low and going high; the Arduino HAL
 * then sends it a byte per call. A Linux SPI device (spidev) drops chip select
 * between calls, so it wants the transaction in one: this makes it one
 * in-place SPI.transfer(buf, len).
 */
class WholeFrameHal : public ArduinoHal {
public:
  explicit WholeFrameHal(SPIClass& spi, SPISettings settings = RADIOLIB_DEFAULT_SPI_SETTINGS)
    : ArduinoHal(spi, settings) { }

  void spiTransfer(uint8_t* out, size_t len, uint8_t* in) override {
    if (in != out) memcpy(in, out, len);
    spi->transfer(in, len);
  }

  // RadioLib waits a microsecond after each transaction before it reads BUSY. Reading a pin
  // from Linux takes longer than that, so the read itself is the wait; a sleep would be a
  // scheduler round trip per transaction.
  void delayMicroseconds(RadioLibTime_t us) override {
    if (us > 1) ArduinoHal::delayMicroseconds(us);
  }
};
