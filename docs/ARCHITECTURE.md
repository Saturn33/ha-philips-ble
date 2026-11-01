# Architecture

How BLE Mesh Lights works, why it is built this way, and the mistakes that were already made once. Read it before changing the integration; the user's view is in the [README](../README.md), the protocol facts in [PROTOCOL.md](PROTOCOL.md), the accepted limits in [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

## The system

```
remotes ──BLE Mesh──┐                        ┌── Home Assistant: ble_mesh_lights ─┐
lights  ──beacons───┼─► ESP32 boards ────────┤   (through the ESPHome and          ├─► lights, remotes, sensors
lights ◄──BLE Mesh──┘   (ESPHome + package   │    Bluetooth integrations)          │   the virtual remote card
                         ble-adv-tx)         └─────────────────────────────────────┘
```

- **Boards** run a stock ESPHome Bluetooth proxy plus the package [`esphome/ble-adv-tx.yaml`](../esphome/ble-adv-tx.yaml): the vendored `ble_adv_proxy` component sends raw adverts (action `adv_svc_v1`) and forwards Mesh adverts (AD `0x2A`) as the event `esphome.ble_adv.raw_adv`; continuous scanning, a scan pause while sending, a package-version sensor and a heartbeat event. The integration never talks to a board in any other way.
- **Light beacons** (power) arrive through Home Assistant's Bluetooth integration, fed by the same boards' Bluetooth proxy.
- **The integration** encrypts and decrypts Mesh itself; a board only moves bytes.
- **Profiles** (`custom_components/ble_mesh_lights/profiles/<id>/`) hold everything model-specific as data; the code knows no model. Format: [PROFILES.md](PROFILES.md).

## Modules

