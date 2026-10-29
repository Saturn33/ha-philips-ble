"""Research tools: the analyzer runs on the fixture with the Valen profile."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_analyze_fixture():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "analyze.py"), str(ROOT / "tests/fixtures/valen_air.jsonl"),
         "--profile", "valen"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "0x37af" in result.stdout
    assert "relax" in result.stdout


def test_send_rejects_non_positive_seq_step():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "send.py"), "--host", "192.0.2.1", "cmd", "off",
         "--src", "0x7FFE", "--seq-step", "0"],
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert result.returncode == 2
    assert "seq-step" in result.stderr
