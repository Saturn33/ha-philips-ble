## What and why

<!-- What this changes and why. Link the issue if there is one. -->

## Checks

- [ ] `ruff check .` and `pytest -q`
- [ ] `pytest tests/ha -q -o addopts=""` (Home Assistant test environment, see CONTRIBUTING.md)
- [ ] `node --test tests/card/*.mjs` (if the remote card changed)
- [ ] Checked for personal data: no real MAC addresses, keys, Wi-Fi details, IP addresses or home address in the code, tests, captures or screenshots
- [ ] For a new profile: tested with a real light (what was checked)
