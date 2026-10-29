"""Send Bluetooth Mesh commands through a board running the ble-adv-tx package.

    python tools/send.py list [--profile valen]
    python tools/send.py --host IP cmd off --src 0x7FFE
    python tools/send.py --host IP raw "C8 11 02 TT 03 00" --src 0x7FFE
    python tools/send.py --host IP pair --src 0x7FFE
    python tools/send.py --host IP trial --src 0x7FFE --count 10 --note "scan 1100/1100"

Rules this script enforces:
- every sender address has its own sequence counter in tools/state/sequence.json,
  reserved on disk before use. Never delete that file: a light ignores a sender
  whose sequence goes back.
- a sender address must be unicast, must not be 0x7FFF and must not be any
  remote's address listed in tools/state/remotes.json (["0x37af", ...]): sending
  as a remote breaks that remote.
- addresses 0x7E01-0x7EFF belong to the Home Assistant integration's lamps and
  are refused unless --force: this tool keeps its own counters, and a light drops
  messages from an address whose sequence is behind.

The API key comes from --key or the ESPHOME_API_KEY environment variable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from aioesphomeapi import APIClient

from _package import load, load_profile

addressing = load("addressing")
mesh = load("mesh")
sequence = load("sequence")

STATE = Path(__file__).resolve().parent / "state"
SEQUENCE_FILE = STATE / "sequence.json"
REMOTES_FILE = STATE / "remotes.json"
TRIALS_FILE = Path(__file__).resolve().parents[1] / "captures" / "trials.jsonl"


class FileStore:
    """A JSON file with the async_load/async_save interface of HA's Store, written atomically."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def async_load(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else None

    async def async_save(self, data) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self.path)


def known_remotes() -> list[int]:
    if not REMOTES_FILE.exists():
        return []
    return [int(x, 16) for x in json.loads(REMOTES_FILE.read_text(encoding="utf-8"))]


class Sender:
    def __init__(self, client: APIClient, service, source: int, profile, counters) -> None:
        self.client, self.service, self.source, self.profile, self.counters = (
            client,
            service,
            source,
            profile,
            counters,
        )
        self.codec = mesh.MeshCodec(profile.net_key, profile.app_key, profile.iv_index)

    async def send(self, template: str, repeat: int = 3, step: int = 1, duration: float = 100.0) -> None:
        for _ in range(step):  # --seq-step N skips N - 1 numbers, as a remote does between presses
            seq = await self.counters.async_next(self.source)
        payload = bytes.fromhex(template.replace("TT", f"{0x80 | (seq & 0x7F):02X}"))
        raw = self.codec.encode(payload, seq, self.source, self.profile.destination, self.profile.ttl).hex()
        await self.client.execute_service(
            self.service,
            {
                "raw": raw,
                "duration": float(duration),  # ms of advertising per copy
                "repeat": float(repeat),
                "ignored_advs": [raw],  # do not report our own packet back
                "ignored_duration": 20000.0,
            },
        )
        print(
            time.strftime("%H:%M:%S"),
            f"sent src={self.source:#06x} seq={seq} payload={payload.hex()} "
            f"[{self.profile.identify(payload) or '?'}] ({repeat}×{duration:.0f} ms)",
            flush=True,
        )


async def connect(args) -> tuple[APIClient, object]:
    client = APIClient(args.host, 6053, None, noise_psk=args.key)
    await client.connect(login=True)
    _, services = await client.list_entities_services()
    service = next((s for s in services if s.name == "adv_svc_v1"), None)
    if service is None:
        await client.disconnect()
        sys.exit(f"No adv_svc_v1 action on the board. Found: {[s.name for s in services]}")
    return client, service


