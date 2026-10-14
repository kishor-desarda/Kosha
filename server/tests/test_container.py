# SPDX-License-Identifier: AGPL-3.0-or-later
"""The container image starts on hosting platforms: no VOLUME line, the platform's PORT is honoured."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"


def _port(**env: str) -> int:
    clean = {k: v for k, v in os.environ.items() if k not in ("KOSHA_PORT", "PORT")}
    out = subprocess.run([sys.executable, "-c", "from kosha import config; print(config.PORT)"], cwd=SERVER,
                         env={**clean, **env}, capture_output=True, text=True, check=True)
    return int(out.stdout.strip())


def test_port_defaults_to_8080():
    assert _port() == 8080


def test_platform_port_is_used():
    assert _port(PORT="4321") == 4321


def test_kosha_port_wins_over_platform_port():
    assert _port(PORT="4321", KOSHA_PORT="9000") == 9000


def test_dockerfile_has_no_volume_line():
    # Some hosting platforms refuse to build an image whose Dockerfile declares VOLUME.
    lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    assert not [ln for ln in lines if ln.strip().upper().startswith("VOLUME")]


def test_dockerfile_does_not_pin_the_port():
    # A fixed KOSHA_PORT in the image would hide the platform's PORT.
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "KOSHA_PORT=" not in text


def _entrypoint(tmp_path: Path, uid: str, owner: str) -> list[str]:
    """Run the entrypoint with stand-ins for id, stat, chown and setpriv that log their calls."""
    bin_dir, log = tmp_path / "bin", tmp_path / "calls.log"
    bin_dir.mkdir()
    stubs = {"id": f'if [ "$1" = "-u" ]; then echo {uid}; else echo 999; fi',
             "stat": f"echo {owner}",
             "chown": f'echo "chown $*" >> "{log}"',
             "setpriv": f'echo "setpriv $*" >> "{log}"'}
    for name, body in stubs.items():
        (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    data = tmp_path / "data"
    out = subprocess.run(["sh", str(ROOT / "deploy" / "docker" / "entrypoint.sh"), "echo", "started"],
                         env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                              "KOSHA_DATA_DIR": str(data)},
                         capture_output=True, text=True, check=True)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return calls + ([out.stdout.strip()] if out.stdout.strip() else [])


def test_entrypoint_runs_the_command_when_not_root(tmp_path):
    assert _entrypoint(tmp_path, uid="999", owner="root") == ["started"]


def test_entrypoint_hands_a_root_owned_volume_to_kosha_and_drops_root(tmp_path):
    calls = _entrypoint(tmp_path, uid="0", owner="root")
    assert calls == [f"chown -R kosha {tmp_path / 'data'}",
                     "setpriv --reuid=kosha --regid=999 --init-groups echo started"]
    assert (tmp_path / "data").is_dir()


def test_entrypoint_leaves_a_volume_kosha_already_owns(tmp_path):
    assert _entrypoint(tmp_path, uid="0", owner="kosha") == ["setpriv --reuid=kosha --regid=999 --init-groups echo started"]
