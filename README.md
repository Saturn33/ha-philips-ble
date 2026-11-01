# BLE Mesh Lights

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Saturn33&repository=ha-philips-ble&category=integration)
[![Open your Home Assistant instance and start setting up BLE Mesh Lights.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ble_mesh_lights)

A Home Assistant integration for ceiling lights that come with a Bluetooth remote and no app or hub, such as the **Philips Valen** series. Home Assistant talks to them through a small ESP32 board running ESPHome, the way the remote does: over Bluetooth Mesh.

Status: **beta** (0.x). It works daily with Philips Valen lights; other lights that use the same kind of remote may work, see [Does it work with my lights?](#does-it-work-with-my-lights).

## What it does

- **Lights in Home Assistant:** on/off, brightness steps, colour temperature, the remote's five scenes (Relax, Bright, Concentrate, Energise, Nightlight), in dashboards, automations and voice (Assist), with areas and aliases.
- **Remotes as devices:** every press on a real remote is an event in Home Assistant (for automations), and the light's state in Home Assistant follows the press.
- **A virtual remote card** for dashboards that looks like the real remote and can control one light or several at once.
- **Power from the wall switch:** a light switched off at the wall shows as having no power; with an optional outage sensor, lights can return to their last state after a power cut.

What it cannot do, because the lights cannot tell: the light never reports its state, so Home Assistant keeps the state it last sent or saw; a remote press that no board heard leaves the state wrong until the next command; brightness goes in the light's own steps, not to any exact percentage. The full list, with what each limit means for you, is in [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).

## What you need

- **Home Assistant 2026.10.0** or later, with the **ESPHome** integration (it comes with Home Assistant).
- **An ESP32-C3 board** within a few metres of your lights. The tested one is the **Seeed XIAO ESP32-C3**, which needs its external antenna (included) connected. Other ESP32-C3 or ESP32-S3 boards should work but are untested. One board per part of your home; two or more give better coverage.
- A USB-C data cable (not a charge-only one) and a computer with Chrome or Edge for the first flash, then any 5 V USB power supply for the board.
- Your lights' remotes.

You do not need to know ESPHome: the steps below are complete. The board keeps working as an ordinary Bluetooth proxy for Home Assistant, so other Bluetooth sensors work through it too.

## 1. Prepare the board

The board needs ESPHome firmware with this project's package, `ble-adv-tx`. The detailed guide, with every option and troubleshooting, is [esphome/README.md](esphome/README.md); the short path:

1. **Connect the antenna** to the board before powering it.
2. **Install the ESPHome Device Builder** in Home Assistant (Settings → Apps / Add-ons → ESPHome Device Builder) and open it.
3. **Create a new device**, skip the wizard's own setup, and replace the device's YAML with the content of [esphome/example-node.yaml](esphome/example-node.yaml). Change `name:` and `friendly_name:` to something that says where the board is, for example `ble-proxy-tx-kitchen`. The file takes the package from this repository's release:
   ```yaml
   packages:
     ble_adv_tx: github://Saturn33/ha-philips-ble/esphome/ble-adv-tx.yaml@v0.5.0
   ```
4. **Fill in the secrets** (Device Builder, top right menu → Secrets): `wifi_ssid` and `wifi_password` for your 2.4 GHz Wi-Fi, an `api_key` (generate one as [esphome/README.md](esphome/README.md#secrets) shows) and a `fallback_password` of at least 8 characters.
5. **Flash it:** Install → "Plug into this computer", connect the board by USB, and follow the steps. If you would rather flash without the Device Builder, build the image and flash it from the browser at https://web.esphome.io, as [esphome/README.md](esphome/README.md#build-and-flash) describes. If the board's port does not appear, hold its BOOT button while plugging it in.
6. **Add the board to Home Assistant:** it shows up under Settings → Devices & services → Discovered → ESPHome. Enter the `api_key`. The board's diagnostic sensor **BLE TX package** shows `2`.
7. Recommended: reserve the board's IP address in your router (DHCP reservation), so over-the-air updates always find it.

Later firmware updates go over Wi-Fi (Install → Wirelessly). When an integration update needs new board firmware, Home Assistant shows a repair that says so.

## 2. Install the integration

**Home Assistant 2026.11 and later (Marketplace):**

1. Use the first badge at the top of this page, or go to **Settings → Marketplace**, select **Add from link** in the upper-right corner, enter `Saturn33/ha-philips-ble` and select **Add**. The Marketplace asks once for a GitHub connection (a code to enter on GitHub).
2. On the repository's page select **Install**, check the version, and select **Install** again. The integration's setup opens right away.

**Home Assistant 2026.10 (HACS):**

1. Open HACS, the ⋮ menu in the upper-right corner → **Custom repositories**. Enter `https://github.com/Saturn33/ha-philips-ble`, type **Integration**, **Add**.
2. Find **BLE Mesh Lights** in HACS, **Download**, then restart Home Assistant.
3. Settings → Devices & services → **Add integration** → BLE Mesh Lights (or use the second badge).

**Setup:** choose the ESPHome boards you prepared (a board without the `ble-adv-tx` package is refused with a message). You can change them later in the integration's options.

## 3. Add a light

1. **Factory-reset the light first** (Philips Valen: switch it off and on at the wall five times quickly). A light only pairs with a new controller after a reset or when it is new.
2. Settings → Devices & services → BLE Mesh Lights → **Add lamp**. Choose the device type (Philips Valen), a name, an area and, if you like, aliases for Assist.
3. The wizard asks you to **switch the light off at the wall, wait about 15 seconds, and switch it on**. Home Assistant pairs with it as soon as it appears; the light **blinks** to confirm.
4. **Test:** Home Assistant switches the light off and on. Confirm that it did.
5. **Bind its remote** (optional, recommended): if the remote is not paired with the light yet, the wizard shows how (Valen: switch the light off and on, then hold ON on the remote until the light blinks; release and press OFF). Then press any scene button: Home Assistant shows which remote it heard. From then on, presses on that remote update the light in Home Assistant and appear as events.

Repeat for every light. Each light needs its own wall-switch cycle, so do them one at a time.

**Pair again:** after a light was reset (or lost its pairing), go to Settings → Devices & services → BLE Mesh Lights, open the light's ⋮ menu → **Reconfigure** → **Pair again**. Its address, entities and automations stay.

Before you delete a light from Home Assistant, reset it: the integration never sends "unpair all", so a deleted but not reset light keeps listening to Home Assistant's old address.

## 4. The virtual remote card

Add a card to a dashboard and pick **BLE Mesh remote** from the list, or in YAML:

```yaml
type: custom:ble-mesh-remote-card
entities:
  - light.living_room
  - light.hallway
name: Downstairs        # optional
layout: vertical        # vertical | horizontal | mini
hide_header: false
```

The card controls all its lights at once and shows their state: colour temperature, brightness, the current scene, and "No power" for a light switched off at the wall. Every press is delivered, even quick repeated ones. The small LED in the corner shows the result: green, sent to every light; yellow, to some (the message says which and why); red, to none. Tap the header to open the light (or the list of the card's lights).

For a phone, a **mini** card fits; use Home Assistant's **Screen** visibility condition to show it instead of a vertical one on small screens.

## Does it work with my lights?

The integration ships one profile, **Philips Valen**. Other lights that came with a similar Bluetooth remote (no app, no hub, a remote with scene buttons) may use the same protocol. To try:

- **It works** when, while adding the light, the light blinks on pairing and the test passes. Press the remote's buttons afterwards: they should show as events on the remote's device.
- **Partly works** (pairing works, some commands do nothing, scenes differ): the light needs its own profile.
- **Nothing happens** on pairing: the light probably speaks another protocol, or needs a different pairing gesture.

Either way, open a **New light or profile request** issue: the form asks for exactly what is needed. If you want to go further yourself, [docs/PROFILES.md](docs/PROFILES.md) explains how to research a light with the tools in this repository and write a profile; [docs/PROTOCOL.md](docs/PROTOCOL.md) is what is known about the protocol.

## Troubleshooting

| Problem | What to do |
|---|---|
| The setup offers no board, or a board counts as not ready | Check the board's **BLE TX package** sensor: it must show `2` and not be unavailable. More in [esphome/README.md](esphome/README.md#troubleshooting). |
| The light did not blink while adding it | It was not reset, or it is too far from every board. Reset it and try again; move a board closer. |
| The page looks old or a text is missing after an update | Reload the page without the cache: **Ctrl+Shift+R** (Cmd+Shift+R on a Mac); in the app, clear the frontend cache in its settings. |
| "Custom element doesn't exist: ble-mesh-remote-card" after a restart | The page was opened before the integration loaded. Reload the page (F5). |
| A light's power ("No power" or not) is out of date for a few minutes | A light that has had power for over 30 minutes sends its beacon only once a minute, so the integration waits about 3 minutes before it counts the light as switched off at the wall, and sees it back at its next beacon. |
| The state is wrong after using the real remote | A press was missed on the air (the remote sends one short burst). Use the remote again or set the state from Home Assistant; a second board helps. |

Diagnostics for an issue: Settings → Devices & services → BLE Mesh Lights → ⋮ → Download diagnostics.

## Credits and licence

- The Bluetooth Mesh work for Philips lights builds on [jphelan82/ha-philips-skylight](https://github.com/jphelan82/ha-philips-skylight).
- The board's sending component is [NicoIIT/esphome-ble_adv_proxy](https://github.com/NicoIIT/esphome-ble_adv_proxy) (with the Mesh fixes from the Skylight project), included under its licence in `esphome/components/ble_adv_proxy/`.

Philips is a trademark of its owner; this project is not affiliated with or endorsed by it.

Licensed under the [MIT licence](LICENSE). Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).
