# Contributing

Keep changes scoped, reproducible and honest about testing. This is a development preview; unit tests do
not certify two-PC behavior. Use CPython 3.12 x64, install requirements-lock.txt, then install the package
with `python -m pip install --no-build-isolation --no-deps -e .` in a virtual environment.

Before a pull request:

```text
python -m ruff check src tests tools build.py
python -m pytest -q -m "not lan"
```

On Windows also use tools/check_powershell.ps1 and build.py. Record the actual OS, commands/results,
skipped tests and acceptance cases. Never claim UAC, domain policy or live LAN success from Mocks.

- Keep long operations outside the GUI thread and system services independent of the UI.
- Never put secrets in argv, logs, SQLite, issue reports or discovery packets.
- Preserve local consent, signing/guest policy, source retention and accurate progress labels.
- Do not default to destructive Robocopy flags or Everyone Full Control.
- Explain overwrite and rollback limits for any system-changing behavior.
- Add meaningful behavior tests; simple documentation edits do not need new tests.
- Preserve Arabic/English labels and RTL/LTR path ordering.

Use sanitized disposable examples in issues. Follow docs/WINDOWS_ACCEPTANCE.md for a dedicated two-PC lab;
do not disrupt production network/policy settings. Report vulnerabilities according to SECURITY.md.
The maintainer has not selected a project redistribution license; this guide adds no license or contributor agreement.
