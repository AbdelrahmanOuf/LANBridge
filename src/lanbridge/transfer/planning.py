from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from lanbridge.core.errors import Stopped, ValidationError
from lanbridge.core.models import CopyRoot, TaskSpec
from lanbridge.core.paths import contains, is_reparse, nearest_existing, safe_path
from lanbridge.core.store import Store


@dataclass(slots=True)
class Preflight:
    total_bytes: int = 0
    total_files: int = 0
    matched_bytes: int = 0
    free_bytes: int = 0
    extras: int = 0
    excluded: int = 0
    warnings: list[str] = field(default_factory=list)
    filesystem: str = "unknown"
    sources: list[dict] = field(default_factory=list)


def roots_for(spec: TaskSpec, data_dir: Path) -> list[CopyRoot]:
    if not spec.sources or len(spec.sources) > 1000:
        raise ValidationError("Choose 1–1000 sources.")
    destination = safe_path(spec.destination)
    if destination.exists() and not destination.is_dir():
        raise ValidationError("Destination must be a folder.")
    sources = [safe_path(p, must_exist=True) for p in spec.sources]
    results: list[CopyRoot] = []
    for i, source in enumerate(sources):
        if not (source.is_dir() or source.is_file()):
            raise ValidationError(f"Only regular files and directories can be copied: {source}")
        for other in sources[:i]:
            if contains(other, source) or contains(source, other):
                raise ValidationError("Do not select overlapping or duplicate sources.")
        target = destination / source.name if source.is_dir() and spec.folder_itself else destination
        if source.is_dir() and (contains(source, target) or contains(target, source)):
            raise ValidationError("Source and destination trees must not overlap.")
        if source.is_file() and (target / source.name).resolve() == source:
            raise ValidationError("A file cannot overwrite itself.")
        # Logs/database cannot be selected into the transfer in either direction.
        for tree in [source if source.is_dir() else source.parent, target]:
            if contains(tree, data_dir.resolve()) or contains(data_dir.resolve(), tree):
                raise ValidationError("Application data/log folders must remain outside the copy trees.")
        if source.is_dir() and not source.name:
            raise ValidationError("Select folders within the drive, not an entire drive root.")
        results.append(CopyRoot(str(source if source.is_dir() else source.parent), str(target),
                                source.name if source.is_file() else None))
    return results


def walk_root(root: CopyRoot, stop: threading.Event) -> Iterator[tuple[Path, Path, str]]:
    source = Path(root.source)
    target = Path(root.destination)
    if root.file_name is not None:
        yield source / root.file_name, target / root.file_name, "file"
        return
    stack = [(source, target)]
    while stack:
        if stop.is_set():
            raise Stopped("Scan stopped. Resume rebuilds the manifest safely.")
        src_dir, dst_dir = stack.pop()
        yield src_dir, dst_dir, "dir"
        with os.scandir(src_dir) as entries:
            for entry in entries:
                if stop.is_set():
                    raise Stopped("Scan stopped.")
                src, dst = Path(entry.path), dst_dir / entry.name
                if is_reparse(src):
                    yield src, dst, "excluded"
                elif entry.is_dir(follow_symlinks=False):
                    stack.append((src, dst))
                elif entry.is_file(follow_symlinks=False):
                    yield src, dst, "file"
                else:
                    yield src, dst, "excluded"


def metadata_match(source: Path, target: Path, size: int, mtime: int, tolerance_ns: int) -> bool:
    try:
        if is_reparse(source) or is_reparse(target):
            return False
        src, dst = source.stat(), target.stat()
        return (source.is_file() and target.is_file() and src.st_size == size == dst.st_size
                and abs(src.st_mtime_ns - mtime) <= tolerance_ns
                and abs(dst.st_mtime_ns - mtime) <= tolerance_ns)
    except OSError:
        return False


def inspect_filesystem(path: Path) -> tuple[str, int]:
    """GetVolumeInformation supports local volumes and UNC share roots."""
    if os.name != "nt":
        return "POSIX (test host)", 255
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    volume = ctypes.create_unicode_buffer(32768)
    get_path = kernel.GetVolumePathNameW
    get_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    if not get_path(str(path), volume, len(volume)):
        raise ctypes.WinError(ctypes.get_last_error())
    fs = ctypes.create_unicode_buffer(128)
    max_component = wintypes.DWORD()
    get_info = kernel.GetVolumeInformationW
    get_info.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                         ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                         ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
    if not get_info(volume.value, None, 0, None, ctypes.byref(max_component), None, fs, len(fs)):
        raise ctypes.WinError(ctypes.get_last_error())
    return fs.value, max_component.value


