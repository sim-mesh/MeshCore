"""MeshCore on sim-mesh: the driver of the companion, the repeater and the
room server, category `meshcore`.

```
repeater, room   sim-mesh ── framed RPC on the console ──► the firmware's own command line
companion        sim-mesh ── framed RPC on the console ──► host.py ── meshcore-cli line ──► firmware
companion        host.py ── "mchost: {json}" lines ──► console_line ──► msg.status, msg.received
```

Every verb is a command line: the repeater's and the room server's own CLI,
or for the companion a meshcore-cli command its host runs on the one
connection to the firmware, answering in JSON. The companion's host is a
second process of the station with its own id in the ether
(node id + 1 000 000), and it reads the console.

A direct message carries sim-mesh's id at the end of its text, sent with
`msg`; its expected acknowledgement code couples it to the id, and the
acknowledgement, or none in the time the firmware suggests, ends it. Every
message the receiving companion fetches is reported with the id read back
out of its text. The repeater and the room server have no messaging of
their own: they take every firmware's verbs and `repeat`, `advert` and
`floodadv`.
"""

import asyncio
import json
import os
import shlex

from sim_mesh.driver import CommandError, chip_dbm
from sim_mesh.meshcore.driver import MeshcoreDriver, tagged, untagged

HOST_SID_OFFSET = 1_000_000
HOST_LINE = "mchost: "
CONFIGURED = "sim-configured"           # in state/: set up, written at the first flush
MARKER_WAIT_S = 20.0
ACK_WAIT_MIN_S = 10.0                   # an acknowledgement's least wait
ACK_WAIT_FACTOR = 2.0                   # times the firmware's suggested timeout


