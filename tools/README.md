# Research tools

Tools for finding out whether a remote-only light speaks Bluetooth Mesh, and for controlling it before the Home Assistant integration exists. They talk to a board running the [`ble-adv-tx` package](../esphome/README.md) over the ESPHome API, and take every device detail (keys, commands, pairing) from a profile in `custom_components/ble_mesh_lights/profiles/`. What we found with them for the Philips Valen is in [docs/PROTOCOL.md](../docs/PROTOCOL.md).

```bash
pip install -r tools/requirements.txt
export ESPHOME_API_KEY="<the board's API key>"
```

Keep the board **out of Home Assistant** while capturing (or disable its ESPHome entry): only one API client can receive the raw advert stream.

| Tool | What it does |
|---|---|
| `capture.py --host IP --out captures/NAME.jsonl` | Listens only. Writes every advert to a file and shows Mesh traffic live, decoded with the profile's keys. Type a label + Enter to drop a marker ("R1 off"), `q` to stop. `--source log` works while HA is connected (Mesh messages only). |
| `analyze.py captures/*.jsonl` | Report: Mesh devices and their presence gaps, whether messages decrypt (and why not), one sender per remote, pairing requests, the command table, and what appeared after each marker. |
| `send.py --host IP cmd off --src 0x7FFE` | Sends a profile command from your own sender address. Also `raw "C8 11 02 TT 03 00"`, `pair` (pairing burst after power-on), `trial` (off/on with a y/n question after each, to measure delivery) and `list`. |

## Stage A: listen

1. `capture.py` with all lights on, 2 minutes: every light should appear with its beacon.
2. Switch each light off and on at the wall with markers: you learn which address is which light, and that power loss is visible.
3. Press every remote button twice, each after a marker. `analyze.py` then answers: do the packets decrypt with the profile's keys, does each remote have its own sender address, which payload is which button.
4. Capture a remote's pairing gesture. Note that a Valen remote stays in pairing mode until one of its buttons is pressed.

## Stage B: send

1. `send.py cmd off --src <address>` before pairing: the light must ignore it.
2. `send.py pair --src <address>`, then switch the light off and on at the wall and press Enter as soon as it lights up. It blinks when it accepts.
3. `send.py trial --src <address> --count 10`: aim for 10 of 10. If not, look at the board's radio time first (`docs/PROTOCOL.md`, Transmitter requirements).
4. With a second light paired to a second address, check that each address controls only its own light.

## Safety rules

- **Never send as a remote's address.** `send.py` refuses `0x7FFF` and every address listed in `tools/state/remotes.json` (`["0x37af", …]`). Sending as a remote makes its light ignore the real remote.
- **Keep out of the integration's addresses.** `0x7E01`–`0x7EFF` belong to the Home Assistant integration's lamps; `send.py` refuses them unless `--force`. Use addresses such as `0x7FFE` and `0x7FFD` for experiments.
- **Never delete `tools/state/sequence.json`.** It holds each sender address's counter. A light ignores a sender whose sequence goes back.
- **Captures contain your neighbours' devices.** `captures/` and `tools/state/` are git-ignored; keep it that way.
