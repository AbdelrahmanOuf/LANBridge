from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from lanbridge.core.errors import PlatformError, ValidationError
from lanbridge.core.models import CopyRoot, TransferSettings
from lanbridge.core.paths import contains, safe_path
from lanbridge.platform.windows_paths import system_directory


def executable() -> str:
    if os.name != "nt":
        raise PlatformError("Real transfers require Windows 10/11 and Robocopy. This host is for tests only.")
    # Never resolve privileged system tools from the current working directory / user PATH.
    path = system_directory() / "robocopy.exe"
    if not path.is_file():
        raise PlatformError("Robocopy was not found in Windows System32.")
    return str(path)


def command(root: CopyRoot, settings: TransferSettings, log: Path, *, dry: bool = False,
            binary: str | None = None, selected: str | None = None) -> list[str]:
    src, dst = safe_path(root.source, must_exist=True), safe_path(root.destination)
    if any(contains(tree, log.resolve()) for tree in (src, dst)):
        raise ValidationError("The log must be outside source and destination.")
    filename = selected or root.file_name
    if filename and (Path(filename).name != filename or any(c in filename for c in '/\\*?\"\r\n\x00')):
        raise ValidationError("Literal file name required.")
    args = [binary or executable(), str(src), str(dst)]
    if filename:
        args.append(filename)
    else:
        args.append("/E")
    args += ["/Z", f"/R:{settings.retries}", f"/W:{settings.wait_seconds}",
             f"/MT:{settings.threads}", "/XJ", "/XJD", "/XJF", "/COPY:DAT", "/DCOPY:DAT",
             "/BYTES", "/FP", "/NP", f"/UNILOG:{log}"]
    if settings.fat_times:
        args.append("/FFT")
    if settings.rate_mib:
        args.append(f"/IORATE:{settings.rate_mib}M")
    if dry:
        args.append("/L")
    return args


def explain_exit(code: int) -> str:
    if code < 0:
        return "Robocopy was interrupted or terminated. Recheck and resume."
    bits = []
    if code & 1:
        bits.append("files copied")
    if code & 2:
        bits.append("extra destination entries retained")
    if code & 4:
        bits.append("mismatches reported")
    if code & 8:
        bits.append("copy failures")
    if code & 16:
        bits.append("fatal error")
    return "; ".join(bits) if bits else "No copy required."


def helper_command(*args: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "lanbridge", *args]


class ProcessRunner:
    """Own Robocopy's console and lifetime; safe stop means BREAK + wait for exit.

    No in-memory suspend/resume claim. App crash closes the job handle, terminating
    the child; /Z and a fresh comparison determine what can actually be resumed.
    """

    def __init__(self) -> None:
        self._process = None
        self._lock = threading.Lock()
        self._force = threading.Event()

    def run(self, args: list[str], stop: threading.Event, tick: Callable[[], None],
            started: Callable[[int], None]) -> int:
        if os.name != "nt":
            raise PlatformError("Robocopy execution requires Windows.")
        import win32api
        import win32con
        import win32job
        import win32event
        import win32process

        job = win32job.CreateJobObject(None, None)
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        self._force.clear()
        startup = win32process.STARTUPINFO()
        startup.dwFlags = win32con.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        handle = None
        thread_handle = None
        try:
            # Suspended creation closes the launch/Job assignment race: no file
            # I/O begins until supervision is established. NEW_CONSOLE isolates BREAK.
            handle, thread_handle, pid, _ = win32process.CreateProcess(
                args[0], subprocess.list2cmdline(args), None, None, False,
                win32con.CREATE_SUSPENDED | win32con.CREATE_NEW_CONSOLE, None, None, startup)
            try:
                win32job.AssignProcessToJobObject(job, handle)
            except Exception:
                win32api.TerminateProcess(handle, 16)
                raise PlatformError("Windows could not supervise Robocopy in a Job Object.") from None
            win32process.ResumeThread(thread_handle)
            thread_handle.Close()
            thread_handle = None
            with self._lock:
                self._process = handle
            started(pid)
            sent = False
            while win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_TIMEOUT:
                if self._force.is_set():
                    win32api.TerminateProcess(handle, 16)
                elif stop.is_set() and not sent:
                    result = subprocess.run(helper_command("--signal-stop", str(pid)),
                                            creationflags=subprocess.CREATE_NO_WINDOW,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            timeout=10, check=False)
                    sent = result.returncode == 0
                    if not sent:
                        raise PlatformError("Safe stop signal failed; the supervised process will be stopped. Resume rechecks files.")
                tick()
                win32event.WaitForSingleObject(handle, 1000)
            return win32process.GetExitCodeProcess(handle)
        finally:
            # Also stops an unsignalled process on runner errors.
            job.Close()
            if handle is not None:
                win32event.WaitForSingleObject(handle, 10000)
                handle.Close()
            if thread_handle:
                thread_handle.Close()
            with self._lock:
                self._process = None

    def force_stop(self) -> None:
        self._force.set()


def signal_stop(pid: int) -> int:
    if os.name != "nt" or pid < 1:
        return 2
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    received = threading.Event()

    def handled(code: int) -> bool:
        received.set()
        return True

    handler = handler_type(handled)
    kernel.FreeConsole()
    if not kernel.AttachConsole(pid):
        return 3
    try:
        kernel.SetConsoleCtrlHandler(handler, True)
        # Group 0 is confined to the newly attached Robocopy console.
        if not kernel.GenerateConsoleCtrlEvent(1, 0):
            return 4
        received.wait(timeout=0.3)
        return 0
    finally:
        kernel.SetConsoleCtrlHandler(handler, False)
        kernel.FreeConsole()


def supports_rate_limit() -> bool:
    if os.name != "nt":
        return False
    output = subprocess.run([executable(), "/?"], capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=False).stdout
    return b"IORATE" in output.upper() or "IORATE" in output.decode("utf-16-le", errors="ignore").upper()
