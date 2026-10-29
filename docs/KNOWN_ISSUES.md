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

## A board's Wi-Fi drop marks active-mode lights uncertain

A light in active mode (the first 30 minutes after it gets power) is watched for silences of the "Short power cut detection" threshold (3 s). If the only board that hears it loses Wi-Fi or reconnects to Home Assistant for that long, the silence looks the same as a quick switch cycle, and "State uncertain" turns on although nothing happened to the light.

**Why it stays:** the integration cannot tell a silent light from a silent board within a few seconds; the board's heartbeat comes every 10 s.

**When it matters:** a board with a weak Wi-Fi signal. Pick a scene to clear the flag, improve the board's Wi-Fi, or raise the threshold in the options.

## Commands sent just after a board is unplugged are lost

For up to about 35 s after a board loses power or Wi-Fi, its heartbeat is not yet overdue, so commands still go through it. The board's action is accepted without an error, the commands never reach the air, and Home Assistant applies them to the light's state anyway.

**Why it stays:** nothing tells sooner that the board is gone (Home Assistant itself notices the lost connection only after up to a minute).

**When it matters:** if a light did not follow a command right after a board was unplugged, use "Set assumed state" or pick a scene. A second board that hears the light takes over after about 35 s.

## A missed remote press leaves the state wrong without "State uncertain"

A remote press is a single burst of 1–3 ms, and some are lost on the air (about 85 % were caught in the research). A missed relative press (brightness up/down) shows up at the next press as a gap in the remote's sequence and marks the state uncertain. A missed absolute press (a scene, select, on or off) is not noticed until the next press of that remote: until then Home Assistant shows the previous state as certain.

**Why it stays:** the light reports nothing; only the remote's next message reveals the gap.

**When it matters:** after a press that Home Assistant did not follow, press the button again or correct the state. A second board in another place catches more presses.

## The 24-bit message counter can run out

Each lamp's sender address has a 24-bit counter (16.7 million messages). Normal use takes many years, but "Skip sequence forward" jumps 1,048,576 each time, and the repair for lost counters starts at 8,388,608. Repeated skips or repairs can exhaust the space, after which the lamp refuses to send.

**Why it stays:** a counter must never go back, and the light remembers the highest value per address.

**When it matters:** when sending fails with "Sequence space … is exhausted". Delete the lamp, factory-reset the light (five quick switch cycles) and add it again: it gets a new address with a fresh counter.

## Outage restore is simple until the outage detector

With an outage sensor in the options and a lamp set to "Restore previous state": when the sensor turns on (from any other state), or is `on` at the integration's start with a `last_changed` under 10 minutes old, every such lamp that comes back within 10 minutes is driven to the state it had before. Three review rounds tried to cover more cases and each broke neighbouring ones; without a real outage sensor nothing could be checked live. Known gaps, as input for the redesign:

- A second HA restart, or a reload of the entry, within 10 minutes of an outage restores again — from the assumed power-on state (3000 K, 100 %), although the lamps did not lose power that time (HA keeps no `last_changed` across a restart).
- A sensor whose device drops off the network and comes back `on` counts as a new outage.
- A sensor that stays `on` (for example "mains present") makes every HA restart an outage.
- A reload drops the pending restores of lamps that are not back yet.
- A restore uses relative commands from the assumed power-on state, so a lamp that did not lose power ends up at a wrong level.
- The sensor must turn on before the lamps are seen back. A lamp seen back first is taken as plainly powered on (3000 K, 100 %), and a later `on` of the sensor restores nothing. A sensor that reports only after the lamps are back (for example a Wi-Fi or Zigbee device that boots with the mains) makes the option useless while HA keeps running; it works when HA itself restarts after the outage.

**Why it stays:** the outage detector (a separate integration, planned) decides how the sensor behaves. The redesign comes with it: first a table of all cases (sensor states and transitions × HA starting / running / entry reload × the sensor reporting before or after the lamps are back), agreed and reviewed before any code, then a test per row that simulates a real HA start.

**When it matters:** use the option only with a sensor that turns on when mains power comes back, before the lamps are back, and turns off again afterwards (a pulse), or leave the lamps at "Do nothing".


## The wizard's reset and remote hints follow the server language

The factory-reset hint and the remote's pairing steps come from the lamp's profile and are filled into the wizard's text by the server, in Home Assistant's server language (Settings → System → General). The rest of the wizard follows each user's profile language. With the two languages different, the hints show in the other language: for example English steps inside the Russian "Pair again" and remote screens. A flow does not know which user opened it, so the server cannot pick the user's language.

**Why it stays:** the fix (a set of translated step variants per profile) is planned for the next version; 0.2.0 has one profile.

**When it matters:** users whose profile language differs from the server language. Until then, set both to the same language.
