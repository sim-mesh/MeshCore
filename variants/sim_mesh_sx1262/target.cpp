#include <Arduino.h>
#include <SPI.h>
#include <sys/random.h>
#include <helpers/portduino/WholeFrameHal.h>
#include "target.h"

SimMeshBoard board;
SimConsole SimSerialPort;

static WholeFrameHal hal(SPI);
RADIO_CLASS radio = new Module(&hal, P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY);
WRAPPER_CLASS radio_driver(radio, board);

PortduinoRTCClock rtc_clock;
SensorManager sensors;

// sim-mesh's radio/portduino library: the station's link to the ether, and
// the chip on SPI and its pins.
void native_radio_backend_init();

// Before setup(), so the link is open before anything waits on time.
void portduinoSetup() {
  native_radio_backend_init();
}

bool radio_init() {
  return radio.std_init();
}

namespace {

// The C library's randomness: in a virtual-time run sim-mesh keys it by the
// run's seed and the node, so identities are distinct and reproducible.
class GetrandomRNG : public mesh::RNG {
public:
  void random(uint8_t* dest, size_t sz) override {
    size_t done = 0;
    while (done < sz) {
      ssize_t n = getrandom(dest + done, sz - done, 0);
      if (n > 0) done += n;
    }
  }
};

}

mesh::LocalIdentity radio_new_identity() {
  GetrandomRNG rng;
  return mesh::LocalIdentity(&rng);
}
