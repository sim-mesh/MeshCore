#!/usr/bin/env python3
"""The MeshCore companion station's host: the companion firmware and a
small command line for it, in one station.

```
station console pty ◄──► host ── companion protocol, TCP <bind addr>:5000 ──► companion firmware (SX1262)
                          ├─ descriptor 0
                          │    ├─ framed RPC → a command → JSON reply frame   for the driver
                          │    └─ every other byte → the line editor          for the person
                          ├─ acknowledgements, messages → "mchost: {json}"   for the driver
                          └─ the firmware's stdout and stderr → "fw: …"      its banner and log
```

The firmware is the stock companion built with its USB interface, whose
serial line is a TCP listener here (MESHCORE_SERIAL_LISTEN). The host speaks
the companion protocol on it itself: frames `<` len16le payload to the
firmware and `>` len16le payload back, the payload's first byte the command,
the reply or (0x80 and up) a push. One command and its reply at a time,
whoever sends it (the driver, the person, or the host fetching messages when
the firmware says some wait), so none takes another's reply.

The driver and the person share the commands (`help` lists them): the
driver's in a framed RPC frame, answered with one JSON value; the person's
typed at the `> ` prompt, answered in text.

The driver starts it with SIM_MESH_NODE_ID set to the host's own id in the
ether and MESHCORE_FIRMWARE_NODE_ID to the station's, which the firmware is
given. In a virtual-time run the host joins the ether as a station of its
own, with no radio, before anything waits on time.

When the firmware exits (a reboot), the host exits, and the station is
started again as a whole.
"""

import asyncio
import ctypes
import json
import os
import pty
import shlex
import signal
import struct
import sys
import time
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
PROMPT = "> "
APP_NAME = b"sim-mesh"
APP_VER = 3                 # the protocol version asked for: messages as their V3 frames

CMD_APP_START = 1
CMD_SEND_TXT_MSG = 2
CMD_SEND_CHANNEL_TXT_MSG = 3
CMD_GET_CONTACTS = 4
CMD_SEND_SELF_ADVERT = 7
CMD_SET_ADVERT_NAME = 8
CMD_SYNC_NEXT_MESSAGE = 10
CMD_SET_RADIO_PARAMS = 11
CMD_SET_RADIO_TX_POWER = 12
CMD_RESET_PATH = 13
CMD_DEVICE_QUERY = 22

RESP_OK = 0
RESP_ERR = 1
RESP_CONTACTS_START = 2
RESP_CONTACT = 3
RESP_END_OF_CONTACTS = 4
RESP_SELF_INFO = 5
RESP_SENT = 6
RESP_NO_MORE_MESSAGES = 10
RESP_DEVICE_INFO = 13
RESP_CONTACT_MSG_RECV_V3 = 16
RESP_CHANNEL_MSG_RECV_V3 = 17
PUSH_SEND_CONFIRMED = 0x82
PUSH_MSG_WAITING = 0x83

TXT_TYPE_PLAIN = 0
TXT_TYPE_SIGNED_PLAIN = 2
OUT_PATH_UNKNOWN = 0xFF
ERRORS = {1: "unsupported", 2: "not found", 3: "table full", 4: "busy", 5: "file error",
          6: "illegal argument"}


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
        os.write(1, b"mchost: cannot join the ether at %s\r\n" % os.environ["SIM_MESH_ETHER"].encode())
        sys.exit(1)


def leave(code):
    os._exit(code)


def die_with_parent():
    libc = ctypes.CDLL(None)
    libc.prctl(1, signal.SIGTERM)       # PR_SET_PDEATHSIG


class CommandError(Exception):
    pass


class Screen:
    """The console's output: whole lines, with the person's prompt and what
    they have typed so far kept below them."""

    def __init__(self):
        self.editing = None             # the line being typed, once the prompt is up

    def line(self, text):
        out = text.replace("\n", "\r\n") + "\r\n"
        if self.editing is not None:
            out = "\r\x1b[K" + out + PROMPT + self.editing
        os.write(1, out.encode("utf-8", "replace"))

    def raw(self, text):
        os.write(1, text.encode("utf-8", "replace"))


SCREEN = Screen()


def say(text):
    """A line of the host's own, on the console."""
    SCREEN.line(text)


