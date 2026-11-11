from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = REPO_ROOT / "scripts" / "run-nightly-source-refresh.ps1"
REGISTER_SCRIPT = REPO_ROOT / "scripts" / "register-nightly-source-refresh-task.ps1"
STUB_CMD = REPO_ROOT / "scripts" / "test-nightly-refresh-stub.cmd"
POWERSHELL_EXE = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    os.name != "nt" or POWERSHELL_EXE is None,
    reason="nightly refresh integration scripts require Windows PowerShell and cmd.exe",
)


def _run_nightly_stub(
    tmp_path: Path,
    *extra_args: str,
    python_version: str = "",
) -> subprocess.CompletedProcess[str]:
    log_path = tmp_path / "nightly.log"
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
            python_version,
            "-Teams",
            "DTSV_China",
            "-Years",
            "2026",
            "-ManualRunYears",
            "2026",
            "-LogPath",
            str(log_path),
            *extra_args,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_nightly_source_refresh_includes_comments_by_default(tmp_path: Path) -> None:
    result = _run_nightly_stub(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "skip_comments=False" in result.stdout
    assert "--skip-comments" not in result.stdout


def test_nightly_source_refresh_defaults_to_project_venv_python() -> None:
    script_text = RUN_SCRIPT.read_text(encoding="utf-8")

    assert '[string]$PythonLauncher = ""' in script_text
    assert 'Join-Path $repoRoot ".venv\\Scripts\\python.exe"' in script_text


def test_nightly_source_refresh_omits_py_version_for_direct_interpreter(tmp_path: Path) -> None:
    result = _run_nightly_stub(tmp_path, python_version="-3.11")

    assert result.returncode == 0, result.stderr
    assert "-3.11 -m backend.analytics_cli refresh-all-sources" not in result.stdout
    assert "-m backend.analytics_cli refresh-all-sources" in result.stdout


def test_nightly_source_refresh_skips_duplicate_index_by_default(tmp_path: Path) -> None:
    result = _run_nightly_stub(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "prepare_duplicate_index=False" in result.stdout
    assert "--prepare-duplicate-index" not in result.stdout


def test_nightly_source_refresh_can_still_skip_comments_explicitly(tmp_path: Path) -> None:
    result = _run_nightly_stub(tmp_path, "-CommentMode", "skip")

    assert result.returncode == 0, result.stderr
    assert "skip_comments=True" in result.stdout
    assert "--skip-comments" in result.stdout


@pytest.mark.parametrize(
    ("output_lines", "expected_result"),
    [
        ([
            'File "concurrent/futures/_base.py", line 401, in __get_result',
            "requests.exceptions.HTTPError: 403 Client Error: Forbidden",
        ], "not_auth"),
        (["requests.exceptions.HTTPError: 403 Client Error: Forbidden for url: https://example.test/history_logs?id=4011234"], "not_auth"),
        (["requests.exceptions.HTTPError: 401 Client Error: Unauthorized"], "auth"),
        (["HTTP/1.1 401 Unauthorized"], "auth"),
        (["status_code=401"], "auth"),
        (["Failed to authenticate legacy Octane session"], "auth"),
    ],
)
def test_nightly_source_refresh_classifies_auth_failures_without_matching_arbitrary_numbers(
    output_lines: list[str],
    expected_result: str,
) -> None:
    command = r"""
$tokens = $null
$parseErrors = $null
$scriptAst = [System.Management.Automation.Language.Parser]::ParseFile(
  $env:VIZION_TEST_REFRESH_SCRIPT, [ref]$tokens, [ref]$parseErrors
)
$authFunction = $scriptAst.Find({
  param($node)
  $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-AuthFailure'
}, $true)
. ([scriptblock]::Create($authFunction.Extent.Text))
$outputLines = @(ConvertFrom-Json $env:VIZION_TEST_REFRESH_OUTPUT)
if (Test-AuthFailure -OutputLines $outputLines) { 'auth' } else { 'not_auth' }
"""
    result = subprocess.run(
        [POWERSHELL_EXE or "powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "VIZION_TEST_REFRESH_SCRIPT": str(RUN_SCRIPT),
            "VIZION_TEST_REFRESH_OUTPUT": json.dumps(output_lines),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected_result


def test_nightly_source_refresh_defaults_to_2025_through_current_testing_year(tmp_path: Path) -> None:
    log_path = tmp_path / "nightly-default-years.log"
    result = subprocess.run(
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
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    expected_years = ",".join(str(year) for year in range(2025, datetime.now().year + 1))
    assert f"years={expected_years}" in result.stdout
    assert f"manual_years={expected_years}" in result.stdout
    assert f"--years {expected_years}" in result.stdout
    assert f"--manual-years {expected_years}" in result.stdout


def test_register_nightly_task_defaults_to_2300() -> None:
    script_text = REGISTER_SCRIPT.read_text(encoding="utf-8")

    assert '[string]$StartTime = "23:00"' in script_text


def test_register_nightly_task_prepares_duplicate_index_by_default() -> None:
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
    assert "-PrepareDuplicateIndex 1" in result.stdout
    assert "-NoComments" not in result.stdout
