#!/usr/bin/env python3
"""The MeshCore companion station's host: the companion firmware and
meshcore-cli, as a person runs them on a desk, in one station.

```
station console pty ◄──► host ── meshcore_py, TCP <bind addr>:5000 ──► companion firmware (SX1262)
                          ├─ descriptor 0
                          │    ├─ framed RPC → process_cmds(mc, …) → reply frame    for the driver
                          │    └─ every other byte → meshcore-cli interactive_loop  for the person
                          ├─ the connection's events → "mchost: {json}" lines      for the driver
                          └─ the firmware's stdout and stderr → "fw: …" lines       its banner and log
```

The firmware is the stock companion built with its USB interface, whose
serial line is a TCP listener here (MESHCORE_SERIAL_LISTEN). Everything that
talks to it goes through the one meshcore_py connection, and one command at
a time: `mc.commands.send` is wrapped in a lock, held for a command and its
reply, which every command passes through (the person's, the driver's, and
meshcore_py's own message fetching), so none takes another's reply.

The driver starts it with SIM_MESH_NODE_ID set to the host's own id in the
ether and MESHCORE_FIRMWARE_NODE_ID to the station's, which the firmware is
given. In a virtual-time run the host joins the ether as a station of its
own, with no radio, before anything waits on time.

When the firmware exits (a reboot), the host exits, and the station is
started again as a whole. When the person quits the interactive loop, the
loop starts again.
"""

import asyncio
import ctypes
import fcntl
import io
import json
import logging
import os
import pty
import shlex
import signal
import struct
import sys
import termios
import tty

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.join(HERE, "program")
PORT = 5000
MAGIC = b"\xf5SG\x01"
COMMAND_MAX = 4096
REPLY_MAX = 16384
EXEC_BOUND_S = 5.0
STALL_S = 1.0               # no progress this long abandons a frame
CONNECT_RETRY_S = 0.5
MARKER = b"serial: framed rpc v1\r\n"


def say(text):
    """A line of the host's own, on the console."""
    print(text, flush=True)


def join_ether():
    """In a virtual-time run, this process is a station of its own in the
    ether (its id is SIM_MESH_NODE_ID), with no radio."""
    if os.environ.get("SIM_MESH_TIME") != "virtual":
        return
    lib = ctypes.CDLL(os.environ["SIM_MESH_RADIO_LIB"])
    lib.simradio_station_open.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p]
    lib.simradio_station_open.restype = ctypes.c_int
    if lib.simradio_station_open(int(os.environ["SIM_MESH_NODE_ID"]),
                                 os.environ["SIM_MESH_BIND_ADDR"].encode(),
                                 os.environ["SIM_MESH_ETHER"].encode()) != 0:
        say("mchost: cannot join the ether at %s" % os.environ["SIM_MESH_ETHER"])
        sys.exit(1)


join_ether()

from meshcore import EventType, MeshCore  # noqa: E402
from meshcore_cli import meshcore_cli as cli  # noqa: E402
from prompt_toolkit.application import create_app_session  # noqa: E402
from prompt_toolkit.input import create_pipe_input  # noqa: E402
from prompt_toolkit.output import create_output  # noqa: E402


def leave(code):
    sys.stdout.flush()
    os._exit(code)


def die_with_parent():
    libc = ctypes.CDLL(None)
    libc.prctl(1, signal.SIGTERM)       # PR_SET_PDEATHSIG


class Firmware:
    """The companion firmware, its stdout and stderr on a pty of our own."""

    async def start(self):
        bind = os.environ["SIM_MESH_BIND_ADDR"]
        env = dict(os.environ, MESHCORE_SERIAL_LISTEN="%s:%d" % (bind, PORT))
        env["SIM_MESH_NODE_ID"] = os.environ.get("MESHCORE_FIRMWARE_NODE_ID",
                                                 os.environ["SIM_MESH_NODE_ID"])
        master, slave = pty.openpty()
        tty.setraw(slave)
        self.proc = await asyncio.create_subprocess_exec(
            PROGRAM, "--fsdir", "state", stdin=asyncio.subprocess.DEVNULL,
            stdout=slave, stderr=slave, env=env, preexec_fn=die_with_parent)
        os.close(slave)
        self.master = master
        self.partial = b""
        asyncio.get_running_loop().add_reader(master, self.readable)
        asyncio.create_task(self.watch())

    def readable(self):
        try:
            data = os.read(self.master, 4096)
        except OSError:
            data = b""
        if not data:
            asyncio.get_running_loop().remove_reader(self.master)
            return
        *lines, self.partial = (self.partial + data).split(b"\n")
        for line in lines:
            say("fw: " + line.rstrip(b"\r").decode("utf-8", "replace"))

    async def watch(self):
        code = await self.proc.wait()
        if self.partial:
            say("fw: " + self.partial.decode("utf-8", "replace"))
        say("mchost: the firmware exited (%s); restarting the station" % code)
        leave(0)


async def connect():
    """The one connection to the firmware, tried on the run's clock until
    it listens and answers."""
    bind = os.environ["SIM_MESH_BIND_ADDR"]
    while True:
        try:
            mc = await MeshCore.create_tcp(bind, PORT)
            if mc is not None:
                return mc
        except OSError:
            pass
        await asyncio.sleep(CONNECT_RETRY_S)


def one_at_a_time(mc):
    """Every command and its reply in turn, whoever sends it."""
    send = mc.commands.send
    lock = asyncio.Lock()

    async def locked(*args, **kwargs):
        async with lock:
            return await send(*args, **kwargs)

    mc.commands.send = locked


