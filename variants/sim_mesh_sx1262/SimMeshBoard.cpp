#include "SimMeshBoard.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace {

const char* envOr(const char* name, const char* fallback) {
  const char* v = getenv(name);
  return v && *v ? v : fallback;
}

// A value of SIM_MESH_BOARD's flat JSON object, as written there.
std::string boardField(const char* json, const char* key) {
  std::string quoted = std::string("\"") + key + "\"";
  const char* at = json ? strstr(json, quoted.c_str()) : nullptr;
  if (!at) return "";
  at = strchr(at + quoted.size(), ':');
  if (!at) return "";
  at++;
  while (*at == ' ') at++;
  if (*at == '"') {
    const char* end = strchr(at + 1, '"');
    return end ? std::string(at + 1, end) : "";
  }
  size_t n = strcspn(at, ",}");
  std::string v(at, n);
  while (!v.empty() && v.back() == ' ') v.pop_back();
  return v;
}

}

void SimMeshBoard::printBanner() {
  PortduinoBoard::printBanner();
  printf("firmware: %s\n", envOr("MESHCORE_SIM_FIRMWARE", "(unpackaged)"));
  printf("node: %s at %s, ether %s\n", envOr("SIM_MESH_NODE_ID", "?"),
         envOr("SIM_MESH_BIND_ADDR", "?"), envOr("SIM_MESH_ETHER", "?"));
  const char* json = getenv("SIM_MESH_BOARD");
  std::string chip = boardField(json, "chip"), max_dbm = boardField(json, "max_dbm"),
              fem = boardField(json, "fem_part");
  printf("radio: %s, max %s dBm at the connector, front end %s\n",
         chip.empty() ? "sx1262" : chip.c_str(), max_dbm.empty() ? "?" : max_dbm.c_str(),
         fem.empty() ? "none" : fem.c_str());
}
