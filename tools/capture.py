"""Listen-only BLE capture from an ESPHome Bluetooth proxy. Sends nothing.

    python tools/capture.py --host 192.0.2.10 --out captures/01-baseline.jsonl [--profile valen]

Every advert goes to the JSONL file; the terminal shows only mesh-related traffic,
decoded with the profile's keys and named from its commands. While it runs, type a
label and press Enter to drop a marker ("L1 off", "R1 power"); an empty line drops
a numbered marker; "q" stops.

Sources:
  raw  (default) every advert with RSSI, through the Bluetooth proxy API. Only one
       API client can hold this subscription: keep the board out of Home Assistant
       (or disable its ESPHome entry) while capturing.
  log  ble_adv_proxy's "recv raw" log lines: mesh adverts only, no RSSI, repeats
       within 20 s suppressed. Works while Home Assistant is connected.

The API key comes from --key or the ESPHOME_API_KEY environment variable.
Captures contain your neighbours' devices too: keep them out of public repositories.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from aioesphomeapi import APIClient, LogLevel

import air
from _package import load_profile

LOG_RAW = re.compile(r"\[([0-9A-F:]{17})\] recv raw - ([0-9a-fA-F]+)")
GONE_AFTER = 10.0  # seconds without adverts before a mesh device counts as gone
REPEAT_WINDOW = 2.0  # identical mesh PDUs within this window are printed once


def mac_of(address: int) -> str:
    return ":".join(f"{(address >> (8 * i)) & 0xFF:02X}" for i in range(5, -1, -1))


class Capture:
    def __init__(self, out: Path, profile, show_all: bool) -> None:
        self.file = out.open("a", encoding="utf-8")
        self.profile = profile
        self.show_all = show_all
        self.network_id = air.mesh._k3(profile.net_key)
        self.adverts = 0
        self.markers = 0
        self.mesh_macs: dict[str, float] = {}
        self.gone: set[str] = set()
        self.described: set[tuple[str, str]] = set()
        self.recent_pdus: dict[bytes, float] = {}

    def write(self, record: dict) -> None:
        self.file.write(json.dumps(record) + "\n")
        self.file.flush()

    def say(self, text: str) -> None:
        print(time.strftime("%H:%M:%S"), text, flush=True)

    def marker(self, label: str) -> None:
        self.markers += 1
        label = label or f"mark {self.markers}"
        self.write({"t": time.time(), "kind": "marker", "label": label})
        self.say(f"=== MARKER: {label}")

    def advert(self, mac: str, rssi: int | None, address_type: int | None, data: bytes) -> None:
        now = time.time()
        self.adverts += 1
        self.write(
            {"t": now, "kind": "adv", "mac": mac, "rssi": rssi, "atype": address_type, "raw": data.hex()}
        )
        info = air.parse_advert(data)
        if not info.mesh_related:
            if self.show_all:
                self.say(f"{mac} rssi={rssi} {data.hex()}")
            return
        if mac in self.gone or mac not in self.mesh_macs:
            self.gone.discard(mac)
            self.say(f"+ {mac} appeared (rssi={rssi}) name={info.name!r}")
        self.mesh_macs[mac] = now
        for note in [air.describe_beacon(b) for b in info.mesh_beacons] + [
            f"network id {n.hex()} -> {'profile NetKey' if n == self.network_id else 'other NetKey'}"
            for n in air.network_ids_in(info)
        ]:
            if (mac, note) not in self.described:
                self.described.add((mac, note))
                self.say(f"  {mac} {note}")
        for pdu in info.mesh_pdus:
            if now - self.recent_pdus.get(pdu, 0) < REPEAT_WINDOW:
                continue
            self.recent_pdus[pdu] = now
            d = air.diagnose(self.profile.net_key, self.profile.app_key, pdu, self.profile.iv_index)
            if d.payload is None:
                self.say(f"  mesh {mac} rssi={rssi} NID={d.nid:#04x}: {d.error}")
                continue
            name = self.profile.identify(d.payload) or "?"
            self.say(
                f"  mesh {mac} rssi={rssi} src={d.source:#06x} dst={d.destination:#06x} seq={d.sequence} "
                f"ttl={d.ttl} payload={d.payload.hex()} [{name}] {air.opcode_summary(d.payload)}"
            )

    def check_presence(self) -> None:
        now = time.time()
        for mac, last in self.mesh_macs.items():
            if mac not in self.gone and now - last > GONE_AFTER:
                self.gone.add(mac)
                self.say(f"- {mac} gone (silent for {now - last:.0f} s)")


def start_stdin_thread(loop: asyncio.AbstractEventLoop, capture: Capture, stop: asyncio.Event) -> None:
    def reader() -> None:
        for line in sys.stdin:
            label = line.strip()
            if label.lower() in ("q", "quit", "exit"):
                loop.call_soon_threadsafe(stop.set)
                return
            loop.call_soon_threadsafe(capture.marker, label)

    threading.Thread(target=reader, daemon=True).start()


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", required=True, help="board IP or name")
    parser.add_argument("--key", default=os.environ.get("ESPHOME_API_KEY"), help="API encryption key")
    parser.add_argument("--out", type=Path, help="JSONL file (default captures/<time>.jsonl)")
    parser.add_argument("--profile", default="valen")
    parser.add_argument("--source", choices=("raw", "log"), default="raw")
    parser.add_argument("--seconds", type=float, default=0, help="stop after this long (default: until q)")
    parser.add_argument("--all", action="store_true", help="also print non-mesh adverts (very noisy)")
    args = parser.parse_args()

    out = args.out or Path(__file__).resolve().parents[1] / "captures" / time.strftime("%Y%m%d-%H%M%S.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    capture = Capture(out, load_profile(args.profile), args.all)

    client = APIClient(args.host, 6053, None, noise_psk=args.key)
    await client.connect(login=True)
    info = await client.device_info()
    capture.write(
        {
            "t": time.time(),
            "kind": "meta",
            "node": info.name,
            "esphome": info.esphome_version,
            "source": args.source,
        }
    )
    capture.say(f"connected to {info.name} (ESPHome {info.esphome_version}), writing {out}")

    if args.source == "raw":

        def on_raw(response) -> None:
            for adv in response.advertisements:
                capture.advert(mac_of(adv.address), adv.rssi, adv.address_type, bytes(adv.data))

        unsubscribe = client.subscribe_bluetooth_le_raw_advertisements(on_raw)
    else:

        def on_log(message) -> None:
            match = LOG_RAW.search(message.message.decode(errors="replace"))
            if match:
                capture.advert(match.group(1), None, None, bytes.fromhex(match.group(2)))

        unsubscribe = client.subscribe_logs(on_log, log_level=LogLevel.LOG_LEVEL_DEBUG)

    stop = asyncio.Event()
    start_stdin_thread(asyncio.get_running_loop(), capture, stop)
    capture.say("type a label + Enter for a marker, q + Enter to stop")
    started = time.time()
    warned = False
    try:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
            capture.check_presence()
            elapsed = time.time() - started
            if args.source == "raw" and not warned and elapsed > 15 and capture.adverts == 0:
                warned = True
                capture.say(
                    "WARNING: no adverts in 15 s. Is Home Assistant connected to this board? "
                    "It holds the only advert subscription. Disable it there or use --source log."
                )
            if args.seconds and elapsed >= args.seconds:
                break
    finally:
        unsubscribe()
        await client.disconnect()
        capture.say(f"done: {capture.adverts} adverts, {capture.markers} markers -> {out}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