def report(obj):
    """What the driver hears of, as one JSON object a line."""
    say("mchost: " + json.dumps(obj, separators=(",", ":")))


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


class Companion:
    """The one connection to the firmware: commands one at a time, pushes
    as they come."""

    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.replies = asyncio.Queue()
        self.lock = asyncio.Lock()
        self.fetching = None
        asyncio.create_task(self.read())

    @classmethod
    async def connect(cls):
        """Tried on the run's clock until the firmware listens."""
        bind = os.environ["SIM_MESH_BIND_ADDR"]
        while True:
            try:
                reader, writer = await asyncio.open_connection(bind, PORT)
                return cls(reader, writer)
            except OSError:
                await asyncio.sleep(CONNECT_RETRY_S)

    async def read(self):
        try:
            while True:
                if (await self.reader.readexactly(1)) != b">":
                    continue
                n = struct.unpack("<H", await self.reader.readexactly(2))[0]
                frame = await self.reader.readexactly(n)
                if frame and frame[0] >= 0x80:
                    self.push(frame)
                elif frame:
                    self.replies.put_nowait(frame)
        except (asyncio.IncompleteReadError, OSError):
            say("mchost: the firmware closed its connection")

    def push(self, frame):
        if frame[0] == PUSH_SEND_CONFIRMED and len(frame) >= 5:
            report({"event": "ack", "code": frame[1:5].hex()})
        elif frame[0] == PUSH_MSG_WAITING:
            self.fetch()

    async def request(self, frame, until=None):
        """`frame` sent, and its reply; with `until`, every reply up to and
        including the one whose code is in `until`."""
        async with self.lock:
            while not self.replies.empty():
                self.replies.get_nowait()       # a reply that came too late for its command
            self.writer.write(b"<" + struct.pack("<H", len(frame)) + frame)
            await self.writer.drain()
            got = [await self.replies.get()]
            while until is not None and got[-1][0] not in until and got[-1][0] != RESP_ERR:
                got.append(await self.replies.get())
        if got[-1][0] == RESP_ERR:
            code = got[-1][1] if len(got[-1]) > 1 else 0
            raise CommandError(ERRORS.get(code, "error %d" % code))
        return got if until is not None else got[0]

    async def ok(self, frame):
        reply = await self.request(frame)
        if reply[0] != RESP_OK:
            raise CommandError("unexpected reply %d" % reply[0])

    # ---- messages waiting --------------------------------------------------

    def fetch(self):
        if self.fetching is None or self.fetching.done():
            self.fetching = asyncio.create_task(self.fetch_all())

    async def fetch_all(self):
        while True:
            try:
                frame = await asyncio.wait_for(self.request(bytes((CMD_SYNC_NEXT_MESSAGE,))),
                                               EXEC_BOUND_S)
            except (asyncio.TimeoutError, CommandError) as err:
                say("mchost: fetching messages: %s" % (str(err) or "no answer"))
                return
            if frame[0] == RESP_NO_MORE_MESSAGES:
                return
            if frame[0] == RESP_CONTACT_MSG_RECV_V3 and len(frame) >= 16:
                extra = 4 if frame[11] == TXT_TYPE_SIGNED_PLAIN else 0
                report({"event": "recv", "from": frame[4:10].hex(),
                        "text": frame[16 + extra:].decode("utf-8", "replace")})
            elif frame[0] == RESP_CHANNEL_MSG_RECV_V3 and len(frame) >= 11:
                report({"event": "recv", "chan": frame[4],
                        "text": frame[11:].decode("utf-8", "replace")})

    # ---- what the commands need ------------------------------------------

    async def start(self):
        """The firmware told who we are, until it answers; then whatever
        waited for us."""
        while True:
            try:
                await asyncio.wait_for(self.device_info(), EXEC_BOUND_S)
                await asyncio.wait_for(self.self_info(), EXEC_BOUND_S)
                break
            except (asyncio.TimeoutError, CommandError) as err:
                say("mchost: the firmware does not answer yet: %s" % (str(err) or "no answer"))
        self.fetch()

    async def device_info(self):
        f = await self.request(bytes((CMD_DEVICE_QUERY, APP_VER)))
        if f[0] != RESP_DEVICE_INFO or len(f) < 80:
            raise CommandError("unexpected reply %d" % f[0])
        return {"firmware": cstr(f[60:80]), "build": cstr(f[8:20]), "board": cstr(f[20:60]),
                "protocol": f[1], "max_contacts": 2 * f[2], "channels": f[3],
                "repeat": bool(f[80]) if len(f) > 80 else False}

    async def self_info(self):
        f = await self.request(bytes((CMD_APP_START,)) + bytes(7) + APP_NAME)
        if f[0] != RESP_SELF_INFO or len(f) < 58:
            raise CommandError("unexpected reply %d" % f[0])
        lat, lon = struct.unpack_from("<ii", f, 36)
        freq, bw = struct.unpack_from("<II", f, 48)
        return {"name": f[58:].decode("utf-8", "replace"), "key": f[4:36].hex(),
                "tx": struct.unpack_from("<b", f, 2)[0], "tx_max": f[3],
                "lat": lat / 1e6, "lon": lon / 1e6,
                "freq": freq / 1000.0, "bw": bw / 1000.0, "sf": f[56], "cr": f[57]}

    async def radio(self):
        me, dev = await self.self_info(), await self.device_info()
        return {"freq": me["freq"], "bw": me["bw"], "sf": me["sf"], "cr": me["cr"],
                "tx": me["tx"], "repeat": dev["repeat"]}

    async def contacts(self):
        frames = await self.request(bytes((CMD_GET_CONTACTS,)), until=(RESP_END_OF_CONTACTS,))
        out = []
        for f in frames:
            if f[0] != RESP_CONTACT or len(f) < 132:
                continue
            plen = f[35]
            if plen == OUT_PATH_UNKNOWN:
                hops = None
            else:
                width = (plen >> 6) + 1
                path = f[36:100]
                hops = [path[i * width:(i + 1) * width].hex() for i in range(plen & 0x3F)]
            out.append({"name": cstr(f[100:132]), "key": f[1:33].hex(), "type": f[33],
                        "path": hops})
        return out

    async def contact(self, who):
        """A contact by its name, or by a prefix of its public key in hex."""
        found = await self.contacts()
        named = [c for c in found if c["name"] == who]
        if not named:
            named = [c for c in found if len(who) >= 2 and c["key"].startswith(who.lower())]
        if len(named) != 1:
            raise CommandError("%s contact %s" % ("no" if not named else "more than one", who))
        return named[0]