def scan(store: Store, spec: TaskSpec, data_dir: Path, stop: threading.Event,
         on_count: Callable[[int, int], None] | None = None) -> tuple[list[CopyRoot], Preflight]:
    roots = roots_for(spec, data_dir)
    result = Preflight()
    anchor = nearest_existing(Path(spec.destination))
    # Only a zero-byte temporary permission probe; never touch an existing user file.
    import tempfile
    with tempfile.TemporaryFile(dir=anchor):
        pass
    result.free_bytes = shutil.disk_usage(anchor).free
    result.filesystem, component_limit = inspect_filesystem(anchor)
    if result.filesystem.upper() in ("FAT", "FAT32", "EXFAT"):
        result.warnings.append("FAT/exFAT does not preserve NTFS ACLs, alternate streams or all metadata.")
        if not spec.settings.fat_times:
            raise ValidationError("FAT/exFAT destination: enable FAT timestamp tolerance (/FFT) first.")
    tolerance = 2_000_000_000 if spec.settings.fat_times else 100
    with store.connect() as db:
        db.execute("DELETE FROM entries WHERE task_id=?", (spec.id,))
    buffer: list[tuple] = []

    def flush() -> None:
        with store.connect() as db:
            for row in buffer:
                try:
                    db.execute("""INSERT INTO entries(task_id,target,source,kind,size,mtime,status,baseline)
                                  VALUES(?,?,?,?,?,?,?,?)""", row)
                except sqlite3.IntegrityError as exc:
                    existing = db.execute("SELECT kind,source FROM entries WHERE task_id=? AND target=?",
                                          (spec.id, row[1])).fetchone()
                    if row[3] != "dir" or not existing or existing["kind"] != "dir":
                        raise ValidationError(f"Two selections map to the same destination: {row[1]}") from exc
        buffer.clear()

    for root in roots:
        previous_files, previous_bytes = result.total_files, result.total_bytes
        for src, dst, kind in walk_root(root, stop):
            component_length = len(dst.name.encode('utf-16-le')) // 2 if os.name == 'nt' else len(os.fsencode(dst.name))
            if component_length > component_limit:
                raise ValidationError(f"Destination filename too long: {dst.name}")
            if dst.exists() and is_reparse(dst):
                raise ValidationError(f"Destination reparse point excluded: {dst}")
            if kind == "excluded":
                result.excluded += 1
                continue
            info = src.stat()
            size = info.st_size if kind == "file" else 0
            if kind == "file":
                with src.open("rb") as stream:
                    stream.read(1)  # Detect deny-read/exclusively locked files before copying.
                if result.filesystem.upper() in ("FAT", "FAT32") and size > 4_294_967_295:
                    raise ValidationError(f"File exceeds FAT32 maximum file size: {src}")
                result.total_bytes += size
                result.total_files += 1
            if dst.exists() and ((kind == "dir") != dst.is_dir()):
                raise ValidationError(f"File/folder type collision at destination: {dst}")
            matched = kind == "file" and metadata_match(src, dst, size, info.st_mtime_ns, tolerance)
            if matched:
                result.matched_bytes += size
            buffer.append((spec.id, str(dst), str(src), kind, size, info.st_mtime_ns,
                           "matched" if matched else "pending", int(matched)))
            if len(buffer) >= 512:
                flush()
                if on_count:
                    on_count(result.total_files, result.total_bytes)
        selected = Path(root.source) / root.file_name if root.file_name else Path(root.source)
        result.sources.append({'name': selected.name, 'path': str(selected),
                               'type': 'file' if root.file_name else 'folder',
                               'files': result.total_files - previous_files,
                               'bytes': result.total_bytes - previous_bytes, 'destination': root.destination})
    flush()
    # Conservative budget: a differing existing file may have to be written in full.
    required = result.total_bytes - result.matched_bytes
    margin = min(256 * 1024**2, max(1024**2, required // 100))
    if required + margin > result.free_bytes:
        raise ValidationError(f"Insufficient free space: require about {required + margin:,} bytes; "
                              f"available {result.free_bytes:,}.")
    scanned_destinations: set[str] = set()
    for root in roots:
        target = Path(root.destination)
        if not target.exists() or root.file_name:
            continue
        if str(target).casefold() in scanned_destinations:
            continue
        scanned_destinations.add(str(target).casefold())
        with store.connect() as db:
            for _, candidate, kind in walk_root(CopyRoot(str(target), str(target)), stop):
                if kind == "excluded":
                    # Any existing destination link could redirect Robocopy into another tree.
                    raise ValidationError(f"Remove destination reparse points before copying: {candidate}")
                found = db.execute("SELECT 1 FROM entries WHERE task_id=? AND target=?",
                                   (spec.id, str(candidate))).fetchone()
                if not found:
                    result.extras += 1
    if result.extras:
        result.warnings.append(f"{result.extras} extra destination entries will be retained.")
    if result.excluded:
        result.warnings.append(f"{result.excluded} source reparse/special entries are excluded.")
    result.warnings.append("Existing differing files may be overwritten by the source. No destination-only files are deleted.")
    return roots, result


def preflight_failed(store: Store, spec: TaskSpec, stop: threading.Event) -> None:
    """Recheck failed literal files without destroying the saved failure selection."""
    anchor = nearest_existing(safe_path(spec.destination))
    fs, _ = inspect_filesystem(anchor)
    if fs.upper() in ('FAT', 'FAT32', 'EXFAT') and not spec.settings.fat_times:
        raise ValidationError('Enable FAT time tolerance for this destination.')
    required = 0
    for batch in store.entries(spec.id, status='failed'):
        for row in batch:
            if stop.is_set():
                raise Stopped('Failed-file preflight stopped.')
            source = safe_path(row['source'], must_exist=True)
            safe_path(row['target'])
            if row['kind'] != 'file':
                continue
            info = source.stat()
            if info.st_size != row['size'] or info.st_mtime_ns != row['mtime']:
                raise ValidationError('A failed source file changed. Use Resume to rebuild the inventory.')
            with source.open('rb') as stream:
                stream.read(1)
            required += row['size']
            if fs.upper() in ('FAT', 'FAT32') and info.st_size > 4_294_967_295:
                raise ValidationError('A failed source exceeds FAT32 file size limits.')
    if required + 1024**2 > shutil.disk_usage(anchor).free:
        raise ValidationError('Insufficient free space for failed-file retry.')
    import tempfile
    with tempfile.TemporaryFile(dir=anchor):
        pass
