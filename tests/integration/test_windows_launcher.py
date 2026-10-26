"""Exercise the real CMD -> Windows PowerShell dispatch without installing packages or opening a GUI."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.windows, pytest.mark.skipif(os.name != 'nt', reason='Real Windows host required')]


@pytest.mark.parametrize('exit_code', [0, 17])
def test_double_click_launcher_dispatch_and_error_code(tmp_path: Path, exit_code: int) -> None:
    project = tmp_path / 'LANBridge Arabic \u0639\u0631\u0628\u064a & spaces!'
    project.mkdir()
    launcher = Path(__file__).resolve().parents[2] / 'run.cmd'
    shutil.copyfile(launcher, project / 'run.cmd')
    # This stub replaces only the application setup. CMD and Windows PowerShell are real processes.
    (project / 'run.ps1').write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'cwd.txt'), (Get-Location).Path)\n"
        f'exit {exit_code}\n',
        encoding='ascii',
    )
    system_root = Path(os.environ['SystemRoot'])
    command = f'"{system_root / "System32" / "cmd.exe"}" /d /s /c ""{project / "run.cmd"}""'
    result = subprocess.run(
        command,
        cwd=tmp_path,
        input=b'\r\n',  # Release the error-only PAUSE without a keyboard or an interactive desktop.
        capture_output=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == exit_code, (result.stdout, result.stderr)
    assert Path((project / 'cwd.txt').read_text(encoding='utf-8')) == project
    assert (b'Press any key to close this window.' in result.stdout) == (exit_code != 0)
