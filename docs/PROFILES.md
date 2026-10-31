# Device profiles

A profile describes one kind of light: `custom_components/ble_mesh_lights/profiles/<id>/profile.yaml` (commands, scenes, pairing, beacon) and, optionally, `remote.json` next to it — how the virtual remote card draws the light's remote. The full profile guide comes with the publishing version; this page describes `remote.json` only.

**Scenes (since 0.3.0):** the scene a lamp is in is told from its colour temperature and level, so every command listed in `scenes:` must set both, and no two scenes may set the same pair; otherwise the profile fails to load. **Texts (since 0.4.0):** `texts: <name>` names the wizard's text set — the step variants `remote_pair_<name>`, `pair_again_<name>`, `settings_<name>`, `test_failed_<name>` and the aborts `reset_needed_<name>`, `reset_needed_again_<name>` in the integration's translations, holding the light's reset and remote-pairing hints (defaults to the profile id).

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
