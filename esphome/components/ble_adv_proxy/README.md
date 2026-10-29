# Mesh transport changes

Vendored from [NicoIIT/esphome-ble_adv_proxy](https://github.com/NicoIIT/esphome-ble_adv_proxy) at
[`d7412240e7edd21b5717b3c6d3235aa1aeaf64cf`](https://github.com/NicoIIT/esphome-ble_adv_proxy/tree/d7412240e7edd21b5717b3c6d3235aa1aeaf64cf),
under the adjacent MIT LICENSE.

Local changes: use non-connectable advertisements for the Bluetooth Mesh bearer,
wait for GAP configuration/start/stop completion callbacks, and advertise each
burst at a 20ms interval. Duplicate-packet expiry compares times by difference,
so it keeps working after `millis()` rolls over at about 49 days. The runtime
patch of ESPHome's `api` schema and the support code for ESPHome releases older
than 2026.4 are removed. `skylight-transmitter.yaml` sets the two `api` options
that the patch used to force on. The raw API actions and receive event format
are unchanged. The proxy forwards only adverts that carry a Bluetooth mesh
message (AD type 0x2A). Upstream forwards every advert it hears, which puts
every nearby phone and watch into Home Assistant's event log.
The local component targets ESPHome 2026.4.5.

Scan pause (this repository): optional `pause_scan_on_send`. On an
ESP32-C3 a continuous scan (window = interval) leaves too little radio time for
the adverts: in tests only 6 of 10 commands reached the lamp. With the option on,
the component stops the esp32_ble_tracker scan before advertising, waits up to
500 ms for it to stop, and resumes continuous scanning 150 ms after the queue
empties.

Nodes normally load this component straight from git; see `esphome/README.md`.
