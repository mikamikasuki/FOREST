#!/usr/bin/env python3
"""Build a source-only archive from explicit public source locations.

Runtime databases, project data, outputs, credentials, environment files and
local machine configuration are never eligible for this archive.
"""
from pathlib import Path
import argparse
import os
import re
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
DIRECTORIES={'apps','docs','examples','packages','research','runners','scripts','services','tests','forest_cli','.github'}
ROOT_FILES={'README.md','LICENSE','CONTRIBUTING.md','SECURITY.md','CHANGELOG.md','pyproject.toml','requirements.lock.txt','Dockerfile','compose.yaml','.gitignore','.dockerignore','.env.example'}
EXCLUDED={'.git','.venv','node_modules','dist','build','var','output','tmp','temp','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache','.cache','test-results','playwright-report','.forest-processes','.forest','.idea','.vscode','htmlcov','coverage'}
PRIVATE_DOCUMENTS={'docs/ACCEPTANCE.md','docs/QUALIFICATION_REPORT.md','docs/RESEARCH_FINDINGS.md','docs/WRITING_REVIEW.md','docs/FEATURES.md','docs/RUNTIME_UPGRADE.md','docs/PIPELINE_LIVE_VALIDATION.md'}
PRIVATE_DIRECTORIES={'docs/research_tasks'}
SUFFIXES={'.py','.md','.txt','.json','.csv','.ts','.tsx','.css','.html','.svg','.sh','.cjs','.yaml','.yml','.toml','.tex','.bib'}
SECRET_NAME=re.compile(r'(^\.env($|\.)|^(credentials?|secrets?|api[-_]?keys?|tokens?)(\.(json|ya?ml|toml|txt))?$|^owner[-_]token(\.txt)?$|^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$|\.(pem|key|p12|pfx|keystore)$)',re.I)
SECRET_VALUE=re.compile(rb'-----BEGIN (?:RSA |DSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----|\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}|\bglpat-[A-Za-z0-9_-]{20,}|\bxox[baprs]-[A-Za-z0-9-]{20,}|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')


def excluded_path(relative):
    """Keep local state out of the archive, including nested copies."""
    path=relative.as_posix()
    return (
        path in PRIVATE_DOCUMENTS
        or any(path == directory or path.startswith(directory + '/') for directory in PRIVATE_DIRECTORIES)
        or any(part in EXCLUDED or part.endswith('.egg-info') for part in relative.parts)
        or any(part.startswith('._') for part in relative.parts)
        or (path != '.env.example' and any(SECRET_NAME.search(part) for part in relative.parts))
    )


def source_files(root=ROOT):
    root=Path(root)
    candidates=[]
    for name in ROOT_FILES:
        path=root/name
        if path.is_file() and not path.is_symlink(): candidates.append(path)
    for name in sorted(DIRECTORIES):
        directory=root/name
        if not directory.is_dir() or directory.is_symlink(): continue
        for current,directories,filenames in os.walk(directory,followlinks=False):
            current=Path(current)
            directories[:]=sorted(name for name in directories if not (current/name).is_symlink() and not excluded_path((current/name).relative_to(root)))
            for name in filenames:
                path=current/name
                if path.suffix in SUFFIXES and path.is_file() and not path.is_symlink() and not excluded_path(path.relative_to(root)):
                    candidates.append(path)
    for path in sorted(candidates):
        relative=path.relative_to(root)
        if SECRET_VALUE.search(path.read_bytes()): raise ValueError('Possible secret in eligible source file: '+str(relative))
        yield path


def package(destination,root=ROOT):
    files=list(source_files(root)); destination=Path(destination); destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in files: archive.write(path,'forest/'+path.relative_to(root).as_posix())
    return len(files)


def source_tree(destination,root=ROOT):
    """Copy the same public files to a new or empty upload directory."""
    root=Path(root).resolve()
    destination=Path(destination).absolute()
    if destination.is_symlink(): raise ValueError('Source destination cannot be a symbolic link')
    destination=destination.resolve()
    if destination==root: raise ValueError('Source destination cannot be the working repository')
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('Source destination must be a new or empty directory')
    # Finish eligibility and credential checks before creating any public files.
    files=list(source_files(root))
    destination.mkdir(parents=True,exist_ok=True)
    for path in files:
        target=destination/path.relative_to(root)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,target)
    return len(files)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    targets=parser.add_mutually_exclusive_group()
    targets.add_argument('--output',type=Path,help='Write a source ZIP (default: output/forest-source.zip)')
    targets.add_argument('--directory',type=Path,help='Copy public source into a new or empty directory for GitHub')
    args=parser.parse_args()
    if args.directory:
        print(f'Copied {source_tree(args.directory)} public source files to {args.directory}')
    else:
        destination=args.output or ROOT/'output/forest-source.zip'
        print(f'Packaged {package(destination)} public source files to {destination}')