| Module | Responsibility |
|---|---|
| `transport.py` | The only module that talks to ESPHome boards and HA Bluetooth: send an advert, receive Mesh adverts and beacons, read the package sensor. |
| `mesh.py` | Bluetooth Mesh over the advertising bearer: unsegmented access messages, encrypt/decrypt, network and application layers. |
| `sequence.py` | Durable sequence counters, one per sender address, reserved in blocks in an HA `Store` before use. |
| `addressing.py` | HA's own sender addresses: unicast, never a remote's, never reused. |
| `profile.py` | Loads and checks profiles (`profile.yaml`, `remote.json`); one bad profile never stops the others. |
| `state.py` | What HA believes about a light, and how each command or press changes it; the scene derived from colour temperature and level. |
| `planner.py` | The shortest chain of commands from the believed state to the wanted one (breadth-first search over the profile's states). |
| `remotes.py` | Physical remotes: which press was this, a repeat from another board, how many presses were missed. |
| `beacons.py` | Whether a light has mains power, from its beacons (active and quiet mode). |
| `hub.py` | One per config entry: owns the boards, the lights, the stores; sending chains, card-press queues, presence, repairs. |
| `config_flow.py` | Setup (boards), options (settings, remotes), the light wizard as a config subentry flow, pair again. |
| `light.py`, `binary_sensor.py`, `button.py`, `event.py`, `sensor.py` | Entities. |
| `services.py`, `websocket.py`, `diagnostics.py`, `repairs.py` | Actions, the card's websocket command, the diagnostics download, repair flows. |
| `frontend/ble-mesh-remote-card.js` | The virtual remote card: one file, no build step; a pure model exported for the node tests. |

Pure modules (`mesh`, `sequence`, `addressing`, `profile`, `state`, `planner`, `remotes`, `beacons`) do not import Home Assistant and are tested in the plain Python suite (`tests/`, loaded with `tests/_load.py`); the rest is tested against a real HA in `tests/ha/`.

## Data flows

**Sending.** An HA request (light service, button, card, action) → `hub` asks `planner` for a chain of profile commands → each command: next sequence number (reserved in the store first), `mesh` encrypts from the light's own sender address → `transport` calls `adv_svc_v1` on the board that hears the light best (smoothed beacon RSSI; the last board that heard it if no recent beacon) → `state` applies the command's effect. Commands to one light are spaced by the "Minimum command interval" (default 0.5 s). The light confirms nothing, so there is no retry through another board.

**Receiving presses.** A board forwards a Mesh advert → `mesh` decrypts with the profile's keys → `remotes` drops repeats (several boards, several copies per press: the pair source + sequence), counts misses from the sequence jump (`sequence_step` per press) → the press becomes an event of the remote's device and is applied to the state of the lights bound to that remote.

**Presence.** Every beacon of a known light refreshes it; a check every 0.5 s turns a light "no power" after `active_timeout` (about 10 s) or, in quiet mode, `quiet_timeout` (190 s). A light that comes back is set to the profile's `power_on` state.

## Decisions and why

- **One sender address per light**, derived from a base address (`0x7E00` + index). A light obeys only senders it has paired with, so separate addresses give per-light control with a broadcast destination. Addresses are never reused, and the integration never sends as a remote's address (the light would then ignore the real remote: replay protection is per sender).
- **The state is believed, not measured.** The lights report nothing; every entity has `assumed_state`, and "State uncertain" says when HA knows it may be wrong (missed presses, a possible short power cut).
- **The scene is derived from colour temperature and level** (`state.scene_of`), never remembered: an on light at a scene's exact pair is in that scene, however it got there. This keeps the light's effect, the "Scene" sensor and the card consistent. Hence a profile's scenes must each set both values and no two may share them.
- **Card presses are queued, chains are not.** A new target from a slider or an automation cancels the unsent rest of the previous chain (generations in `hub._async_run`), which is right for "go to this state". A press on the card must behave like a press on the remote: three quick "brighter" are three steps, so card presses (`send_command` with `queue: true`) wait in a per-light queue of at most 10. A card press, like a real remote press, stops a chain started by HA.
- **Presence is stored across reloads and restarts**, and a held advert (HA Bluetooth's cache of the last advert) is not a sighting. Otherwise every restart would bring switched-off lights back as on for minutes.
- **Wizard texts per profile as translated step variants** (`<step>_<texts>`). A config flow does not know which user opened it, so a hint built on the server could only be in the server's language; step variants are translated by the frontend in each user's language. The cost: a new light with other gestures needs its own six keys in every translation.
- **Outage restore is deliberately simple** until a separate outage detector exists: it fires on the rising edge of a chosen sensor; the known gaps are listed in [KNOWN_ISSUES.md](KNOWN_ISSUES.md#outage-restore-is-simple-until-the-outage-detector). Several attempts to patch the edge cases each broke a neighbouring case, so the redesign waits for the detector.
- **The card is served by the integration** (a static path and `add_extra_js_url`, with a content hash in the URL), so it needs no separate frontend install and updates with the integration.
- **Profiles are data.** No per-model code: a new light is a folder, checked by a schema with errors that name the file and the key.

## Pitfalls already met

Each of these cost a release or a live check. Do not repeat them.

- **HA's ESPHome integration drops unchanged states** (`homeassistant/components/esphome/entry_data.py`, `async_update_state`: an equal state that is not stale is not written). A sensor that sends the same value every 10 s is no heartbeat. The board's heartbeat is therefore an **event** (`esphome.ble_adv_tx.heartbeat`, package version 2): HA puts every ESPHome event on the bus. A board without a heartbeat for 35 s is not used for sending.
- **A light keeps its radio on about 7 s after a switch-off at the wall** (it runs from the power supply's capacitor). A switch cycle shorter than that leaves no trace in the air, although the light has reset to its power-on state. Timeouts and the wizard's "wait about 15 seconds" follow from this.
- **Quiet mode:** 30 minutes after power-on a light beacons only once a minute, and about 1 in 20 bursts is missed; one missing burst is not a power cut.
- **A light blinks only when it stores a new sender.** Pairing an address it already knows succeeds silently, so "Pair again" checks with an off/on test instead of asking "did it blink?".
- **A light keeps at least six senders** (HA and five remotes were all accepted after one reset); the limit was not reached. Pairing never pushes out a remote in practice.
- **A remote's pairing request is identical to ours** and carries no identity: HA hears it even when the light did not accept it, so a pairing request never binds a remote; a scene press does.
- **A single-radio ESP32-C3 that scans all the time loses about 4 of 10 sends**; the scan pause while sending (`pause_scan_on_send`) fixed it. Wi-Fi roaming (`post_connect_roaming`) cut the HA connection for up to 45 s and is off.
- **The card's file loads only with the page**: a dashboard opened before the integration loaded shows "Custom element doesn't exist" until reloaded. Accepted, documented in KNOWN_ISSUES.
- **A flow does not know its user's language** (see the decision above); texts the server builds from names (a remote's button name, "New remote (…)") stay in the server language, documented in KNOWN_ISSUES.
- **The colour temperature and level sets of the scenes must not overlap**, or a derived scene would be ambiguous; the profile loader refuses it.
- **Never trust a remembered behaviour of HA**: check the installed Home Assistant source and cite it in the code comment, as above. Several assumptions about the ESPHome integration and config flows were wrong until checked.

## Where to start

- A protocol question: [PROTOCOL.md](PROTOCOL.md).
- A new light: [PROFILES.md](PROFILES.md).
- Setup, tests, releases: [CONTRIBUTING.md](../CONTRIBUTING.md).
