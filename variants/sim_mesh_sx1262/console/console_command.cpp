// The repeater's and the room server's command line, for framed RPC on the
// console: the call their own line editor makes.
#include "MyMesh.h"

extern MyMesh the_mesh;

bool simConsoleCommand(char* line, char* reply) {
  the_mesh.handleCommand(0, line, reply);
  return true;
}
