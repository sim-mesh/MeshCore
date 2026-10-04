#include "PortduinoBoard.h"
#include "PortduinoMeshFS.h"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <pthread.h>
#include <sys/time.h>
#include <unistd.h>

#ifndef MESHCORE_BUILD_DESCRIBE
  #define MESHCORE_BUILD_DESCRIBE  "unknown"
#endif
#ifndef MESHCORE_BUILD_COMMIT
  #define MESHCORE_BUILD_COMMIT  "unknown"
#endif
#ifndef MESHCORE_BUILD_DATE
  #define MESHCORE_BUILD_DATE  __DATE__ " " __TIME__
#endif

void PortduinoBoard::begin() {
  printBanner();
  fflush(stdout);
}

void PortduinoBoard::printBanner() {
  printf("MeshCore: %s\n", MESHCORE_BUILD_DESCRIBE);
  printf("commit: %s\n", MESHCORE_BUILD_COMMIT);
  printf("built: %s\n", MESHCORE_BUILD_DATE);
  printf("board: %s\n", getManufacturerName());
  printf("filesystem: %s\n", MeshFS.root());
}

void PortduinoBoard::reboot() {
  // Threads blocked on descriptors are left as they are: no destructors run.
  fflush(nullptr);
  _exit(0);
}

void PortduinoBoard::idle(uint32_t max_ms) {
  rnode_idle(max_ms);
}

namespace {
pthread_mutex_t s_mu = PTHREAD_MUTEX_INITIALIZER;
pthread_cond_t s_cv = PTHREAD_COND_INITIALIZER;
bool s_woken;
}

void __attribute__((weak)) rnode_idle(uint32_t max_ms) {
  if (max_ms == 0) return;
  struct timeval now;
  gettimeofday(&now, nullptr);
  int64_t end_us = (int64_t)now.tv_sec * 1000000 + now.tv_usec + (int64_t)max_ms * 1000;
  struct timespec end = {(time_t)(end_us / 1000000), (long)(end_us % 1000000) * 1000};

  pthread_mutex_lock(&s_mu);
  while (!s_woken) {
    if (pthread_cond_timedwait(&s_cv, &s_mu, &end) == ETIMEDOUT) break;
  }
  s_woken = false;
  pthread_mutex_unlock(&s_mu);
}

void __attribute__((weak)) rnode_wake() {
  pthread_mutex_lock(&s_mu);
  s_woken = true;
  pthread_cond_signal(&s_cv);
  pthread_mutex_unlock(&s_mu);
}
