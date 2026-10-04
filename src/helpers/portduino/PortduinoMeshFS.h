#pragma once

#include <FS.h>
#include <PortduinoFS.h>

/**
 * The store: Portduino's file system over a directory (--fsdir DIR on the
 * command line, ~/.portduino/default without it), with what MeshCore asks of
 * a file system beyond it: open(path, mode, create), which makes the missing
 * directories of a path opened to write, and format().
 *
 * MeshFS has a VFS of its own, so it can be made before Portduino's; begin()
 * puts it on the directory Portduino mounted, before setup() runs.
 */
class PortduinoMeshFS : public fs::FS {
public:
  PortduinoMeshFS();

  bool begin();
  const char* root();

  using fs::FS::open;
  File open(const char* path, const char* mode, bool create);

  // Empties the root.
  bool format();
};

extern PortduinoMeshFS MeshFS;
