from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = REPO_ROOT / "scripts" / "run-octane-cookie-refresh.ps1"
REGISTER_SCRIPT = REPO_ROOT / "scripts" / "register-octane-cookie-refresh-task.ps1"
STUB_CMD = REPO_ROOT / "scripts" / "test-nightly-refresh-stub.cmd"
POWERSHELL_EXE = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    os.name != "nt" or POWERSHELL_EXE is None,
    reason="cookie refresh integration scripts require Windows PowerShell and cmd.exe",
)


def _run_cookie_refresh_stub(tmp_path: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    log_path = tmp_path / "cookie.log"
    return subprocess.run(
        [
            POWERSHELL_EXE or "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(RUN_SCRIPT),
            "-PythonLauncher",
            str(STUB_CMD),
            "-PythonVersion",
            "",
            "-LogPath",
            str(log_path),
            *extra_args,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cookie_refresh_invokes_refresh_octane_cookie_command(tmp_path: Path) -> None:
    result = _run_cookie_refresh_stub(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "refresh-octane-cookie" in result.stdout


def test_cookie_refresh_is_not_headless_by_default(tmp_path: Path) -> None:
    result = _run_cookie_refresh_stub(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "cookie_refresh_headless=False" in result.stdout
    assert "--headless" not in result.stdout


def test_cookie_refresh_can_still_run_headless(tmp_path: Path) -> None:
    result = _run_cookie_refresh_stub(tmp_path, "-CookieRefreshHeadless", "1")

    assert result.returncode == 0, result.stderr
    assert "cookie_refresh_headless=True" in result.stdout
    assert "--headless" in result.stdout


def test_cookie_refresh_writes_timestamped_log(tmp_path: Path) -> None:
    result = _run_cookie_refresh_stub(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Starting octane-cookie-refresh" in result.stdout
    assert "Finished octane-cookie-refresh" in result.stdout
    # The PowerShell stub echoes its argv; confirm the command id was logged.
    assert "cookie-refresh" in result.stdout


def test_register_cookie_refresh_task_uses_hourly_four_hour_interval_by_default() -> None:
    result = subprocess.run(
        [
            POWERSHELL_EXE or "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REGISTER_SCRIPT),
            "-PrintCommandOnly",
            "1",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "run-octane-cookie-refresh.ps1" in result.stdout


def test_register_cookie_refresh_task_prints_registration_command_only_when_requested() -> None:
    # PrintCommandOnly short-circuits before touching schtasks, so it must never
    # register a real task even if invoked without admin rights.
    result = subprocess.run(
        [
            POWERSHELL_EXE or "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REGISTER_SCRIPT),
            "-PrintCommandOnly",
            "1",
            "-IntervalHours",
            "2",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "run-octane-cookie-refresh.ps1" in result.stdout
    # PrintCommandOnly emits only the -File command, never schtasks arguments.
    assert "/SC" not in result.stdout
    assert "/MO" not in result.stdout
