# MeshCore on sim-mesh

Three MeshCore firmwares as [sim-mesh](https://github.com/sim-mesh/sim-mesh)
stations, each a Linux process on the Portduino platform
(`src/helpers/portduino/`) driving sim-mesh's virtual SX1262 through
MeshCore's own RadioLib driver:

| Base | Example | The station's console |
|---|---|---|
| `meshcore-companion-sx1262` | `examples/companion_radio` | meshcore-cli, on the companion's protocol as on a desk, carried over TCP |
| `meshcore-repeater-sx1262` | `examples/simple_repeater` | the repeater's own command line |
| `meshcore-room-sx1262` | `examples/simple_room_server` | the room server's own command line |

```
repeater, room   console pty ◄──► program (SimConsole: framed RPC → handleCommand, the rest → line editor)
companion        console pty ◄──► host.py ── meshcore_py, TCP <bind addr>:5000 ──► program (USB interface)
every program    RadioLib ── WholeFrameHal: one SPI.transfer per transaction ──► sim-mesh radio/portduino
```

## The pieces

- `target.h`, `target.cpp`: the board (`SimMeshBoard`), the radio on a
  `WholeFrameHal`, the store's clock, and `portduinoSetup()`, which opens the
  station's link to the ether before `setup()`. Identities come from
  `getrandom`, which sim-mesh keys by the run's seed and the node in a
  virtual-time run.
- `SimSerialPort.*`: `Serial` is `SimConsole`, a `PosixSerial` that in the
  repeater and room server takes framed RPC frames out of the console
  (`console/console_command.cpp` runs each on the build's own command line)
  and prints `serial: framed rpc v1` when it starts.
- `SimMeshBoard.*`: the boot banner's station lines (firmware, node id,
  bind address, ether, the board from `SIM_MESH_BOARD`).
- `sim/host.py`: the companion's host, a second process of the station. It
  starts the firmware, connects to it with meshcore_py, runs meshcore-cli's
  interactive loop for a person at the console, answers framed RPC with
  meshcore-cli command lines in JSON, and prints `mchost: {json}` lines for
  acknowledgements and every message fetched.
- `sim/driver.py`: the sim-mesh driver of all three, category `meshcore`.
- `sim/make-zips`: builds the three environments for aarch64 and x86_64 and
  makes the firmware zips.

## Building

PlatformIO, and sim-mesh's clone beside this repository (`../sim-mesh`,
whose `radio/build/` holds `libsimradio-sx1262.so` once sim-mesh has
started, or `cmake -S ../sim-mesh/radio -B ../sim-mesh/radio/build && cmake
--build ../sim-mesh/radio/build`). Portduino's core needs the libuv and
libi2c headers (`libuv1-dev`, `libi2c-dev` on Ubuntu) though the program
loads neither.

```sh
pio run -e sim_mesh_sx1262_repeater          # or _room, _companion: .pio/build/<env>/program
variants/sim_mesh_sx1262/sim/make-zips       # all three, both architectures, in .pio/sim-zips/
```

**Both architectures.** sim-mesh's pre-built firmware carries aarch64 and
x86_64, so `make-zips` builds each role for both (`--arch` for one): the
machine's own natively in `.pio/build/`, the other in
`.pio/build.linux-<arch>/` with that architecture's cross g++
(`g++-x86-64-linux-gnu` or `g++-aarch64-linux-gnu`) and its libc from
Ubuntu's multiarch packages (`libc6-dev:amd64` or `:arm64`), as the spangap
build image has them. `SIM_MESH_ARCH=<arch>` in pio's environment selects
it: sim-mesh's `radio/portduino/cross.py` swaps in the cross tools, and its
`link.py` compiles the radio with them into `radio/build.linux-<arch>/` and
links that copy. By hand:

```sh
SIM_MESH_ARCH=x86_64 PLATFORMIO_BUILD_DIR=.pio/build.linux-x86_64 pio run -e sim_mesh_sx1262_repeater
```

`make-zips` checks with `readelf` that a program needs only the C library,
the C++ runtime and sim-mesh's radio, and fills the companion's `pylib/`
with meshcore-cli, meshcore_py and their dependencies for CPython 3.12 on
the zip's architecture (bleak left out: both import it inside a `try`),
checked, on the machine's own architecture, by importing meshcore-cli from
it alone. Its Python needs pip; the zips go to sim-mesh with
`sim firmware add <zip>`.

## Running one by hand

```sh
SIM_MESH_NODE_ID=1 SIM_MESH_BIND_ADDR=127.16.0.1 SIM_MESH_ETHER=127.0.0.1:7100 \
LD_LIBRARY_PATH=../sim-mesh/radio/build \
  .pio/build/sim_mesh_sx1262_repeater/program --fsdir state
```

against `python3 ../sim-mesh/ether/ether.py --bind 127.0.0.1:7100`. Its
store is `state/`; `reboot` exits, for whatever runs it to start it again.
`MESHCORE_SERIAL_LISTEN=addr:port` makes `Serial` the one TCP connection
accepted there instead of the console, which is how the companion's host
reaches the companion.
