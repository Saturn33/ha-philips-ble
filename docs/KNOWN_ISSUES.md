# Known issues

Accepted limitations of the current code, each with the reason it stays for now and what to do when it starts to matter. Limits of the hardware itself (missed remote presses, no feedback from the light, the pairing window) are in [PROTOCOL.md](PROTOCOL.md).

## Profiles assume the Telink vendor command layout

A command template must have the transaction byte `TT` as its 4th byte, and the transaction runs from `0x80` to `0xFF`. That is how the Philips Valen and Skylight remotes work (Telink vendor opcodes `C8`/`C9`/`CF` + company ID `0x0211`), and it is checked when a profile is loaded.

**Why it stays:** both known devices use this layout. A general template language would be guesswork without a second, different device.

**When it matters:** a device with another opcode layout or no transaction byte. Then the template format (`profile.Command.payload`, `matches`, and the check in `_template`) needs a field for the transaction position and range.

## "Does this command turn the light on" is not a profile field

A command that sets both colour temperature and brightness (a scene) is assumed to turn the light on, and power commands are `on`, `off` or `toggle`. The spec mentions a per-command "turns the light on" flag; the profile has none.

**Why it stays:** every Valen scene turns the light on (checked from off), and no other kind of command needs the flag.

**When it matters:** a device whose scenes work only while the light is on. Add an optional `turns_on` to the effect and read it in `state.apply`.

## A profile object cannot be hashed

`Profile` is a frozen dataclass that holds a dict of commands, so `hash(profile)` raises.

**Why it stays:** nothing needs it. Code that caches or indexes by profile should use `profile.id`.

**When it matters:** if a cache keyed by the profile object is wanted, store the commands as a tuple and build the lookup separately.

## Address allocation does not avoid earlier test addresses

`addressing.allocate()` skips `0x7FFF` and every known remote, but not addresses that were used by hand during research (for example with `tools/send.py`). With the default base `0x7E00` this cannot happen: lamp addresses are `0x7E01`–`0x7EFF`, and the tools refuse that range unless `--force`.

**Why it stays:** a factory reset of the light (five quick switch cycles) clears every pairing, which is the advice before pairing a light with the integration anyway. A list of "used once" addresses would need to be kept somewhere by hand.

**When it matters:** a non-default base that overlaps addresses you paired by hand. Factory-reset the light, or pick a base outside them.

## A quick switch cycle at the wall is not seen

A light keeps advertising for about 7 s after it is switched off at the wall, and comes back in its power-on state (3000 K, 100 %) after any switch cycle, however short (see [PROTOCOL.md](PROTOCOL.md#light-beacons)). The integration sees a cycle only from the air:

| Off for | What the integration does |
|---|---|
| under about 7 s | nothing: the adverts never stop, so the cycle is invisible. HA keeps showing the old state, not marked uncertain. |
| about 7–17 s, light in quiet mode | the radio restarted, so its beacons are in active mode again: the light is set to its power-on state. |
| about 10–17 s, light in active mode | a silence of at least the "Short power cut detection" threshold (3 s): "State uncertain" turns on, the state is kept. |
| longer (active mode), or longer than 190 s (quiet mode) | the light counts as switched off; when it is back, it is set to its power-on state. |

**Why it stays:** nothing in the air tells a short cycle apart from no cycle at all; the adverts before and after are the same.

**When it matters:** after a quick flick of the switch, HA shows the light in its previous state while it is at 3000 K / 100 %. Pick a scene, or use the action "Set assumed state", to bring HA back in line. A physical switch that is part of the automation (a smart relay) could report the cycle instead.
