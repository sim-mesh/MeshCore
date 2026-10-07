# MeshCore on sim-mesh

Three MeshCore firmwares as [sim-mesh](https://github.com/sim-mesh/sim-mesh)
stations, each a Linux process on the Portduino platform
(`src/helpers/portduino/`) driving sim-mesh's virtual SX1262 through
MeshCore's own RadioLib driver. Each base ends in the MeshCore release it
is built from, as its example's `FIRMWARE_VERSION` says it
(`meshcore-repeater-sx1262-1.17.1`):

| Base | Example | The station's console |
|---|---|---|
| `meshcore-companion-sx1262-<release>` | `examples/companion_radio` | its host's own small command line, which speaks the companion's protocol to it over TCP |
| `meshcore-repeater-sx1262-<release>` | `examples/simple_repeater` | the repeater's own command line |
| `meshcore-room-sx1262-<release>` | `examples/simple_room_server` | the room server's own command line |

```
repeater, room   console pty ◄──► program (SimConsole: framed RPC → handleCommand, the rest → line editor)
companion        console pty ◄──► host.py ── companion protocol, TCP <bind addr>:5000 ──► program (USB interface)
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
  starts the firmware and speaks the companion protocol to it itself, with
  Python's standard library alone. Its commands (`help` lists them: `infos`,
  `get radio`, `set name|radio|tx`, `advert`, `floodadv`, `contacts`, `msg`,
  `chan`, `path`, `reset_path`) are the person's at the console's `> `
  prompt, answered in text, and the driver's in framed RPC, answered in
  JSON. It prints `mchost: {json}` lines for acknowledgements and every
  message fetched.
- `sim/driver.py`: the sim-mesh driver of all three, category `meshcore`.
- `sim/make-zips`: builds the three environments and makes the firmware
  zips, for the machine's own architecture or the ones asked for.
- `.github/workflows/build-sim-mesh-firmwares.yml`: the zips for x86_64 and
  for aarch64, each on a runner of its own architecture.

## Building

PlatformIO, and sim-mesh's clone beside this repository (`../sim-mesh`),
whose radio library the program links. Portduino's core needs the libuv and
libi2c headers (`libuv1-dev`, `libi2c-dev` on Ubuntu) though the program
loads neither. `make-zips` builds the radio library (`cmake`, into
`../sim-mesh/radio/build/`) when the clone has none yet, then every role
for this machine's architecture, then the zips:

```sh
pio run -e sim_mesh_sx1262_repeater          # or _room, _companion: .pio/build/<env>/program
variants/sim_mesh_sx1262/sim/make-zips       # all three, this machine's architecture, in .pio/sim-zips/
```

sim-mesh's pre-built firmware carries aarch64 and x86_64, each built on a
machine of its own architecture: `.github/workflows/build-sim-mesh-firmwares.yml`
runs `make-zips` on an x86_64 and an arm64 runner and keeps the zips as
artifacts.

**Another architecture on the same machine.** `--arch` builds for another
architecture than the machine's own (more than one `--arch` for several),
in `.pio/build.linux-<arch>/`, with that architecture's cross g++
(`g++-x86-64-linux-gnu` or `g++-aarch64-linux-gnu`) and its libc from
Ubuntu's multiarch packages (`libc6-dev:amd64` or `:arm64`). `SIM_MESH_ARCH=<arch>` in pio's environment selects
it: sim-mesh's `radio/portduino/cross.py` swaps in the cross tools, and its
`link.py` compiles the radio with them into `radio/build.linux-<arch>/` and
links that copy. By hand:

```sh
SIM_MESH_ARCH=x86_64 PLATFORMIO_BUILD_DIR=.pio/build.linux-x86_64 pio run -e sim_mesh_sx1262_repeater
```

`make-zips` checks with `readelf` that a program needs only the C library,
the C++ runtime and sim-mesh's radio, zips it stripped of its symbols and
debug sections (the build in `.pio/build` keeps them), and adds the
companion's `host.py`. The zips go to sim-mesh with `sim firmware add <zip>`.

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
