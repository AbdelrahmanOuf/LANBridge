"""Resolve privileged Windows locations through native APIs, never caller-supplied environment values."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

from lanbridge.core.errors import PlatformError


def _directory(function_name: str) -> Path:
    if os.name != 'nt':
        raise PlatformError('Native Windows directories require Windows.')
    from ctypes import wintypes
    function = getattr(ctypes.WinDLL('kernel32', use_last_error=True), function_name)
    function.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    function.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = function(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise PlatformError('Windows could not resolve a trusted system directory.')
    return Path(buffer.value)


def system_directory() -> Path:
    return _directory('GetSystemDirectoryW')


def windows_directory() -> Path:
    return _directory('GetWindowsDirectoryW')


def program_files() -> Path:
    if os.name != 'nt':
        raise PlatformError('Windows Program Files requires Windows.')
    from ctypes import wintypes
    function = ctypes.WinDLL('shell32', use_last_error=True).SHGetFolderPathW
    function.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR]
    function.restype = ctypes.c_long
    buffer = ctypes.create_unicode_buffer(32768)
    if function(None, 0x26, None, 0, buffer) != 0:
        raise PlatformError('Windows could not resolve the protected Program Files folder.')
    return Path(buffer.value)
