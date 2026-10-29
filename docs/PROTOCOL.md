# Philips Valen BLE protocol

What we found out about the Philips Valen CL650 ceiling light and its remote from 24 to 26 October 2026, on real hardware. It is written so that anyone porting this to a similar remote-only light can start from here, not from zero.

Everything below was observed on four Valen CL650 lights and four remotes in one flat, with a Seeed XIAO ESP32-C3 running ESPHome as listener and transmitter. "Observed" means captured over the air or seen on the light. Guesses are marked as guesses.

This builds on [jphelan82/ha-philips-skylight](https://github.com/jphelan82/ha-philips-skylight), which decoded the Philips LED Skylight panel. The Valen turned out to be the same platform with a different command set. Differences are listed in [Differences from the Skylight](#differences-from-the-skylight).

> **About the addresses in this document.** All device addresses (BLE MAC addresses of the lights, remotes and transmitter, and the Mesh addresses derived from them) are made-up replacements of the real ones, and the example packets were rebuilt with them. The vendor prefixes (`E8:26:CF`, `18:90:67`, `B0:A6:04`) are real: they identify the module maker, not a device.

## Contents

- [Hardware](#hardware)
- [Network layer](#network-layer)
- [Commands](#commands)
- [Light model](#light-model)
- [Pairing](#pairing)
- [Remotes](#remotes)
- [Light beacons](#light-beacons)
- [Transmitter requirements](#transmitter-requirements)
- [Worked example](#worked-example)
- [Differences from the Skylight](#differences-from-the-skylight)
- [How to repeat this for another device](#how-to-repeat-this-for-another-device)
- [Open questions](#open-questions)

## Hardware

| Part | What we saw |
|---|---|
| Light | Philips Valen CL650 AIO R. No app, Wi-Fi, Zigbee or Matter. Remote only. |
| Light radio | Advertises the name `Mesh_JX_light` (JX is probably Shenzhen Jingxun, the module maker; the Skylight's module calls itself `BK_MESH_light`). BLE address prefix `E8:26:CF`. |
| Remote | Battery Bluetooth Mesh node with a status LED. BLE address prefixes `E8:26:CF` and `18:90:67` (two of each among our four). |
| Remote buttons | ON, OFF, a ring with brightness up (top) and down (bottom) around SELECT, and five scene buttons laid out 2-1-2. |

The remotes are interchangeable: any remote can be paired with any light.

## Network layer

Standard Bluetooth Mesh over the advertising bearer, with factory keys and no provisioning:

| Item | Value | How we know |
|---|---|---|
| NetKey | `11111111111111111111111111111111` | Network ID k3(NetKey) = `c7cd5342d1544cd5` matches the remotes' Mesh Proxy adverts and secure network beacons. NID k2 = `0x1b` matches every captured message, and every NetMIC checks. |
| AppKey | `00000000000000000000000000000000` | AID k4 = `0x38` matches, and every AppMIC checks. |
| IV index | `0` | Secure network beacon. |
| Derived | encryption key `42139da87ab51ae7eddf2b389e92f5d0`, privacy key `0cbe118b7fcc238eadfa2eb373d754ee` | k2(NetKey, 0x00). |

**Addresses.** A sender (source) address must be a unicast address, `0x0001`–`0x7FFF`. Addresses from `0x8000` up are virtual (`0x8000`–`0xBFFF`) and group addresses (`0xC000`–`0xFFFF`, with `0xFFFF` = all nodes); a light will not accept them as a sender. Any address made from 16 bits of something else, such as the low bytes of a BLE address, therefore has its top bit cleared (`& 0x7FFF`).

Every command we saw is a single unsegmented access message:

- network PDU in AD type `0x2A` (Mesh Message), one per advert;
- CTL = 0, TTL = 5, destination `0xFFFF` (all nodes);
- lower transport: SEG = 0, AKF = 1, AID = `0x38`, 32-bit TransMIC;
- 25 to 31 bytes of advertising data in total.

The light does not relay. Unlike the Skylight, no TTL 4 copies ever appeared, so a relay cannot be used as proof that the light heard a packet. The light sends no status of any kind.

Replay protection is per sender address: the light drops a message whose sequence number is not above the last one it accepted from that address. This is why a controller must never reuse a sender address with a lower sequence, and must never send as a remote's address.

## Commands

All commands are Telink vendor messages (company ID `0x0211`, opcodes `0xC8`, `0xC9`, `0xCF`). `TT` is a transaction byte in `0x80..0xFF`. The remote counts it up by one per message; we send `0x80 | (sequence & 0x7F)` and the light accepted it.

| Button | Access payload | Notes |
|---|---|---|
| ON | `C8 11 02 TT 03 01 41` | Explicit on, not a toggle. |
| OFF | `C8 11 02 TT 03 00` | Explicit off. Six bytes, no trailing `41`. |
| Brightness up | `C8 11 02 TT 01 01 14 41` | One step. |
| Brightness down | `C8 11 02 TT 01 00 14 41` | One step. Holding the button repeats this about once a second. |
| SELECT | `C8 11 02 TT 0A` | Next colour temperature, in a circle. Also sets brightness to 100 %. |
| Scene 1 (spiral icon, Relax) | `C9 11 02 TT 04 01 FF 41` | |
| Scene 2 (sun icon, Bright) | `C9 11 02 TT 04 02 FF 41` | |
| Scene 3 (rings icon, Concentrate) | `C9 11 02 TT 04 03 FF 41` | |
| Scene 4 (half-filled circle, Energise) | `C9 11 02 TT 04 04 FF 41` | |
| Scene 5 (moon icon, Nightlight) | `C9 11 02 TT 04 05 FF 41` | |
| Pairing request | `CF 11 02 TT 01 11 11 11 11 11 11` | Byte for byte the same as the Skylight. See [Pairing](#pairing). |

Tested and found to do nothing on the Valen:
- `C8 11 02 TT 01 02 14 41` and `… 01 03 14 41` (the Skylight's hold codes);
- other values of the step byte (`0x14`). `05`, `14` and `50` all give exactly one step. There is no absolute brightness command that we know of.

Holding ON, OFF, SELECT or a scene button sends nothing extra. (Holding ON right after the light gets power is the remote's pairing gesture; see the remote's pairing below.)

A scene command turns an off light on. ON brings the light back with the brightness and colour temperature it had before OFF. While the light is off, SELECT and brightness up/down do nothing.

## Light model

| | |
|---|---|
| Brightness | Six fixed steps. Measured with a lux meter at each colour temperature, relative to the top step: 100 / 80 / 52 / 31 / 10.5 / 5 % (spread under 3 points between temperatures). We label them 100 / 80 / 50 / 30 / 10 / 5 %. The lowest step still glows; down never turns the light off. |
| Colour temperature | Five steps: 2200, 2700, 3000, 4000 and 6500 K. The values come from the product description; which step is which was matched by eye (warmest to coldest). We number them 1 to 5 in that order. |
| SELECT | Goes from step k to k % 5 + 1 (3 → 4 → 5 → 1 → 2 → 3), and always sets brightness to 100 %. After a scene, it continues from the scene's colour temperature. |
| Scenes | Relax: 2 (2700 K) at 50 %. Bright: 3 (3000 K) at 100 %. Concentrate: 4 (4000 K) at 80 %. Energise: 5 (6500 K) at 50 %. Nightlight: 2 (2700 K) at 10 %. (Which step and level each scene uses was judged by eye against the five temperatures and the six brightness steps.) |
| Power on | Whenever mains power returns, the light turns on at 100 % and colour temperature 3 (3000 K), even if it was switched off by remote before. |

So the state space is small: off, or on × 5 temperatures × 6 brightness steps. ON, OFF, SELECT and the scenes are absolute for the parts they set, which makes it possible to recover a known state from an unknown one in at most two commands, and to reach any state in at most seven.

## Pairing

A light obeys only senders it has paired with. An unpaired sender address is ignored. We tested this with an address that was never paired: 0 of 5 commands obeyed, while a paired address obeyed 2 of 2 right after.

To pair a new sender address:

1. Cut the light's mains power (wall switch), wait about 5 seconds, restore it. The pairing window opens after power returns. Its length was not measured; starting within a few seconds of the light coming on always worked.
2. From the new address, send the pairing request about twice a second for 10 seconds (20 requests, each with the next sequence number and a new `TT`). The light blinks when it accepts.

Observations:

- Any unicast address works. We paired `0x7FFE`, `0x7FFD` and `0x02ED` (the last one made from the transmitter's BLE address `B0:A6:04:4D:82:ED` by the remotes' rule, to test whether the light requires that). The address does not have to relate to the transmitter's BLE address.
- Each light obeys only its own paired senders. With light 1 paired to `0x7FFE` and light 2 to `0x7FFD`, each address controlled only its light. This is what makes per-light control possible.
- The real remote's pairing request is identical to ours: the same payload, about 2 requests a second, sequence +1 each. The six `11` bytes carry no remote identity.
- A light blinks only when it stores a new sender. Pairing an address it already knows is accepted without a blink (owner, 0.1.1 live check); after a factory reset the same address makes it blink again.
- A light keeps at least six senders: after a factory reset, HA's address and five remotes paired one after another all worked at the same time; none was pushed out (0.1.1 live check). The limit was not reached.
- **Pairing a remote** (owner's routine): switch the light off and on at the wall, then right away press and hold ON on the remote until the light blinks (usually 1–2 s). The remote sends the pairing request above.
- **The remote stays in pairing mode** after its own pairing: it keeps sending `CF …` requests, with its LED on, until any button on it is pressed. As long as it does, the light ignores normal commands from every sender. Pressing a remote button ends it.
- `CF 11 02 TT 02 11…` (unpair all, on the Skylight) was not tried on the Valen.

**Factory reset:** switch the light on and off quickly five times at the wall. This clears every pairing, ours and the remote's. Afterwards the remote has to be paired again.

## Remotes

- **Address.** Each remote's sender address is the low 16 bits of its BLE address with the top bit cleared, so that it is a valid unicast address (see [Addresses](#network-layer)): `E8:26:CF:6B:37:AF` sends as `0x37AF`, and `E8:26:CF:C9:89:FC` as `0x09FC` (`0x89FC` with the top bit cleared). Different remotes therefore have different addresses, so a listener can tell which remote was pressed. The Skylight remote always sends as `0x7FFF`.
- **Sequence.** Advances by 10 per press (2280, 2290, 2300, …). A held brightness-down button adds +1 per repeat within the press. A jump of 20 or more means a press was missed.
- **Burst.** A press goes out as one burst of 1 to 4 copies within 1 to 3 ms, alongside Mesh Proxy adverts (service `0x1828`, node identity) and secure network beacons (AD `0x2B`). Even listening 100 % of the time, our ESP32-C3 caught about 85 % of presses (11 of 13, 7 of 8, 20 of 22 in three sessions). The radio is shared with Wi-Fi, and a burst that short is lost if it lands in a Wi-Fi slot.
- **Rapid presses.** Once, after a series of fast presses, the light ignored the remote for about a minute although every press reached our listener. The cause is unknown. A guess is flood protection in the light.

## Light beacons

A powered light advertises all the time, from its own BLE address:

| Advert | Content |
|---|---|
| Mesh beacon, AD `0x2B` | Unprovisioned device beacon: `00` + UUID + OOB `0000`. UUID = `00 FF FF FF FF 31 01` + the light's BLE address reversed (`AD 65 54 CF 26 E8` for `E8:26:CF:54:65:AD`) + `F 00 01` (F = flag, see below). |
| Connectable advert | Flags, service UUID `0x1827` (Mesh Provisioning), service data `0x1827` with the same UUID and OOB, name `Mesh_JX_light`, manufacturer data `0xFF01`: `FF FF FF FF` + reversed BLE address. |

**Quiet mode.** For 30 minutes after mains power returns, the light sends both adverts back to back (gaps under 1 s). Exactly 30 minutes after power-on, it changes:

- the connectable advert with the name stops;
- the mesh beacon is sent only once a minute, as a short burst of about 3 copies;
- the flag byte in the UUID changes from `00` to `01`.

The beacons do not change with on/off, brightness, colour temperature or pairing. They only tell whether the light has power. In quiet mode a listener has to wait out missed bursts: in one 40-minute capture about 1 in 20 quiet-mode bursts was not heard, so the integration waits 190 s (three bursts) before it counts the light as switched off. Five quick switch flicks (a factory reset) leave gaps under 3 seconds.

**Switching off at the wall.** Measured through Home Assistant on 2026-10-29 (light in active mode, one board, adverts logged every 0.5 s):

- the radio keeps advertising for **about 7 s** after the switch is turned off, with the same adverts and signal, although the light is already dark (it runs from the power supply's capacitor);
- after the switch is turned on again, the first advert arrives **within a second**, in active mode;
- the light itself always comes back in its power-on state (3000 K, 100 %), even after an off time of about one second.

So a switch cycle shorter than about 7 s leaves no trace in the air: the adverts go on without a gap and a light in quiet mode stays in quiet mode, but the light has reset to 3000 K / 100 %. (An earlier capture during the research saw a switch-off within 1 to 2 seconds; the 7 s here are from the live check with the integration.) How the integration uses this: [KNOWN_ISSUES.md](KNOWN_ISSUES.md#a-quick-switch-cycle-at-the-wall-is-not-seen).

## Transmitter requirements

What made our ESP32-C3 transmitter work, in order of importance:

1. **Non-connectable advertising** (`ADV_NONCONN_IND`), 20 ms interval, about 100 ms per copy, 3 copies per command. Connectable adverts are ignored. This is the change jphelan82 made to NicoIIT's [esphome-ble_adv_proxy](https://github.com/NicoIIT/esphome-ble_adv_proxy).
2. **Pause BLE scanning while sending.** On a single-radio ESP32-C3, a continuous scan (window = interval = 1100 ms) let only 6 of 10 commands reach the light, and broke OTA updates. With a 30/320 ms scan, 10 of 10 arrived. Our `pause_scan_on_send` option stops the scan before advertising and resumes it 150 ms after the queue empties. That gives reliable sending and continuous listening at the same time.
3. **Continuous scanning otherwise.** With ESPHome's default scan (30 of every 320 ms), most remote presses are missed.
4. **No Wi-Fi roaming.** `post_connect_roaming: false`. Roaming every five minutes breaks the API connection for up to 45 s (found by jphelan82).
5. **One sender address per light**, each with its own sequence counter that only goes up and is saved before use.
6. **A heartbeat from the board** (package version 2). Home Assistant notices a lost ESPHome API connection only after up to a minute, and an action called on such a board is accepted and lost without an error. The board fires the event `esphome.ble_adv_tx.heartbeat` every 10 s (Home Assistant adds the board's `device_id`), and the integration stops sending through a board whose last heartbeat is older than 35 s. It is an event because Home Assistant's ESPHome integration drops a re-sent unchanged sensor state, while it puts every ESPHome event on the bus. Judging a board by the BLE traffic it forwards does not work: where only quiet-mode lights are in range, a healthy board forwards nothing for a minute.

ESPHome 2026.10 note: after `esp32_ble_tracker.stop_scan()`, setting `scan_continuous` back to true is not enough to restart the scan, because the tracker's loop only re-checks on a scanner state change. Call `start_scan_if_idle()` as well.

## Worked example

The OFF button of a remote, as received. The remote's address in this document is a made-up replacement (see the note at the top); the packet was rebuilt with it from a real capture, keeping the command, sequence and TTL.

```
advertising data  19 2A 1B 2E 29 B7 B4 97 85 8E D1 3D BE 72 07 0D EC 16 74 D7 B8 3D A5 B2 3E D5
                  │  │  │  └──────────────┘ └───────────────────────────────────────────────────┘
                  │  │  │  obfuscated header       encrypted DST + transport PDU + NetMIC
                  │  │  IVI = 0, NID = 0x1B
                  │  AD type 0x2A (Mesh Message)
                  length 25
```

1. The privacy random is the first 7 bytes after the obfuscated header (`8E D1 3D BE 72 07 0D`). XOR the 6 header bytes with the first 6 bytes of AES(privacy key, `00 00 00 00 00` + IV index + privacy random):
   header = `05 00 08 E8 37 AF` → CTL 0, TTL 5, SEQ 2280, SRC `0x37AF`.
2. Decrypt the rest with AES-CCM (encryption key, 4-byte MIC), network nonce `00` + header + `00 00` + IV index:
   DST `0xFFFF`, then the lower transport byte `0x78` (SEG 0, AKF 1, AID `0x38`).
3. Decrypt the upper transport PDU with AES-CCM (AppKey, 4-byte MIC), application nonce `01 00` + SEQ + SRC + DST + IV index:
   access payload `C8 11 02 81 03 00` → OFF, transaction `0x81`.

A short Python implementation (about 100 lines, `cryptography` only) is [`custom_components/ble_mesh_lights/mesh.py`](../custom_components/ble_mesh_lights/mesh.py). For research, [`tools/air.py`](../tools/air.py) also says *why* a packet does not decrypt (other NetKey, other AppKey, segmented).

## Differences from the Skylight

| | Skylight | Valen |
|---|---|---|
| Light name | `BK_MESH_light` | `Mesh_JX_light` |
| Keys | factory NetKey `11…`, AppKey `00…`, IV 0 | the same |
| Power | one toggle `C8 … 03 01 41` | ON `C8 … 03 01 41` and OFF `C8 … 03 00` |
| Brightness | taps `01 00/01 14 41`, holds `01 02/03 14 41` | taps only; holds repeat the tap |
| Colour temperature | none | SELECT `C8 … 0A`, five steps |
| Scenes | `C9 … 04 FF 0N 41`, N = 1..6 | `C9 … 04 0N FF 41`, N = 1..5 (bytes swapped) |
| Pairing | `CF … 01 11×6`, after power-on | the same |
| Remote address | always `0x7FFF` | from the remote's BLE address, different per remote |
| Remote sequence | +1 per message | +10 per press |
| Relaying | the panel relays with TTL 4 | no relaying |
| Beacon after 30 min | not reported | quiet mode, once a minute |

## How to repeat this for another device

What worked, step by step, with the tools in [`tools/`](../tools/README.md) (`capture.py`, `analyze.py`, `send.py`):

1. **Listen first, send nothing.** Flash an ESP32 as an ESPHome Bluetooth proxy with continuous scanning, keep it out of Home Assistant, and stream raw adverts to a script over the ESPHome API (`subscribe_bluetooth_le_raw_advertisements`). Record everything to a file, with markers typed during the session ("R1 off", "light 1 switched off").
2. **Look for the mesh fingerprints.** A light that advertises service `0x1827` or `0x1828`, AD types `0x29`, `0x2A` or `0x2B`, or a name with MESH in it is probably Bluetooth Mesh. Read the network ID from a `0x1828` advert or a secure network beacon and compare it with k3 of candidate keys: a match tells you the NetKey without pressing anything.
3. **Press every button with a marker**, twice each, and decode with the candidate keys. A matching NID with a failing NetMIC means another NetKey or IV index. A failing AppMIC means another AppKey.
4. **Map the commands** from the decoded payloads, and the light's behaviour from what you saw it do. Measure brightness steps with a lux meter if you need percentages.
5. **Find the pairing gesture** in the manual and capture the remote doing it. Note that the remote may stay in pairing mode afterwards.
6. **Send from your own address only after pairing it**, never from a remote's address, and with a counter that only goes up.
7. **Measure delivery** with an off/on trial: each command, did the light obey, yes or no. If it is not close to 10 of 10, look at your transmitter's radio time first.

If no Mesh traffic appears when buttons are pressed but new non-mesh adverts do, the device uses another protocol. [NicoIIT/ha-ble-adv](https://github.com/NicoIIT/ha-ble-adv) supports many such Chinese light protocols. If nothing at all appears, the remote is probably not Bluetooth (IR or proprietary 2.4 GHz).

## Open questions

- Is two really the limit of paired senders, and which one is dropped by a third?
- Does `CF … 02 …` unpair all senders, as on the Skylight?
- How long does the pairing window stay open after power-on?
- What caused the light to ignore the remote for a minute after rapid presses, and how fast can commands safely be sent?
- What do the bytes `FF FF FF FF 31 01` and `00 01` in the beacon UUID mean, and does the quiet-mode flag affect anything else?