def report(mc):
    """What the driver hears of: acknowledgements and every message fetched,
    as one JSON object a line."""

    def line(obj):
        say("mchost: " + json.dumps(obj, separators=(",", ":")))

    async def on_ack(event):
        line({"event": "ack", "code": event.payload.get("code")})

    async def on_contact_msg(event):
        p = event.payload
        line({"event": "recv", "from": p.get("pubkey_prefix"), "text": p.get("text")})

    async def on_channel_msg(event):
        p = event.payload
        line({"event": "recv", "chan": p.get("channel_idx"), "text": p.get("text")})

    mc.subscribe(EventType.ACK, on_ack)
    mc.subscribe(EventType.CONTACT_MSG_RECV, on_contact_msg)
    mc.subscribe(EventType.CHANNEL_MSG_RECV, on_channel_msg)


class Console:
    """Descriptor 0: framed RPC frames out to the driver's queue, every other
    byte to the person's interactive loop, as it came."""

    def __init__(self, mc, person):
        self.mc = mc
        self.person = person
        self.frames = asyncio.Queue()
        self.buf = b""
        self.last = 0.0
        loop = asyncio.get_running_loop()
        if os.isatty(0):
            tty.setraw(0)
        loop.add_reader(0, self.readable)
        asyncio.create_task(self.answer())

    def readable(self):
        try:
            data = os.read(0, 4096)
        except OSError:
            data = b""
        if not data:
            asyncio.get_running_loop().remove_reader(0)
            return
        now = asyncio.get_running_loop().time()
        if self.buf and now - self.last > STALL_S:
            if not self.buf.startswith(MAGIC):
                self.person.send_bytes(self.buf)    # a partial magic: the console's
            self.buf = b""                          # a stalled frame: dropped
        self.last = now
        self.buf += data
        self.take()

    def take(self):
        """Frames out of the buffer; a partial magic or frame waits for more."""
        passed = b""
        buf = self.buf
        while buf:
            at = buf.find(MAGIC[0:1])
            if at < 0:
                passed += buf
                buf = b""
                break
            passed += buf[:at]
            buf = buf[at:]
            if not MAGIC.startswith(buf[:4]):
                passed += buf[:1]           # a lone 0xF5: the console's
                buf = buf[1:]
                continue
            if len(buf) < 7:
                break                       # wait for the header
            n = (buf[5] << 8) | buf[6]
            if len(buf) < 7 + n:
                break                       # wait for the payload
            self.frames.put_nowait((buf[4], buf[7:7 + n], n))
            buf = buf[7 + n:]
        self.buf = buf
        if passed:
            self.person.send_bytes(passed)

    async def answer(self):
        """One frame at a time, each bounded."""
        while True:
            frame_id, payload, n = await self.frames.get()
            if n > COMMAND_MAX:
                reply = "rpc: command over 4096 bytes"
            else:
                reply = await self.run(payload.decode("utf-8", "replace").strip())
            out = reply.encode("utf-8", "replace")
            if len(out) > REPLY_MAX:
                out = out[:out.rfind(b"\n", 0, REPLY_MAX) + 1]
            os.write(1, MAGIC + bytes((frame_id, len(out) >> 8, len(out) & 0xFF)) + out)

    async def run(self, line):
        """A meshcore-cli command line, as on its own command line, in JSON:
        what it printed, and what it logged as an error. A command it does
        not know, or one that printed nothing, answers so."""
        sink = io.StringIO()
        errors = ErrorsTo(sink)
        try:
            args = shlex.split(line)
        except ValueError as err:
            return json.dumps({"error": str(err)}) + "\n"
        if not args:
            return json.dumps({"error": "no command"}) + "\n"
        try:
            logging.getLogger("meshcore").addHandler(errors)
            known = await asyncio.wait_for(
                cli.process_cmds(self.mc, args, json_output=True, sink=sink), EXEC_BOUND_S)
        except asyncio.TimeoutError:
            known = True
            sink.write(json.dumps({"error": "no answer in %.0f s" % EXEC_BOUND_S}) + "\n")
        except Exception as err:  # noqa: BLE001 - the reply says what went wrong
            known = True
            sink.write(json.dumps({"error": str(err)}) + "\n")
        finally:
            logging.getLogger("meshcore").removeHandler(errors)
        if not known:
            sink.write(json.dumps({"error": "unknown command", "command": args[0]}) + "\n")
        return sink.getvalue() or json.dumps({"ok": args[0]}) + "\n"


class ErrorsTo(logging.Handler):
    """meshcore-cli's errors during one frame, into its reply."""

    def __init__(self, sink):
        super().__init__(logging.ERROR)
        self.sink = sink

    def emit(self, record):
        self.sink.write(json.dumps({"error": record.getMessage()}) + "\n")


async def person_loop(mc, person):
    """meshcore-cli's own interactive loop, reading what the console passes
    on; started again when someone quits it."""
    if os.isatty(1) and os.get_terminal_size(1).columns == 0:
        fcntl.ioctl(1, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    output = create_output(stdout=sys.stdout)
    with create_app_session(input=person, output=output):
        while True:
            try:
                await cli.interactive_loop(mc)
            except Exception as err:  # noqa: BLE001 - the loop starts again
                say("mchost: the interactive loop ended: %s" % err)
            await asyncio.sleep(0.1)


async def main():
    firmware = Firmware()
    await firmware.start()
    mc = await connect()
    one_at_a_time(mc)
    report(mc)
    await mc.start_auto_message_fetching()
    with create_pipe_input() as person:
        Console(mc, person)
        os.write(1, MARKER)
        await person_loop(mc, person)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    leave(0)
