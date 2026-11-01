# Notes for coding agents

**What this is:** a Home Assistant custom integration (`custom_components/ble_mesh_lights`) for Bluetooth Mesh ceiling lights with remotes (first profile: Philips Valen), driven through ESP32 boards running ESPHome with the package in `esphome/`, plus a Lovelace card served by the integration (`frontend/ble-mesh-remote-card.js`).

**Read first:**

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): modules, data flows, decisions and the pitfalls already met (most bugs here came from wrong assumptions about Home Assistant or the lights).
- [CONTRIBUTING.md](CONTRIBUTING.md): setup, tests, rules, releases.
- [docs/PROTOCOL.md](docs/PROTOCOL.md) for protocol facts, [docs/PROFILES.md](docs/PROFILES.md) for the profile format, [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) for accepted limits.

**Rules that must not be broken:**

- No personal data in any file or commit: no real MAC addresses, keys, Wi-Fi details, IP addresses, home address or e-mail. Use placeholders.
- Verify Home Assistant behaviour in the installed Home Assistant source before relying on it, and cite file and function.
- Tests first; all suites green before a commit.
- Never send as a remote's Mesh address; never reuse a sender address with a lower sequence number.
- No model-specific code: what differs between lights belongs in a profile (the card's `valen:` icons are the one exception).
- Documentation and code in English; UI texts only through `strings.json` and `translations/` (en, ru, es).

**Test commands** (repository root):

```bash
ruff check .
pytest -q
pytest tests/ha -q -o addopts=""      # Home Assistant environment (requirements-test-ha.txt, Python 3.14)
node --test tests/card/*.mjs
```