def cstr(raw):
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def on_off(word):
    if word in ("on", "1", "true", "yes"):
        return True
    if word in ("off", "0", "false", "no"):
        return False
    raise CommandError("on or off, not %s" % word)


class Commands:
    """The commands, each answering with one JSON-able value; `text`
    renders it for the person."""

    HELP = [
        ("infos", "this node: name, key, radio, firmware"),
        ("get radio", "frequency (MHz), bandwidth (kHz), SF, CR, power, repeat"),
        ("set name NAME", "the name this node adverts"),
        ("set radio FREQ,BW,SF,CR[,on|off]", "radio settings, and repeating"),
        ("set tx DBM", "transmit power"),
        ("advert", "a zero-hop advert"),
        ("floodadv", "a flooded advert"),
        ("contacts", "every contact, with its path"),
        ("msg CONTACT TEXT", "a direct message (contact by name or key prefix)"),
        ("chan NB TEXT", "a message on channel NB"),
        ("path CONTACT", "the contact's path, or flood"),
        ("reset_path CONTACT", "back to flood for that contact"),
        ("help", "this list"),
    ]

    def __init__(self, companion):
        self.c = companion

    async def run(self, args):
        cmd, rest = args[0], args[1:]
        if cmd == "help":
            return [{"command": c, "does": d} for c, d in self.HELP]
        if cmd == "infos":
            return dict(await self.c.self_info(), **await self.c.device_info())
        if cmd == "get" and rest == ["radio"]:
            return await self.c.radio()
        if cmd == "set" and len(rest) >= 2:
            return await self.set(rest[0], " ".join(rest[1:]))
        if cmd in ("advert", "floodadv") and not rest:
            await self.c.ok(bytes((CMD_SEND_SELF_ADVERT, 1 if cmd == "floodadv" else 0)))
            return {"ok": cmd}
        if cmd == "contacts" and not rest:
            return await self.c.contacts()
        if cmd == "msg" and len(rest) >= 2:
            return await self.msg(rest[0], " ".join(rest[1:]))
        if cmd == "chan" and len(rest) >= 2:
            try:
                nb = int(rest[0])
            except ValueError:
                raise CommandError("channel %s is not a number" % rest[0])
            await self.c.ok(bytes((CMD_SEND_CHANNEL_TXT_MSG, TXT_TYPE_PLAIN, nb & 0xFF))
                            + struct.pack("<I", int(time.time()))
                            + " ".join(rest[1:]).encode())
            return {"ok": cmd}
        if cmd == "path" and len(rest) == 1:
            return {"path": (await self.c.contact(rest[0]))["path"]}
        if cmd == "reset_path" and len(rest) == 1:
            key = bytes.fromhex((await self.c.contact(rest[0]))["key"])
            await self.c.ok(bytes((CMD_RESET_PATH,)) + key)
            return {"ok": cmd}
        raise CommandError("unknown command %s (help lists them)" % " ".join(args))

    async def set(self, what, value):
        if what == "name":
            await self.c.ok(bytes((CMD_SET_ADVERT_NAME,)) + value.encode())
        elif what == "radio":
            parts = value.replace(" ", "").split(",")
            if len(parts) not in (4, 5):
                raise CommandError("set radio FREQ,BW,SF,CR[,on|off]")
            try:
                freq, bw, sf, cr = float(parts[0]), float(parts[1]), int(parts[2]), int(parts[3])
            except ValueError:
                raise CommandError("set radio FREQ,BW,SF,CR[,on|off]")
            repeat = on_off(parts[4]) if len(parts) == 5 else (await self.c.radio())["repeat"]
            await self.c.ok(bytes((CMD_SET_RADIO_PARAMS,))
                            + struct.pack("<IIBBB", round(freq * 1000), round(bw * 1000), sf, cr,
                                          1 if repeat else 0))
        elif what == "tx":
            try:
                dbm = int(value)
            except ValueError:
                raise CommandError("set tx DBM")
            await self.c.ok(bytes((CMD_SET_RADIO_TX_POWER,)) + struct.pack("<b", dbm))
        else:
            raise CommandError("nothing called %s to set (name, radio, tx)" % what)
        return {"ok": "set " + what}

    async def msg(self, who, text):
        key = bytes.fromhex((await self.c.contact(who))["key"])
        f = await self.c.request(bytes((CMD_SEND_TXT_MSG, TXT_TYPE_PLAIN, 0))
                                 + struct.pack("<I", int(time.time())) + key[:6] + text.encode())
        if f[0] != RESP_SENT or len(f) < 10:
            raise CommandError("unexpected reply %d" % f[0])
        return {"ack": f[2:6].hex(), "timeout_ms": struct.unpack_from("<I", f, 6)[0],
                "flood": bool(f[1])}

    async def answer(self, line):
        """A command line's answer: (the value, or None, and the error)."""
        try:
            args = shlex.split(line)
        except ValueError:
            args = line.split()
        if not args:
            return None, None
        try:
            return await asyncio.wait_for(self.run(args), EXEC_BOUND_S), None
        except asyncio.TimeoutError:
            return None, "no answer in %.0f s" % EXEC_BOUND_S
        except CommandError as err:
            return None, str(err)

    async def json(self, line):
        got, err = await self.answer(line)
        if err is None and got is None:
            err = "no command"
        return json.dumps({"error": err} if err is not None else got) + "\n"

    async def text(self, line):
        got, err = await self.answer(line)
        if err is not None:
            return "error: " + err
        if got is None:
            return None
        if isinstance(got, dict) and set(got) == {"ok"}:
            return "ok"
        if line.split()[0] == "help":
            return "\n".join("  %-34s %s" % (h["command"], h["does"]) for h in got)
        if line.split()[0] == "contacts":
            if not got:
                return "no contacts"
            return "\n".join("  %-20s %s  %s" % (c["name"], c["key"][:12], hops(c["path"]))
                             for c in got)
        if line.split()[0] == "path":
            return hops(got["path"])
        if isinstance(got, dict):
            return "\n".join("  %s: %s" % kv for kv in got.items())
        return json.dumps(got)


