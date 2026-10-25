"""Build on Windows, then execute the actual frozen application self-test.

Dependencies must be installed first (build.ps1 does this). A failed native or
frozen test is a failed build, not a warning to be ignored.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def call(*args: str, timeout: int = 600) -> None:
    subprocess.run(args, cwd=ROOT, check=True, timeout=timeout, shell=False)


def main() -> int:
    if os.name != 'nt' or sys.version_info[:2] != (3, 12) or platform.architecture()[0] != '64bit':
        raise RuntimeError('Build requires Windows x64 and CPython 3.12 x64.')
    call(sys.executable, '-m', 'pip', 'check')
    call(sys.executable, '-m', 'ruff', 'check', 'src', 'tests', 'tools', 'build.py')
    call(sys.executable, '-m', 'pytest', '-q', '-m', 'not lan')
    call(sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm', 'packaging/LANBridgePro.spec')
    executable = ROOT / 'dist/LANBridgePro/LANBridgePro.exe'
    report = ROOT / 'dist/compiled-selftest.json'
    call(str(executable), '--self-test', '--self-test-report', str(report), timeout=240)
    result = json.loads(report.read_text(encoding='utf-8'))
    if result.get('ok') is not True:
        raise RuntimeError('The actual packaged executable did not pass its self-test.')
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    (ROOT / 'dist/LANBridgePro.exe.sha256').write_text(digest + '  LANBridgePro.exe\n', encoding='ascii')
    shutil.make_archive(str(ROOT / 'dist/LANBridgePro-Windows-x64'), 'zip', ROOT / 'dist', 'LANBridgePro')
    # Optional Inno Setup 6.3+ compilation. Portable transfers work without installation;
    # privileged frozen operations require protected Program Files installation.
    compiler = shutil.which('ISCC.exe')
    if not compiler:
        candidate = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Inno Setup 6/ISCC.exe'
        compiler = str(candidate) if candidate.exists() else None
    if compiler:
        call(compiler, str(ROOT / 'packaging/installer.iss'))
    record = {'ok': True, 'python': sys.version, 'platform': platform.platform(),
              'exe_sha256': digest, 'compiled_selftest': result, 'installer_built': bool(compiler),
              'two_pc_acceptance': 'NOT RUN by build.py; follow docs/WINDOWS_ACCEPTANCE.md'}
    (ROOT / 'dist/build-report.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print('Built and self-tested:', executable)
    if not compiler:
        print('Inno Setup 6.3+ not found. Install it and rerun to produce the protected-installation setup EXE.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
