from __future__ import annotations

import ntpath
import os
import re
import stat
from pathlib import Path

from .errors import ValidationError


def app_data() -> Path:
    if os.name == "nt":
        root = Path(os.environ["LOCALAPPDATA"])
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    result = root / "LANBridgePro"
    result.mkdir(parents=True, exist_ok=True)
    return result


def is_reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def safe_path(value: str, *, must_exist: bool = False) -> Path:
    if not value or any(c in value for c in "\x00\r\n\"*?"):
        raise ValidationError("Choose a literal file/folder path without wildcard or control characters.")
    if os.name == "nt":
        if value.startswith(("\\\\.\\", "\\\\?\\GLOBALROOT")) or ":" in value[2:]:
            raise ValidationError("Device paths and alternate data streams are not allowed.")
        if not ntpath.isabs(value) or value.startswith("/"):
            raise ValidationError("An absolute Windows path is required.")
        for part in re.split(r"[\\/]", value):
            if part in (".", "..") or part.endswith((" ", ".")):
                raise ValidationError("Ambiguous path components are not allowed.")
            if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I):
                raise ValidationError("Reserved Windows file name.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValidationError("An absolute path is required.")
    # Check every existing ancestor before resolving: resolution must not hide junctions.
    for ancestor in [path, *path.parents]:
        try:
            if is_reparse(ancestor):
                raise ValidationError(f"Reparse/symlink paths are excluded: {ancestor}")
        except FileNotFoundError:
            continue
    path = path.resolve(strict=False)
    if must_exist and not path.exists():
        raise ValidationError(f"Path does not exist: {path}")
    return path


def contains(parent: Path, child: Path) -> bool:
    try:
        return os.path.commonpath((str(parent), str(child))) == str(parent)
    except ValueError:
        return False


def nearest_existing(path: Path) -> Path:
    while not path.exists():
        if path.parent == path:
            raise ValidationError("Destination drive/share is unavailable.")
        path = path.parent
    if not path.is_dir():
        raise ValidationError("A destination parent is a file.")
    return path


def validate_unc(value: str) -> tuple[str, str]:
    if not re.fullmatch(r"\\\\[A-Za-z0-9][A-Za-z0-9.-]{0,252}\\[^\\/:*?\"<>|\x00-\x1f]+(?:\\[^:*?\"<>|\x00-\x1f]+)*\\?", value):
        raise ValidationError(r"Use a UNC path such as \\PC-B\LANBridge_1234.")
    parts = value.rstrip("\\").split("\\")
    if any(p in (".", "..") for p in parts[2:]):
        raise ValidationError("UNC traversal is not allowed.")
    return parts[2], parts[3]
