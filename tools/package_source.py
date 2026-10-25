"""Package only source, documentation, tests and assets, with a checksum manifest."""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'__pycache__', '.pytest_cache', '.ruff_cache', '.git', 'dist', 'build', 'vendor'}


def files() -> list[Path]:
    return sorted(p for p in ROOT.rglob('*') if p.is_file()
                  and not any(part in EXCLUDED or part.startswith('.venv') or part.endswith('.egg-info')
                              for part in p.relative_to(ROOT).parts)
                  and p.suffix not in ('.pyc', '.pyo', '.zip'))


def main() -> None:
    tree_path = ROOT / 'docs/PROJECT_TREE.md'
    tree_path.touch(exist_ok=True)
    entries = files()
    folders: dict = {}
    for item in entries:
        node = folders
        relative = item.relative_to(ROOT)
        for part in relative.parts[:-1]:
            node = node.setdefault(part, {})
        node[relative.name] = relative

    def render(node: dict, depth: int = 0) -> list[str]:
        result = []
        for name, value in sorted(node.items(), key=lambda pair: (not isinstance(pair[1], dict), pair[0])):
            indent = '  ' * depth
            if isinstance(value, dict):
                result.append(f'{indent}- **{name}/**')
                result += render(value, depth + 1)
            else:
                result.append(f'{indent}- [{name}](../{value.as_posix()})')
        return result
    tree_path.write_text('# شجرة المشروع\n\nLANBridgePro/\n\n' + '\n'.join(render(folders)) + '\n', encoding='utf-8')
    output = ROOT.parent / 'artifacts'
    output.mkdir(exist_ok=True)
    archive = output / 'LANBridgePro_v1.0.0_Source.zip'
    checksums = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files()}
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in files():
            bundle.write(path, 'LANBridgePro/' + path.relative_to(ROOT).as_posix())
        bundle.writestr('LANBridgePro/SHA256SUMS.json', json.dumps(checksums, indent=2))
    # Verify archive bytes and CRCs once, before handing it to the user.
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise RuntimeError('Source archive CRC verification failed.')
        for name, digest in checksums.items():
            if hashlib.sha256(bundle.read('LANBridgePro/' + name)).hexdigest() != digest:
                raise RuntimeError('Source archive checksum mismatch: ' + name)
    shutil.copy2(ROOT / 'docs/USER_GUIDE_AR.md', output / 'LANBridgePro_Arabic_Guide.md')
    shutil.copy2(ROOT / 'docs/previews/dashboard-ar-dark.png', output / 'LANBridgePro_Dashboard.png')
    print(json.dumps({'archive': str(archive), 'files': len(checksums) + 1,
                      'bytes': archive.stat().st_size, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
