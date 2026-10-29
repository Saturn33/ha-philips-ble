"""Report on air captures: which devices speak Bluetooth Mesh, with which keys and commands.

    python tools/analyze.py captures/*.jsonl [--profile valen] [--window 5] > report.md

Input: JSONL from tools/capture.py (records with "kind": "adv" | "marker" | "meta")
or the test fixture format (adverts only, with a "file" field).

Sections:
  Q1 mesh devices and their presence gaps     Q2 do messages decrypt with the profile's keys?
  Q3 senders (one per remote)                 Q4 pairing requests
  Q5 command payloads, named from the profile Markers: what appeared right after each marker
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

import air
from _package import load_profile

STATE_FILE = Path(__file__).resolve().parent / "state" / "sequence.json"


def load_records(paths: list[Path]) -> tuple[list[dict], list[dict]]:
    adverts, markers = [], []
    for path in paths:
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            kind = record.get("kind", "adv")
            record.setdefault("file", path.name)
            if kind == "adv":
                record["data"] = bytes.fromhex(record["raw"])
                record["info"] = air.parse_advert(record["data"])
                adverts.append(record)
            elif kind == "marker":
                markers.append(record)
    adverts.sort(key=lambda r: (r["file"], r["t"]))
    markers.sort(key=lambda r: (r["file"], r["t"]))
    return adverts, markers


def clock(t: float) -> str:
    # Captures hold epoch seconds; the fixture holds seconds since the start of each file.
    return time.strftime("%H:%M:%S", time.localtime(t)) if t > 1e9 else f"{t:8.1f}s"


def table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    return "\n".join(lines + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows])


def marker_label(markers: list[dict], record: dict, window: float) -> str:
    label = ""
    for m in markers:
        if m["file"] == record["file"] and m["t"] <= record["t"] <= m["t"] + window:
            label = m["label"]
    return label


def own_senders() -> set[int]:
    if not STATE_FILE.exists():
        return set()
    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {int(k, 16) for k in data.get("addresses", {})}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--profile", default="valen")
    parser.add_argument("--window", type=float, default=5.0, help="seconds after a marker that belong to it")
    parser.add_argument("--gap", type=float, default=8.0, help="silence that counts as a presence gap, s")
    args = parser.parse_args()

    profile = load_profile(args.profile)
    adverts, markers = load_records(args.files)
    if not adverts:
        print("No adverts in the capture.")
        return
    own = own_senders()
    out: list[str] = []
    say = out.append

    say(f"# Air report ({profile.name})\n")
    say(f"Files: {', '.join(p.name for p in args.files)}  ")
    say(f"{len(adverts)} adverts, {len({a['mac'] for a in adverts})} addresses, {len(markers)} markers  ")
    nid = air.mesh._k2(profile.net_key)[0]
    network_id = air.mesh._k3(profile.net_key)
    aid = air.mesh._k4(profile.app_key)
    say(f"Profile keys: NID {nid:#04x}, network ID {network_id.hex()}, AID {aid:#04x}\n")

    # Q1: mesh-related devices
    by_mac: dict[str, list[dict]] = defaultdict(list)
    for a in adverts:
        by_mac[a["mac"]].append(a)
    mesh_macs = {mac: recs for mac, recs in by_mac.items() if any(r["info"].mesh_related for r in recs)}
    say("## Q1. Mesh devices\n")
    if not mesh_macs:
        say("**No mesh-related adverts.** See the marker section for anything that appears on presses.\n")
    rows = []
    net_ids: Counter = Counter()
    for mac, recs in sorted(mesh_macs.items(), key=lambda kv: -len(kv[1])):
        names = {r["info"].name for r in recs if r["info"].name}
        kinds: set[str] = set()
        for r in recs:
            info = r["info"]
            kinds |= {air.describe_beacon(b).split(" uuid")[0].split(" net_id")[0] for b in info.mesh_beacons}
            kinds |= {f"svc {u:#06x}" for u in info.service_data if u in (0x1827, 0x1828)}
            if info.mesh_pdus:
                kinds.add("mesh msg")
            for found in air.network_ids_in(info):
                net_ids[(mac, found.hex())] += 1
        rssis = [r["rssi"] for r in recs if r.get("rssi") is not None]
        gaps = [
            (p, n)
            for p, n in zip(recs, recs[1:], strict=False)
            if p["file"] == n["file"] and n["t"] - p["t"] > args.gap
        ]
        gap_text = "; ".join(f"{clock(p['t'])}→{clock(n['t'])} ({n['t'] - p['t']:.0f}s)" for p, n in gaps[:6])
        rows.append(
            [
                mac,
                ", ".join(sorted(names)) or "—",
                ", ".join(sorted(kinds)),
                len(recs),
                f"{statistics.median(rssis):.0f}" if rssis else "—",
                (gap_text + (" …" if len(gaps) > 6 else "")) or "none",
            ]
        )
    if rows:
        say(table(["address", "name", "what", "adverts", "RSSI med", f"gaps > {args.gap:.0f}s"], rows))
        say("")
    if net_ids:
        say("### Network IDs\n")
        for (mac, found), n in sorted(net_ids.items()):
            verdict = next(
                (label for label, key in air.CANDIDATE_NETKEYS.items() if air.mesh._k3(key).hex() == found),
                "unknown NetKey",
            )
            say(f"- {mac}: `{found}` ×{n} → {verdict}")
        say("")

    # Q2..Q5: mesh messages
    diagnosed: list[tuple[dict, air.Diagnosis]] = []
    errors: Counter = Counter()
    seen = set()
    for a in adverts:
        for pdu in a["info"].mesh_pdus:
            if (pdu, a["mac"]) in seen:
                continue
            seen.add((pdu, a["mac"]))
            d = air.diagnose(profile.net_key, profile.app_key, pdu, profile.iv_index)
            if d.error:
                errors[d.error] += 1
            diagnosed.append((a, d))
    decoded = [(a, d) for a, d in diagnosed if d.payload is not None]

    say("## Q2. Do mesh messages decrypt with the profile's keys?\n")
    if not diagnosed:
        say("No mesh messages (AD 0x2A) captured.\n")
    else:
        say(f"{len(diagnosed)} distinct mesh messages, **{len(decoded)}** decrypted to an access payload.  ")
        if errors:
            say("Failures: " + ", ".join(f"{e} ×{c}" for e, c in errors.most_common()))
        say("")

    say("## Q3. Senders\n")
    if decoded:
        per_source = defaultdict(list)
        for a, d in decoded:
            per_source[d.source].append((a, d))
        rows = []
        for source, items in sorted(per_source.items()):
            sequences = [d.sequence for _, d in items]
            labels = Counter(marker_label(markers, a, args.window) for a, _ in items)
            labels.pop("", None)
            rows.append(
                [
                    f"`{source:#06x}`",
                    "ours (send.py)" if source in own else "",
                    len(set(sequences)),
                    f"{min(sequences)}–{max(sequences)}",
                    ", ".join(f"{m}×{c}" for m, c in Counter(a["mac"] for a, _ in items).most_common(3)),
                    ", ".join(f"{label}×{c}" for label, c in labels.most_common(4)) or "—",
                ]
            )
        say(table(["source", "note", "messages", "sequence", "advert addresses", "markers"], rows))
        say("")
    else:
        say("No decrypted messages.\n")

    say("## Q4. Pairing requests\n")
    pairing = profile.commands[profile.pairing.command]
    requests = [(a, d) for a, d in decoded if pairing.matches(d.payload)]
    if requests:
        for source, count in Counter(d.source for _, d in requests).items():
            say(f"- source `{source:#06x}`: {count} requests")
        say("")
    else:
        say(f"No `{profile.pairing.command}` requests captured.\n")

    say("## Q5. Command payloads\n")
    if decoded:
        commands: dict[str, dict] = {}
        for a, d in decoded:
            key = d.payload[:3].hex() + d.payload[4:].hex()
            entry = commands.setdefault(
                key, {"first": d.payload, "count": 0, "sources": set(), "labels": Counter()}
            )
            entry["count"] += 1
            entry["sources"].add(d.source)
            entry["labels"][marker_label(markers, a, args.window)] += 1
        rows = []
        for entry in sorted(commands.values(), key=lambda e: -e["count"]):
            entry["labels"].pop("", None)
            rows.append(
                [
                    f"`{entry['first'].hex()}`",
                    profile.identify(entry["first"]) or "**new**",
                    air.opcode_summary(entry["first"]),
                    entry["count"],
                    ", ".join(f"{s:#06x}" for s in sorted(entry["sources"])),
                    ", ".join(f"{label}×{c}" for label, c in entry["labels"].most_common(3)) or "—",
                ]
            )
        say(table(["payload (first)", "command", "opcode", "count", "sources", "markers"], rows))
        say("")
    else:
        say("No decrypted commands.\n")

    say("## Markers\n")
    if not markers:
        say("No markers.\n")
    for m in markers:
        window = [a for a in adverts if a["file"] == m["file"] and m["t"] <= a["t"] <= m["t"] + args.window]
        lines = []
        for a in window:
            for pdu in a["info"].mesh_pdus:
                d = air.diagnose(profile.net_key, profile.app_key, pdu, profile.iv_index)
                text = (
                    (
                        f"src={d.source:#06x} seq={d.sequence} ttl={d.ttl} {d.payload.hex()} "
                        f"[{profile.identify(d.payload) or '?'}]"
                    )
                    if d.payload is not None
                    else d.error
                )
                if text not in lines:
                    lines.append(text)
        say(f"### {clock(m['t'])} {m['label']}\n")
        say("\n".join(f"- {line}" for line in lines[:12]) or "- no mesh messages")
        say("")

    print("\n".join(out))


if __name__ == "__main__":
    main()