class MeshCoreSim(MeshcoreDriver):
    def __init__(self, firmware):
        super().__init__(firmware)
        self.companion = "companion" in self.firmware["base"]
        self.acks = {}              # station name -> {ack code: mid}
        self.reboot = set()         # stations whose radio applies at a restart
        self.tasks = set()          # acknowledgement waits

    # ---- starting a station ----------------------------------------------

    def argv(self, station):
        if not self.companion:
            return [self.firmware["exec"], "--fsdir", "state"]
        return ["env", "SIM_MESH_NODE_ID=%d" % (station.node_id + HOST_SID_OFFSET),
                "MESHCORE_FIRMWARE_NODE_ID=%d" % station.node_id,
                "python3", self.firmware["exec"]]

    def env(self, station):
        return {"SIM_MESH_IDLE": "threads",
                "MESHCORE_SIM_FIRMWARE": self.firmware.get("firmware") or self.firmware["base"]}

    def sids(self, station):
        if self.companion:
            return (station.node_id, station.node_id + HOST_SID_OFFSET)
        return (station.node_id,)

    def console_sid(self, station):
        return station.node_id + HOST_SID_OFFSET if self.companion else station.node_id

    def configured(self, station):
        return os.path.exists(os.path.join(station.dir, "state", CONFIGURED))

    async def wait_up(self, station, timeout):
        wait = timeout if self.companion else min(MARKER_WAIT_S, timeout)
        return await self.rpc_ready(station, timeout, wait)

    async def flush(self, station):
        state = os.path.join(station.dir, "state")
        if os.path.isdir(state):
            open(os.path.join(state, CONFIGURED), "a").close()
        if station.name in self.reboot:
            self.reboot.discard(station.name)
            await station.restart()

    # ---- lines -----------------------------------------------------------

    async def run(self, station, line, timeout=None):
        return await self.rpc_query(station, line, timeout)

    async def cli(self, station, *args):
        """The companion: one meshcore-cli command, its JSON reply parsed.
        An error it reports is raised."""
        text = (await self.run(station, " ".join(shlex.quote(str(a)) for a in args))).strip()
        try:
            got = json.loads(text)
        except ValueError:
            got = None
        if got is None:
            raise CommandError("%s: %s" % (args[0], text or "no answer"))
        if isinstance(got, dict) and "error" in got:
            raise CommandError("%s: %s" % (args[0], got["error"]))
        return got

    async def own(self, station, line):
        """The repeater's or the room server's command line: its reply,
        without the CLI's '> ' or the leading 'OK - '."""
        reply = (await self.run(station, line)).strip()
        if reply.startswith("Err") or reply.startswith("Unknown") or reply.startswith("Error"):
            raise CommandError("%s: %s" % (line, reply))
        return reply[2:] if reply.startswith("> ") else reply

    def need_companion(self, verb):
        if not self.companion:
            raise self.cannot(verb)

    # ---- every firmware's verbs ------------------------------------------

    async def name(self, station, name):
        if self.companion:
            await self.cli(station, "set", "name", name)
        else:
            await self.own(station, "set name %s" % name)

    async def radio(self, station, freq_mhz=None, sf=None, bw_khz=None, cr=None, tx_dbm=None,
                    sync=None, preamble=None):
        if sync is not None or preamble is not None:
            raise CommandError("MeshCore sets neither the sync word nor the preamble length")
        if (freq_mhz, sf, bw_khz, cr) != (None, None, None, None):
            now = await self.current_radio(station)
            figures = (freq_mhz if freq_mhz is not None else now["freq"],
                       bw_khz if bw_khz is not None else now["bw"],
                       sf if sf is not None else now["sf"],
                       cr if cr is not None else now["cr"])
            if self.companion:
                await self.cli(station, "set", "radio", "%s,%s,%s,%s,%s" % (
                    figures + ("on" if now["repeat"] else "off",)))
            else:
                await self.own(station, "set radio %s,%s,%s,%s" % figures)
                self.reboot.add(station.name)
        if tx_dbm is not None:
            await self.tx_power(station, tx_dbm)

    async def current_radio(self, station):
        if self.companion:
            got = await self.cli(station, "get", "radio")
            return {"freq": got["radio_freq"], "bw": got["radio_bw"], "sf": got["radio_sf"],
                    "cr": got["radio_cr"], "repeat": got.get("repeat", False)}
        freq, bw, sf, cr = (await self.own(station, "get radio")).split(",")
        return {"freq": float(freq), "bw": float(bw), "sf": int(sf), "cr": int(cr)}

    async def tx_power(self, station, dbm):
        chip = int(round(chip_dbm(station.board, dbm)))
        if self.companion:
            await self.cli(station, "set", "tx", chip)
        else:
            await self.own(station, "set tx %d" % chip)

    async def diagnostics(self, station):
        if self.companion:
            return {"infos": await self.run(station, "infos")}
        return {"ver": await self.own(station, "ver"), "stats": await self.run(station, "stats-core")}

    # ---- the category's verbs --------------------------------------------

    async def repeat(self, station, on):
        if not self.companion:
            await self.own(station, "set repeat %s" % ("on" if on else "off"))
            return
        now = await self.current_radio(station)
        await self.cli(station, "set", "radio", "%s,%s,%s,%s,%s" % (
            now["freq"], now["bw"], now["sf"], now["cr"], "on" if on else "off"))

    async def advert(self, station):
        if self.companion:
            await self.cli(station, "advert")
        else:
            await self.own(station, "advert.zerohop")

    async def floodadv(self, station):
        if self.companion:
            await self.cli(station, "floodadv")
        else:
            await self.own(station, "advert")

    async def contacts(self, station):
        self.need_companion("contacts")
        got = await self.cli(station, "contacts") or {}
        out = []
        for key, c in got.items():
            plen = c.get("out_path_len", -1)
            out.append((c.get("adv_name"), (c.get("public_key") or key)[:12],
                        None if plen is None or plen < 0 else plen))
        return out

    async def msg(self, station, dest, text, mid):
        self.need_companion("msg")
        try:
            sent = await self.cli(station, "msg", dest, tagged(text, mid))
        except CommandError as err:
            self.msg_status(station, mid, "failed", str(err))
            return
        if not sent or "expected_ack" not in sent:
            self.msg_status(station, mid, "failed", json.dumps(sent) if sent else "not sent")
            return
        code = sent["expected_ack"]
        self.acks.setdefault(station.name, {})[code] = mid
        self.msg_status(station, mid, "sent")
        wait = max(ACK_WAIT_MIN_S, ACK_WAIT_FACTOR * sent.get("suggested_timeout", 0) / 1000.0)
        station_name = station.name

        async def expire():
            await self.pause(station, wait)
            if self.acks.get(station_name, {}).pop(code, None) is not None:
                self.msg_status(station, mid, "failed", "no acknowledgement in %.0f s" % wait)

        self.spawn(expire())

    async def chan(self, station, nb, text, mid):
        self.need_companion("chan")
        try:
            await self.cli(station, "chan", nb, tagged(text, mid))
        except CommandError as err:
            self.msg_status(station, mid, "failed", str(err))
            return
        self.msg_status(station, mid, "sent")

    async def path(self, station, dest):
        """From the contact's own record (`contacts`), as meshcore-cli's
        `path` prints it: its hops' hash prefixes, [] for a neighbour, None
        for flood."""
        self.need_companion("path")
        got = await self.cli(station, "contacts") or {}
        found = [c for c in got.values() if c.get("adv_name") == dest]
        if not found:
            raise CommandError("path: no contact %s" % dest)
        c = found[0]
        plen = c.get("out_path_len", -1)
        if plen is None or plen < 0:
            return None
        width = 2 * (c.get("out_path_hash_mode", 0) + 1)
        hops = c.get("out_path") or ""
        return [hops[i * width:(i + 1) * width] for i in range(plen)]

    async def reset_path(self, station, dest):
        self.need_companion("reset_path")
        await self.cli(station, "reset_path", dest)

    # ---- what the companion's host says ----------------------------------

    def console_line(self, station, line):
        at = line.find(HOST_LINE)
        if at < 0:
            return
        try:
            said = json.loads(line[at + len(HOST_LINE):])
        except ValueError:
            return
        if not isinstance(said, dict):
            return
        if said.get("event") == "ack":
            mid = self.acks.get(station.name, {}).pop(said.get("code"), None)
            if mid is not None:
                self.msg_status(station, mid, "delivered")
        elif said.get("event") == "recv":
            text = said.get("text") or ""
            if said.get("chan") is not None and ": " in text:
                text = text.split(": ", 1)[1]           # "<sender>: <text>" on a channel
            body, mid = untagged(text)
            self.msg_received(station, mid, body, sender=said.get("from"), chan=said.get("chan"))

    def spawn(self, coro):
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)


DRIVER = MeshCoreSim
