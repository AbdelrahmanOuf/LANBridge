$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if ($env:OS -ne 'Windows_NT') { throw 'Build the Windows executable on Windows x64.' }
py -3.12 -m venv .venv-build
if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 x64 with Python Launcher.' }
& .\.venv-build\Scripts\python.exe -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& .\.venv-build\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw 'Project installation failed.' }
& .\.venv-build\Scripts\python.exe build.py
if ($LASTEXITCODE -ne 0) { throw 'Build or compiled EXE self-test failed. Inspect the build report.' }