async def run_trial(sender: Sender, args) -> None:
    """Send off, on, off, ... and let the person say whether the lamp obeyed each time."""
    commands = sender.profile.commands
    if "on" not in commands or "off" not in commands:
        sys.exit("trial needs a profile with separate on and off commands")
    print("Start with the lamp ON. After each command answer y (obeyed), n (did not) or q (stop).")
    loop = asyncio.get_running_loop()
    results = []
    expect_off = True
    while len(results) < args.count:
        command = commands["off" if expect_off else "on"]
        await sender.send(command.template, repeat=args.repeat, step=args.seq_step, duration=args.duration)
        # Ask in a thread so the API connection keeps answering pings meanwhile.
        prompt = f"  lamp went {'OFF' if expect_off else 'ON'}? [y/n/q] "
        answer = (await loop.run_in_executor(None, input, prompt)).strip().lower()
        if answer.startswith("q"):
            break
        ok = answer.startswith("y")
        results.append(ok)
        if ok:
            expect_off = not expect_off  # otherwise retry the same command
    if not results:
        return
    print(
        f"obeyed {sum(results)} of {len(results)} ({sum(results) / len(results):.0%}); repeat={args.repeat} "
        f"duration={args.duration:.0f} ms seq-step={args.seq_step} {args.note}"
    )
    TRIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with TRIALS_FILE.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "t": time.time(),
                    "src": f"{args.src:#06x}",
                    "repeat": args.repeat,
                    "duration": args.duration,
                    "seq_step": args.seq_step,
                    "note": args.note,
                    "results": results,
                }
            )
            + "\n"
        )


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", help="board IP or name")
    parser.add_argument("--key", default=os.environ.get("ESPHOME_API_KEY"), help="API encryption key")
    parser.add_argument("--profile", default="valen")
    parser.add_argument(
        "--force", action="store_true", help="allow an address of the integration's range (0x7E01-0x7EFF)"
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="show the profile's commands")
    for name, text in (
        ("cmd", "a command of the profile"),
        ("raw", "an access payload in hex, TT = transaction"),
    ):
        p = sub.add_parser(name, help=f"send {text}")
        p.add_argument("what")
        p.add_argument("--src", required=True, type=lambda s: int(s, 0))
        p.add_argument("--count", type=int, default=1, help="send this many times")
        p.add_argument("--interval", type=float, default=0.6, help="seconds between sends")
        p.add_argument(
            "--seq-step", type=positive_int, default=1, help="advance the sequence by this much per send"
        )
        p.add_argument("--repeat", type=int, default=3, help="advertising bursts per command")
        p.add_argument("--duration", type=float, default=100, help="ms of advertising per burst")
    p = sub.add_parser(
        "trial", help="alternate off/on, ask after each whether the lamp obeyed, report the rate"
    )
    p.add_argument("--src", required=True, type=lambda s: int(s, 0))
    p.add_argument("--count", type=int, default=10, help="number of commands")
    p.add_argument("--seq-step", type=positive_int, default=1)
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--duration", type=float, default=100)
    p.add_argument("--note", default="", help="what is special about this run, e.g. the scan window")
    p = sub.add_parser("pair", help="pairing burst: run it, then power-cycle the lamp at the wall")
    p.add_argument("--src", required=True, type=lambda s: int(s, 0))
    p.add_argument("--now", action="store_true", help="start without waiting for Enter")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    profile = load_profile(args.profile)
    if args.action == "list":
        for name, command in profile.commands.items():
            print(f"{name:20} {command.template}")
        return
    if not args.host:
        sys.exit("--host is required")
    try:
        addressing.check_sender(args.src, known_remotes())
    except ValueError as err:
        sys.exit(str(err))
    if addressing.in_integration_range(args.src) and not args.force:
        sys.exit(f"{args.src:#06x} belongs to the Home Assistant integration; use another address or --force")

    if args.action == "cmd":
        if args.what not in profile.commands:
            sys.exit(f"Unknown command {args.what!r}; see: send.py list")
        if args.what == profile.pairing.command:
            sys.exit("Use the pair action for pairing")
        template = profile.commands[args.what].template
    elif args.action == "raw":
        template = args.what
        try:
            bytes.fromhex(template.replace("TT", "80"))
        except ValueError:
            sys.exit("Payload must be hex bytes; write the transaction byte as TT")
    else:
        template = profile.commands[profile.pairing.command].template

    counters = sequence.SequenceStore(FileStore(SEQUENCE_FILE))
    await counters.async_load()
    client, service = await connect(args)
    sender = Sender(client, service, args.src, profile, counters)
    try:
        if args.action in ("cmd", "raw"):
            for i in range(args.count):
                if i:
                    await asyncio.sleep(args.interval)
                await sender.send(template, repeat=args.repeat, step=args.seq_step, duration=args.duration)
            # The board queues the adverts; give it time to send them before disconnecting.
            await asyncio.sleep(1 + args.repeat * args.duration / 1000)
        elif args.action == "trial":
            await run_trial(sender, args)
        else:
            if not args.now:
                input(
                    "Turn the lamp's wall switch OFF, wait 5 s, turn it ON. "
                    "Press Enter as soon as the lamp lights up... "
                )
            rules = profile.pairing
            print(
                f"pair: {rules.requests} requests over {rules.requests * rules.interval:.0f} s. "
                "Watch whether the lamp blinks."
            )
            for _ in range(rules.requests):
                await sender.send(template, repeat=1)
                await asyncio.sleep(rules.interval)
            await asyncio.sleep(1)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