def hops(path):
    if path is None:
        return "flood"
    return "direct" if not path else ",".join(path)


class Console:
    """Descriptor 0: framed RPC frames out to the driver's queue, every other
    byte to the person's line editor, as it came."""

    def __init__(self, commands, person):
        self.commands = commands
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
                self.person.feed(self.buf)          # a partial magic: the console's
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
            self.person.feed(passed)

    async def answer(self):
        """One frame at a time, each bounded."""
        while True:
            frame_id, payload, n = await self.frames.get()
            if n > COMMAND_MAX:
                reply = "rpc: command over 4096 bytes"
            else:
                reply = await self.commands.json(payload.decode("utf-8", "replace").strip())
            out = reply.encode("utf-8", "replace")
            if len(out) > REPLY_MAX:
                out = out[:out.rfind(b"\n", 0, REPLY_MAX) + 1]
            os.write(1, MAGIC + bytes((frame_id, len(out) >> 8, len(out) & 0xFF)) + out)


class Person:
    """The person's line editor: printable characters, backspace, ^U and ^C
    to clear the line, Enter to run it, up and down through earlier lines.
    Other escape sequences are ignored."""

    def __init__(self, commands):
        self.commands = commands
        self.lines = asyncio.Queue()
        self.history = []
        self.back = 0
        self.esc = b""
        self.pending = b""                  # a UTF-8 character's first bytes
        self.typed = ""
        self.busy = False                   # a command running: typing goes on unseen
        self.prompt()
        asyncio.create_task(self.serve())

    def prompt(self):
        SCREEN.editing = self.typed
        SCREEN.raw(PROMPT + self.typed)

    def show(self, text):
        self.typed = text
        if not self.busy:
            SCREEN.editing = text
            SCREEN.raw("\r\x1b[K" + PROMPT + text)

    def feed(self, data):
        for b in data:
            byte = bytes((b,))
            if self.esc:
                self.esc += byte
                if len(self.esc) == 2 and byte != b"[":
                    self.esc = b""
                elif len(self.esc) > 2 and 0x40 <= b <= 0x7E:
                    self.arrow(self.esc[2:])
                    self.esc = b""
                continue
            if byte == b"\x1b":
                self.esc = byte
            elif byte in (b"\r", b"\n"):
                self.enter()
            elif byte in (b"\x7f", b"\x08"):
                self.show(self.typed[:-1])
            elif byte in (b"\x03", b"\x15"):
                self.show("")
            elif b >= 0x20:
                self.pending += byte
                try:
                    ch = self.pending.decode("utf-8")
                except UnicodeDecodeError:
                    if len(self.pending) >= 4:
                        self.pending = b""
                    continue
                self.pending = b""
                self.typed += ch
                if not self.busy:
                    SCREEN.editing = self.typed
                    SCREEN.raw(ch)

    def arrow(self, seq):
        if seq not in (b"A", b"B") or not self.history:
            return
        self.back = min(self.back + 1, len(self.history)) if seq == b"A" else max(self.back - 1, 0)
        self.show(self.history[-self.back] if self.back else "")

    def enter(self):
        line, self.typed, self.back = self.typed.strip(), "", 0
        if line and (not self.history or self.history[-1] != line):
            self.history.append(line)
        if self.busy:
            if line:
                self.lines.put_nowait(line)
            return
        SCREEN.raw("\r\n")
        if line:
            self.busy = True
            SCREEN.editing = None
            self.lines.put_nowait(line)
        else:
            self.prompt()

    async def serve(self):
        while True:
            line = await self.lines.get()
            self.busy = True
            SCREEN.editing = None
            text = await self.commands.text(line)
            if text is not None:
                SCREEN.line(text)
            self.busy = not self.lines.empty()
            if not self.busy:
                self.prompt()


async def main():
    firmware = Firmware()
    await firmware.start()
    companion = await Companion.connect()
    await companion.start()
    commands = Commands(companion)
    os.write(1, MARKER)
    Console(commands, Person(commands))
    await asyncio.Event().wait()


if __name__ == "__main__":
    join_ether()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    leave(0)
