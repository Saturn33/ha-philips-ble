# The board: ESPHome package `ble-adv-tx`

The integration sends and receives through one or more ESP32 boards running ESPHome. Each board is an ordinary Bluetooth proxy with one addition, the `ble-adv-tx` package, which lets it send raw BLE adverts. This page covers the hardware, building and flashing, and adding the board to Home Assistant.

## What the package adds

A stock ESPHome Bluetooth proxy only listens. The package adds:

- **Sending raw BLE adverts.** The `ble_adv_proxy` component (from [NicoIIT/esphome-ble_adv_proxy](https://github.com/NicoIIT/esphome-ble_adv_proxy), with the Mesh fixes from [jphelan82/ha-philips-skylight](https://github.com/jphelan82/ha-philips-skylight)) registers the actions `adv_svc_v1` and `setup_svc_v0`, and reports received Bluetooth Mesh messages as `esphome.ble_adv.raw_adv` events.
- **A scan pause while sending.** A single-radio ESP32-C3 that listens all the time loses about 4 of 10 commands. The scan stops while an advert goes out and resumes 150 ms after the last one.
- **Continuous listening** (scan window = interval = 1100 ms), because a remote press is a single burst of 1 to 3 ms.
- **`post_connect_roaming: false`.** Wi-Fi roaming every five minutes would cut the Home Assistant connection for up to 45 s.
- **A diagnostic sensor, "BLE TX package"**, with the package version. The integration compares it with the version it needs and raises a repair issue when the board needs new firmware.
- **A heartbeat** (from version 2): the event `esphome.ble_adv_tx.heartbeat` every 10 s. The integration stops sending through a board whose last heartbeat is older than about 35 s. Without it, Home Assistant notices an unplugged board only after up to a minute, and commands sent through it meanwhile are lost. The recorder stores these events like any other; to leave them out of the database, add to `configuration.yaml`:
  ```yaml
  recorder:
    exclude:
      event_types:
        - esphome.ble_adv_tx.heartbeat
  ```

Everything else keeps working as on any Bluetooth proxy: BTHome sensors, Xiaomi thermometers with custom firmware and other passive devices reach Home Assistant through the same board.

## Hardware

- **Seeed XIAO ESP32-C3** is the tested board. Other ESP32-C3 or ESP32-S3 boards should work with a different `board:` in the node, but are untested.
- **Connect the U.FL antenna before powering the board.** The XIAO ESP32-C3 has no antenna on the board: without the external one there is no Wi-Fi and no Bluetooth.
- Any 5 V USB power supply. Place the board within a few metres of the lights it should reach; a second board elsewhere in the home improves coverage (the integration uses whichever board hears each light best).

## The node

The node is the board's own ESPHome file. There are two starting points in this folder; both give the same board:

- [`example-node.yaml`](example-node.yaml) takes the package and the component from a GitHub release (`@v0.1.0`). Use it once the release exists.
- [`local-node.yaml`](local-node.yaml) takes them from this folder of a downloaded copy of the repository. Use it **until the first release** (the release tag appears at the end of the integration's Plan B; before that, building `example-node.yaml` fails, and git asks for a GitHub login), or when you change the package yourself.

1. Copy one of them **in this folder** under the board's name, one file per board:
   ```bash
   cd esphome
   cp example-node.yaml ble-proxy-tx-kitchen.yaml   # from the release
   cp local-node.yaml ble-proxy-tx-kitchen.yaml     # or: from this copy (before the first release)
   ```
   `local-node.yaml` refers to `ble-adv-tx.yaml` and `components/` next to it, so its copy must stay in this folder. Files named `ble-proxy-tx-*.yaml` here are ignored by git.
2. Rename the node inside the copy: `name: ble-proxy-tx-<place>` and `friendly_name: BLE Proxy TX (<Place>)`, for example `ble-proxy-tx-kitchen`. One name per board.
3. With `example-node.yaml`: keep the two release references (`@v0.1.0` in `packages:` and `ref: v0.1.0` in `external_components:`) equal: the package and the component must come from the same release.
4. Keep `bluetooth_proxy: active: true` if Home Assistant should also connect to other Bluetooth devices through this board; set `false` if the board only serves lights.

## Secrets

Copy [`secrets.yaml.example`](secrets.yaml.example) to `secrets.yaml` next to the node YAML (in this folder) and fill it in:

- `wifi_ssid`, `wifi_password`: your 2.4 GHz network;
- `api_key`: generate it once with `python3 -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"`. It encrypts the API and over-the-air updates; Home Assistant asks for it when you add the board. A board that already runs ESPHome accepts an over-the-air update only with the key it was built with: reuse that key to update it without USB;
- `fallback_password`: at least 8 characters, for the fallback access point (see Troubleshooting).

Never commit `secrets.yaml`.

## Build and flash

Pick one way.

**ESPHome Device Builder (add-on in Home Assistant).** Create a new device, replace its YAML with your copy of `example-node.yaml`, and put the secrets into the Device Builder's own `secrets.yaml` (top right menu → Secrets). Install → "Plug into this computer" for the first flash over USB, or "Wirelessly" once the board runs ESPHome. (`local-node.yaml` needs the files of this folder, which the add-on does not have: before the first release, use the command line.)

**ESPHome command line.**

```bash
pip install esphome                          # or in a virtual environment: python3 -m venv .venv && .venv/bin/pip install esphome
cd esphome
esphome run ble-proxy-tx-kitchen.yaml        # builds, then flashes over USB or over the air
```

In WSL, keep the build on the Linux disk (`export ESPHOME_DATA_DIR=~/.cache/esphome`): a build under `/mnt/c` is many times slower.

The first build downloads the ESP-IDF toolchain (about 1 GB) and takes 10–20 minutes.

**First flash from a browser (web.esphome.io).** Build the factory image (`esphome compile …`; it appears as `firmware.factory.bin` in the build folder), connect the board by USB-C with a data cable, open https://web.esphome.io in Chrome or Edge, choose Connect → the board's port → Install, and pick the image. If the port does not appear, hold the BOOT button while plugging the board in.

**Later updates go over the air:** `esphome upload ble-proxy-tx-kitchen.yaml --device <board IP>` (or `esphome run`, which builds first), or Install → Wirelessly in the Device Builder.

## Add the board to Home Assistant

1. Home Assistant finds the board by itself (Settings → Devices & services → Discovered → ESPHome); otherwise add the ESPHome integration with the board's IP and port 6053. Enter the `api_key`.
2. Check the board's diagnostic sensor **BLE TX package**: it shows the package version (`2` for this release).
3. Reserve the board's IP address in your router (DHCP reservation). Home Assistant would find the board again by name, but OTA updates and tools from other machines (for example WSL, which cannot resolve `.local` names) use the IP.

The board is now ready to be chosen in the BLE Mesh Lights integration.

The ESPHome option **Allow the device to perform Home Assistant actions** is not needed: it only lets the board call Home Assistant actions, which this package never does. Sending (Home Assistant calls the board's actions) and remote presses (events) work without it.

## Troubleshooting

| Symptom | What to do |
|---|---|
| The board does not join Wi-Fi on channel 12 or 13 | Add `fast_connect: true` under `wifi:` (it connects without scanning) and flash over USB again. |
| The board cannot join Wi-Fi at all | After about a minute it opens the **fallback** access point "BLE Proxy TX Fallback" (password `fallback_password`). Connect to it and open http://192.168.4.1 to enter the Wi-Fi details. |
| No Wi-Fi, no Bluetooth, nothing in the logs about scanning | The antenna is not connected (XIAO ESP32-C3). |
| An over-the-air update stops at about 50 % | The radio is busy listening. Retry once; if it fails again, flash over USB. |
| A capture tool gets no adverts | Another API client (usually Home Assistant) holds the advert stream: only one client can receive it. |
| A board counts as not connected although it is online | Its **BLE TX package** sensor must be enabled, and on package version 2 the heartbeat must arrive: Developer tools → Events, listen to `esphome.ble_adv_tx.heartbeat`, one event about every 10 s per board. |
| The board's log shows `event 'esphome.ble_adv_tx.heartbeat' dropped` every 10 s | Expected while Home Assistant is not connected to the board (no API client to send the heartbeat to). It stops once Home Assistant connects. |
| Remote presses never reach Home Assistant | Check that the board is connected (its **BLE TX package** sensor is not unavailable). The integration asks the board to forward presses each time it connects; reloading the BLE Mesh Lights entry asks again. |
