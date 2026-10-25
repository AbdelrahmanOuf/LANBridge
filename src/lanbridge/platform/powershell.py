from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from lanbridge.core.errors import PlatformError
from lanbridge.platform.windows_paths import system_directory


class PowerShell:
    """Fixed, shipped dispatcher. User data is JSON stdin, never PowerShell source."""

    def __init__(self, timeout: float = 90):
        self.timeout = timeout

    def call(self, action: str, payload: dict | None = None) -> Any:
        if os.name != "nt":
            raise PlatformError("Windows network/share management is available on Windows only.")
        binary = system_directory() / "WindowsPowerShell/v1.0/powershell.exe"
        script = (Path(__file__).parents[1] / "resources/windows.ps1").read_text(encoding="utf-8")
        # A fixed loader avoids Windows' 32K command-line limit. Only the bundled
        # dispatcher is code; the following line is JSON data read by that dispatcher.
        loader = "& ([ScriptBlock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadLine()))))"
        encoded = base64.b64encode(loader.encode("utf-16-le")).decode("ascii")
        try:
            process = subprocess.run([str(binary), "-NoLogo", "-NoProfile", "-NonInteractive",
                                      "-EncodedCommand", encoded],
                                     input=base64.b64encode(script.encode('utf-8')).decode('ascii') + '\n' +
                                     json.dumps({"action": action, "payload": payload or {}}, ensure_ascii=True),
                                     capture_output=True, text=True, encoding="utf-8", errors="replace",
                                     shell=False, timeout=self.timeout, creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise PlatformError("Windows operation timed out. Inspect the saved operation before retrying.") from exc
        try:
            result = json.loads(process.stdout.lstrip("\ufeff").strip())
        except json.JSONDecodeError as exc:
            # Do not expose encoded commands / raw stderr in the GUI or logs.
            raise PlatformError("PowerShell did not return valid JSON. Check Windows policy/event logs.") from exc
        if process.returncode or not result.get("ok"):
            raise PlatformError(str(result.get("error", "Windows operation failed.")))
        return result.get("data")
