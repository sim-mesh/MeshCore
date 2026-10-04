#pragma once

#define RADIOLIB_STATIC_ONLY 1
#include <RadioLib.h>
#include <helpers/radiolib/RadioLibWrappers.h>
#include <helpers/radiolib/CustomSX1262Wrapper.h>
#include <helpers/SensorManager.h>
#include <helpers/portduino/PortduinoRTCClock.h>
#include "SimMeshBoard.h"
#include "SimSerialPort.h"

// MeshCore's Serial is the station's console, which reads, unlike Portduino's.
#define Serial SimSerialPort
extern SimConsole SimSerialPort;

extern SimMeshBoard board;
extern WRAPPER_CLASS radio_driver;
extern PortduinoRTCClock rtc_clock;
extern SensorManager sensors;

bool radio_init();
mesh::LocalIdentity radio_new_identity();
