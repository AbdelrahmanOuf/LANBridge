# Optional: prepare dependencies on an internet-connected Windows x64 build machine.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
py -3.12 -m pip download --only-binary=:all: -r requirements-lock.txt -d vendor\wheels
if ($LASTEXITCODE -ne 0) { throw 'Wheel download failed; inspect compatibility and the package index.' }
Write-Host 'For offline installation: python -m pip install --no-index --find-links vendor\wheels -r requirements-lock.txt'
