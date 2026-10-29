"""tools/deploy_dev.sh: copies the integration to the HA VM and restarts HA (with fake ssh/rsync/curl)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "deploy_dev.sh"
DEST = "/config/custom_components/ble_mesh_lights"


@pytest.fixture
def fake(tmp_path):
    """A bin directory with ssh, rsync and curl that log their arguments."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "log"
    for name in ("ssh", "rsync", "curl"):
        tool = bin_dir / name
        tool.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\ncat > /dev/null\nexit 0\n')
        tool.chmod(0o755)
    env_file = tmp_path / "deploy.env"
    env_file.write_text('HA_SSH="root@ha.example"\nHA_SSH_PORT=2222\n')
    return bin_dir, log, env_file


def _run(fake, *args, **env):
    bin_dir, log, env_file = fake
    environment = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DEPLOY_ENV": str(env_file),
        **env,
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), *args], env=environment, capture_output=True, text=True, timeout=30
    )
    lines = log.read_text().splitlines() if log.exists() else []
    return result, lines


def test_requires_env_file(fake, tmp_path):
    result, _ = _run(fake, DEPLOY_ENV=str(tmp_path / "missing.env"))
    assert result.returncode == 1
    assert "deploy.env.example" in result.stderr


def test_syncs_with_rsync_and_restarts_over_ssh(fake):
    result, lines = _run(fake, DEPLOY_SYNC="rsync")
    assert result.returncode == 0, result.stderr
    (rsync,) = [line for line in lines if line.startswith("rsync ")]
    assert "--delete" in rsync and "--exclude=__pycache__" in rsync
    assert "ssh -p 2222" in rsync
    assert rsync.endswith(f"custom_components/ble_mesh_lights/ root@ha.example:{DEST}/")
    assert lines[-1] == "ssh -p 2222 root@ha.example ha core restart"


def test_falls_back_to_tar_over_ssh(fake):
    result, lines = _run(fake, DEPLOY_SYNC="tar")
    assert result.returncode == 0, result.stderr
    assert not any(line.startswith("rsync ") for line in lines)
    assert lines[0].startswith("ssh -p 2222 root@ha.example ")
    assert f"rm -rf '{DEST}'" in lines[0] and "tar -C" in lines[0]


def test_no_restart(fake):
    result, lines = _run(fake, "--no-restart", DEPLOY_SYNC="rsync")
    assert result.returncode == 0, result.stderr
    assert not any("restart" in line for line in lines)


def test_restart_through_api_when_token_is_set(fake):
    _, _, env_file = fake
    env_file.write_text(
        env_file.read_text() + 'HA_URL="http://ha.example:8123"\nHA_TOKEN="not-a-real-token"\n'
    )
    result, lines = _run(fake, DEPLOY_SYNC="rsync")
    assert result.returncode == 0, result.stderr
    assert lines[-1].startswith("curl ")
    assert "http://ha.example:8123/api/services/homeassistant/restart" in lines[-1]


def test_unknown_option_rejected(fake):
    result, _ = _run(fake, "--bogus")
    assert result.returncode == 2


def test_settings_file_is_not_committed():
    assert "tools/deploy.env" in (ROOT / ".gitignore").read_text().splitlines()
    example = (ROOT / "tools" / "deploy.env.example").read_text()
    assert "HA_SSH=" in example and "HA_TOKEN=" in example


def test_deploy_quotes_the_destination(fake):
    result, lines = _run(fake, DEPLOY_SYNC="tar")
    assert result.returncode == 0, result.stderr
    assert f"rm -rf '{DEST}'" in lines[0] and f"mkdir -p '{DEST}'" in lines[0]
