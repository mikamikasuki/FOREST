#!/usr/bin/env python3
"""Create an editable source ZIP and validate portable archive paths."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

if __package__:
    from .package_source import source_files
else:
    from package_source import source_files

ROOT = Path(__file__).resolve().parents[1]
RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$', re.I)


def portable_path(name):
    if '\\' in name or name.startswith('/') or re.match(r'^[A-Za-z]:', name):
        raise ValueError('Archive paths must be relative POSIX paths: ' + name)
    if len(name.encode('utf-8')) > 220:
        raise ValueError('Archive path exceeds portable 220-byte budget: ' + name)
    for part in PurePosixPath(name).parts:
        if part in ('.', '..') or part.endswith((' ', '.')) or RESERVED.match(part) or any(c in part for c in '<>:"|?*') or any(ord(c) < 32 for c in part):
            raise ValueError('Windows-incompatible archive path: ' + name)


def inspect_archive(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        folded = set()
        for name in names:
            portable_path(name.rstrip('/'))
            if name.casefold() in folded:
                raise ValueError('Case-insensitive duplicate archive path: ' + name)
            folded.add(name.casefold())
        bad = archive.testzip()
        if bad:
            raise ValueError('ZIP integrity check failed: ' + bad)
        return {'archive': str(Path(path).resolve()), 'files': len(names), 'portable_paths': True, 'zip_crc_check': 'passed', 'native_windows_runtime': 'use WSL2 or Docker Desktop'}


def create_source(destination, root=ROOT):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Release destination already exists')
    root = Path(root).resolve()
    files = list(source_files(root))
    names = set()
    for path in files:
        name = 'forest/' + path.relative_to(root).as_posix()
        portable_path(name)
        if name.casefold() in names:
            raise ValueError('Case-insensitive duplicate archive path: ' + name)
        names.add(name.casefold())
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path, 'forest/' + path.relative_to(root).as_posix())
    return inspect_archive(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(inspect_archive(args.archive) if args.check_only else create_source(args.archive), indent=2))


if __name__ == '__main__':
    main()
