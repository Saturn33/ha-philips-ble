# Device profiles

A profile describes one kind of light: how to talk to it, what its commands do, how it pairs and how to tell from the air that it has power. The integration ships one, **Philips Valen**, in [`custom_components/ble_mesh_lights/profiles/valen/`](../custom_components/ble_mesh_lights/profiles/valen/profile.yaml). Every other kind of light needs its own folder there: `profile.yaml` and, optionally, `remote.json` (how the virtual remote card draws its remote).

This guide is for adding a light. If you would rather send the data and let someone else write the profile, open a **New light or profile request** issue (the `new_light` form): it asks for the captures described in [Research](#research-is-it-bluetooth-mesh-and-what-does-it-send).

## When a light needs a profile

- **The Valen profile works** (the light blinks on pairing, the wizard's test passes, presses show up): no new profile needed. If only the model name differs, say so in an issue so the README can list it.
- **Pairing works, but some commands do nothing or do something else** (other scenes, other brightness steps, a toggle instead of on/off): a new profile with the same network keys and different `commands`.
- **Nothing reacts**: first find out whether the light speaks Bluetooth Mesh at all (next section). The integration supports Bluetooth Mesh lights with Telink-style vendor commands only (see [docs/KNOWN_ISSUES.md](KNOWN_ISSUES.md#profiles-assume-the-telink-vendor-command-layout)).

## Research: is it Bluetooth Mesh, and what does it send?

The tools in [`tools/`](../tools/README.md) talk to a board running the `ble-adv-tx` package over the ESPHome API; [tools/README.md](../tools/README.md) has the commands and the safety rules. The method that found the Valen protocol is in [docs/PROTOCOL.md](PROTOCOL.md#how-to-repeat-this-for-another-device); in short:

1. **Listen** with `capture.py`, all lights on, two minutes: does each light advertise a Mesh beacon (AD type `0x2B`, service `0x1827`)? Switch each one off and on at the wall with markers.
2. **Press every remote button twice**, each after a marker, and run `analyze.py`. It tells whether the messages decrypt with a profile's keys (start with `--profile valen`: many such lights ship with the same factory keys), and gives the command table.
3. **Capture the pairing gesture** from the manual, and check that the light blinks when it accepts a new sender.
4. **Send** with `send.py` from your own test address only after pairing it, and measure delivery with `send.py trial`.

Captures contain every Bluetooth device nearby. Keep them out of git (`captures/` and `tools/state/` are ignored) and remove your neighbours' devices before you share one.

## `profile.yaml`, field by field

The Valen profile is the worked example; every field is required unless marked optional. A mistake makes the profile fail to load: Home Assistant shows a repair naming the file and the key, and the other profiles keep working.

### Identity

```yaml
id: valen
name: Philips Valen series
texts: valen
manufacturer: Philips
model: Valen ceiling light
```

- `id`: the folder name and the internal key (stored with every light; never change it once released).
- `name`: shown in the wizard's "Device type" list.
- `manufacturer`, `model`: shown on the light's device page.
- `texts` (optional, defaults to `id`): the name of the wizard's **text set**: the reset and remote-pairing hints of this light. They live in the integration's translations as step variants `remote_pair_<texts>`, `pair_again_<texts>`, `settings_<texts>`, `test_failed_<texts>` and aborts `reset_needed_<texts>`, `reset_needed_again_<texts>`, so each user sees them in their own language (a configuration flow does not know which user opened it, so the server could not translate them itself). A light paired and reset like the Valen can say `texts: valen`; otherwise add the six keys under a new name to `strings.json` and every file in `translations/` (`tests/ha/test_translations.py` checks that every text set is complete). Only `a-z`, `0-9` and `_`.

### Network: `network`

```yaml
network: {net_key: "1111…1111", app_key: "0000…0000", iv_index: 0, ttl: 5, destination: 0xFFFF}
```

- `net_key`, `app_key`: the Mesh network and application keys, 16 bytes as hex. The Valen uses fixed factory keys; `analyze.py` tells you whether a light's remote uses the same ones.
- `iv_index`: the Mesh IV index (0 for the Valen).
- `ttl`: the time-to-live of sent messages.
- `destination`: the address commands are sent to; `0xFFFF` (all nodes) for the Valen. Which light obeys is decided by the sender address it was paired with.

### Light model

```yaml
brightness_levels: [100, 80, 50, 30, 10, 5]
color_temps: [2200, 2700, 3000, 4000, 6500]
power_on: {power: "on", color_temp: 3000, level: 100}
```

- `brightness_levels`: the light's own brightness steps in percent, **brightest first**. Home Assistant can only move between them, one step per command.
- `color_temps`: the colour temperatures in kelvin, **ascending**, in the order the light cycles through them.
- `power_on`: the state the light is in whenever mains power returns (an effect, see below). The integration uses it after a switch-on at the wall.

### Commands: `commands`

```yaml
commands:
  "on":              {payload: "C8 11 02 TT 03 01 41", effect: {power: "on"}}
  brightness_up:     {payload: "C8 11 02 TT 01 01 14 41", effect: {brightness_step: 1}}
  color_temperature: {payload: "C8 11 02 TT 0A", effect: {color_temp_step: 1, level: 100}}
  relax:             {payload: "C9 11 02 TT 04 01 FF 41", effect: {power: "on", color_temp: 2700, level: 50, scene: relax}}
  pairing:           {payload: "CF 11 02 TT 01 11 11 11 11 11 11"}
```

Each command has a name (used in `scenes`, `events`, `pairing`, `remote.json` and the `ble_mesh_lights.send_command` action) and:

- `payload`: the Mesh access payload as hex bytes: opcode (3 bytes, Telink vendor layout), then `TT` as the **4th byte**, exactly once (the transaction byte, filled in on every send), then the parameters; at most 11 bytes. The same template recognises the remote's presses, whatever their `TT`.
- `effect` (optional): what the command does to the light's state, so that Home Assistant can follow it without feedback from the light. One of these shapes:
  - a **scene**: `color_temp` + `level` (absolute), optionally `power: "on"`, and `scene` (informational only: since 0.3.0 the scene a light is in is told from its colour temperature and level, and named after the command listed in `scenes`);
  - **power alone**: `power` is `"on"`, `"off"` or `"toggle"`;
  - **next colour temperature**: `color_temp_step` (+1 = next in `color_temps`, wrapping), optionally a `level` it also sets;
  - **one brightness step**: `brightness_step` (+1 brighter, −1 dimmer).

  `level` must be one of `brightness_levels`, `color_temp` one of `color_temps`. A command without `effect` (like `pairing`) changes nothing in Home Assistant's idea of the light.

### Scenes, events, remote: `scenes`, `events`, `remote`

```yaml
scenes: [relax, bright, concentrate, energise, nightlight]
events: ["on", "off", brightness_up, …, pairing]
remote: {address: mac_low15, sequence_step: 10}
```

- `scenes`: the commands that are scenes, in the order the light's effect list shows them. **The scene is told from colour temperature and level**: every scene command must set both, and no two scenes may set the same pair; a light that gets to 2700 K / 50 % by any route is in Relax.
- `events`: the commands a remote press can be; each becomes an event type of the remote's "Button press" entity.
- `address` (under `remote`): how a remote's Mesh sender address follows from its Bluetooth address: `mac_low15` (the low 15 bits of the MAC, as on the Valen), or one fixed unicast address (`0x0001`–`0x7FFF`).
- `sequence_step` (under `remote`): how far a remote's sequence number advances per press (10 on the Valen: one press is a burst of 10 messages). The integration uses it to count presses it missed.

### Pairing: `pairing`

```yaml
pairing: {command: pairing, requests: 20, interval: 0.5, after_power_on: true}
```

- `command`: the command that asks the light to accept a new sender.
- `requests`, `interval`: how many requests to send and how many seconds apart (20 × 0.5 s on the Valen, like its remote).
- `after_power_on`: `true` if the light accepts pairing only right after it gets power: the wizard then asks the user to switch it off and on at the wall and pairs as soon as its beacon appears.

### Beacon: `beacon`

```yaml
beacon: {ad_type: 0x2B, uuid_prefix: "0000ffffffff3101", mac_offset: 8, flag_offset: 14, service_uuid: 0x1827, active_timeout: 10, quiet_timeout: 190}
```

How the integration sees from the air that a light has power (see "Light beacons" in [docs/PROTOCOL.md](PROTOCOL.md#light-beacons)):

- `ad_type`: the advert type of the light's beacon (`0x2B`, the Mesh unprovisioned device beacon).
- `uuid_prefix`: the hex bytes the beacon starts with (beacon type and the fixed part of the UUID); it tells this kind of light from other devices.
- `mac_offset`: where the light's reversed Bluetooth address starts in the beacon; it tells the lights apart.
- `flag_offset`: where the quiet-mode flag is (`00` while the light is in its first 30 minutes after power-on and beacons several times a second, `01` afterwards, beacon once a minute).
- `service_uuid` (optional): a 16-bit service whose service data carries the same UUID in the light's connectable advert (`0x1827` on the Valen), a second way to hear an active light.
- `active_timeout`, `quiet_timeout`: seconds of silence after which the light counts as switched off at the wall, in active and in quiet mode. A light keeps advertising about 7 s after it loses power, and quiet-mode bursts are sometimes missed, so `quiet_timeout` covers three bursts.

## `remote.json`

Without it the card shows a plain row of every command of the profile. With it the card looks like the remote. Example (Philips Valen, shortened):

```json
{
  "buttons": {
    "on": {"label": "ON"},
    "color_temperature": {"label": "SELECT", "short": "SEL"},
    "brightness_up": {"icon": "valen:brighter"},
    "relax": {"icon": "valen:relax"}
  },
  "groups": {
    "power": {"kind": "row", "buttons": ["on", "off"]},
    "ring": {"kind": "ring", "up": "brightness_up", "down": "brightness_down", "center": "color_temperature"},
    "scenes": {"kind": "grid", "columns": 3, "cells": [["relax", 1, 1], ["concentrate", 3, 1], ["nightlight", 2, 2]]},
    "scene_row": {"kind": "row", "buttons": ["relax", "concentrate", "nightlight", "bright", "energise"]}
  },
  "layouts": {
    "vertical": [["power", "ring", "scenes"]],
    "horizontal": [["power", "scene_row"], ["ring"]],
    "mini": [["bar", "scene_row"]]
  }
}
```

- **`buttons`** — how a command's button looks: `label` (text), `short` (text in the mini layout and narrow rows), `icon` (`valen:<name>`, drawn by the card, or `mdi:<name>`, a Home Assistant icon). A command without an entry shows its translated name. An unknown `valen:` icon falls back to the label.
- **`groups`** — named groups of buttons:
  - `row`: `buttons` in a row; wraps when narrow, except in the mini layout unless the group sets `"wrap": true` (the plain grid of a profile without `remote.json` does);
  - `ring`: the remote's round key, `up`, `down` and `center` commands;
  - `grid`: `columns` and `cells`, each `[command, column, row]`, 1-based, rows unbounded.
- **`layouts`** — `vertical`, `horizontal` and `mini`, all three required. Each is a list of columns, each column a list of groups top to bottom; a group at most once per layout.

Every command in `buttons` and `groups` must be a command of the profile and not its pairing command; a mistake makes the profile fail to load with the file and key named, like an error in `profile.yaml`.

Valen's own icons: `valen:brighter`, `valen:dimmer`, `valen:relax`, `valen:concentrate`, `valen:nightlight`, `valen:bright`, `valen:energise`.

## Testing a new profile

1. **The schema tests:** `pytest tests/test_profile.py` loads every profile in the repository (`test_repo_profiles_load`): a new folder that fails to load fails the test with the file and the key.
2. **A fixture for behaviour that differs:** profiles used only by tests live in [`tests/fixtures/profiles`](../tests/fixtures/profiles/) (for example `toggle`, a light with one power toggle). Add one when your light needs code paths the Valen does not use, and a test for them.
3. **A live check** with the real light, through the wizard: pairing (blink), the test, every remote button (Developer tools → Events or the remote's device page), every command from Home Assistant, the scenes from both directions, a switch-off at the wall (the light shows "No power" within `active_timeout` / `quiet_timeout`), and a factory reset followed by **Pair again**.

The development setup and the test commands are in [CONTRIBUTING.md](../CONTRIBUTING.md).


## Contributing a profile

- **By pull request:** the profile folder (and `remote.json` if you made one), the text set in the translations if it is new, and what you checked live. Profiles inside the integration's folder are replaced by every update, so a profile you only keep locally disappears with the next update: send it upstream.
- **By issue:** the "New light or profile request" form (`new_light`) asks for the model, what works with the Valen profile, captures of each button, beacon samples and how the light behaves. Remove your neighbours' devices from captures, and never paste your Wi-Fi password or the board's API key.
