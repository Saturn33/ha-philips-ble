# Contributing

Thanks for helping. Issues are welcome for bugs, new lights and ideas (the forms ask for what is needed). For code, read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first: it explains how the parts fit and which mistakes were already made. A new light is mostly data: see [docs/PROFILES.md](docs/PROFILES.md).

## Development setup

Two Python environments, because the Home Assistant test package pins its own Python and Home Assistant version:

```bash
# Pure modules, tools, ruff (Python 3.12 or later)
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt

# Home Assistant suite (Python 3.14; pytest-homeassistant-custom-component pins HA 2026.10.4)
python3.14 -m venv .venv-ha
.venv-ha/bin/pip install -r requirements-test-ha.txt
```

Node.js 24 runs the remote card's tests; nothing to install. Board firmware is built with ESPHome (see [esphome/README.md](esphome/README.md)).

## Tests

Run all of them before a pull request; CI runs the same on every push:

```bash
ruff check .                              # lint (line length 110)
pytest -q                                 # pure modules, profiles, tools, docs, CI files
pytest tests/ha -q -o addopts=""          # against a real Home Assistant (in .venv-ha)
node --test tests/card/*.mjs              # the remote card (list the files: a folder argument fails on Node 24)
```

CI also runs:

- **hassfest** (`home-assistant/actions/hassfest`) and **HACS validation** (`hacs/action`, category integration, no checks skipped);
- an **ESPHome compile** of `esphome/local-node.yaml` with placeholder secrets, when anything under `esphome/` changes.

Write the test first, watch it fail, then make it pass. Documentation has tests too (`tests/test_docs.py`): what the guides must mention and that every relative link resolves.

## Trying it on a real Home Assistant

[`tools/deploy_dev.sh`](tools/deploy_dev.sh) copies the integration to a development Home Assistant over SSH and restarts it (`--no-restart` to only copy, then reload the entry). Copy `tools/deploy.env.example` to `tools/deploy.env` (git-ignored) and fill in the SSH login of the HA SSH add-on. After changing texts or the card, reload the page with Ctrl+Shift+R.

## Rules

- **No personal data in the repository, ever:** no real MAC addresses of your devices, no keys or tokens, no Wi-Fi details, no IP addresses of your network, no home address, no e-mail. Use made-up values in tests and examples (`AA:BB:CC:…`, `192.0.2.x`). Check every commit for personal data before you push it; captures (`captures/`) and `tools/state/` are git-ignored for this reason.
- **Check Home Assistant's behaviour in its source**, not from memory, and cite the file and function in a comment where the code relies on it (see the pitfalls in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#pitfalls-already-met)).
- **English** for code, comments and documentation. UI texts go through `strings.json` and `translations/` (en = strings, plus ru and es; `tests/ha/test_translations.py` checks they are complete).
- **Never send as a remote's address**, and never reuse a sender address with a lower sequence: a light ignores such a sender afterwards. The research tools enforce this; keep it that way.
- Keep the vendored ESPHome component (`esphome/components/ble_adv_proxy/`) in its upstream style; ruff skips it.

## Releases

1. Raise `version` in `custom_components/ble_mesh_lights/manifest.json`. The tag must be the same version with a `v` in front.
2. If the board package changed: raise `ble_adv_tx_version` in `esphome/ble-adv-tx.yaml` and `REQUIRED_PACKAGE_VERSION` in `const.py` (boards with an older package then get a repair asking for new firmware).
3. Point `esphome/example-node.yaml` (both references), `esphome/README.md` and the README at the new tag; the tests check that all three match the manifest version.
4. Commit, tag `v<version>`, push the commit and the tag.
5. Create a **GitHub release** for the tag (a tag alone is not a release for the Marketplace / HACS). Its text is what users see in Home Assistant's update dialog: put anything they must do first (a changed option, a removed entity, new board firmware), then the changes. Mark it as a pre-release to test with users who opted in.

HACS refreshes its data about every 4 hours and the Marketplace reads it about every 6 hours, so a new release can take that long to appear.

## Maintainer notes

The public repository is `Saturn33/ha-philips-ble`. Its GitHub settings matter for the HACS validation:

- a description, **topics** (`home-assistant`, `hacs`, `ble-mesh`, `philips`, `esphome`) and **issues** enabled;
- push `main` and the release tags `v*` only. The maintainer's local branch `private` and its tags `private/*` hold the private working history and are never pushed.

Getting into the default catalog later: a pull request to `hacs/default` adding the repository to the `integration` file (alphabetical), from a personal account, with links to the latest release and to passing hassfest and HACS action runs.
