#include "PortduinoMeshFS.h"

#include <logging.h>
#include <cstdio>
#include <cstring>
#include <ftw.h>
#include <string>
#include <sys/stat.h>

PortduinoMeshFS MeshFS;

PortduinoMeshFS::PortduinoMeshFS() : fs::FS(std::make_shared<VFSImpl>()) { }

bool PortduinoMeshFS::begin() {
  const char* mp = portduinoVFS->mountpoint();
  if (!mp) {
    log_e("MeshFS: Portduino has mounted no file system");
    return false;
  }
  _impl->mountpoint(mp);
  return true;
}

const char* PortduinoMeshFS::root() {
  const char* mp = _impl->mountpoint();
  return mp ? mp : portduinoVFS->mountpoint();
}

File PortduinoMeshFS::open(const char* path, const char* mode, bool create) {
  if (create && path && path[0] == '/' && mode && mode[0] != 'r') {
    std::string dirs(path);
    for (size_t at = dirs.find('/', 1); at != std::string::npos; at = dirs.find('/', at + 1)) {
      std::string dir = dirs.substr(0, at);
      if (!exists(dir.c_str())) mkdir(dir.c_str());
    }
  }
  return fs::FS::open(path, mode);
}

static int removeBelowRoot(const char* path, const struct stat*, int, struct FTW* at) {
  if (at->level == 0) return 0;
  if (::remove(path) != 0) {
    log_e("MeshFS: cannot remove %s", path);
    return -1;
  }
  return 0;
}

bool PortduinoMeshFS::format() {
  const char* mp = root();
  if (!mp) return false;
  return nftw(mp, removeBelowRoot, 16, FTW_DEPTH | FTW_PHYS) == 0;
}
