"""Exercise both real source-packaging CLIs against isolated temporary trees."""
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from scripts.release import create_source
from scripts.package_source import source_tree as export_source_tree


@pytest.fixture
def source_tree(tmp_path):
    root = tmp_path / 'source'
    (root / 'scripts').mkdir(parents=True)
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    for name in ('package_source.py', 'release.py'):
        shutil.copyfile(scripts / name, root / 'scripts' / name)
    (root / 'README.md').write_text('Public source\n')
    (root / 'docs').mkdir()
    return root


def run_packager(entrypoint, root, destination):
    arguments = ['--output', str(destination)] if entrypoint == 'package_source.py' else [str(destination)]
    return subprocess.run(
        [sys.executable, str(root / 'scripts' / entrypoint), *arguments],
        cwd=root.parent, capture_output=True, text=True,
    )


@pytest.mark.parametrize('entrypoint', ['package_source.py', 'release.py'])
def test_packaging_clis_reject_detected_secret_before_writing_archive(source_tree, tmp_path, entrypoint):
    (source_tree / 'docs' / 'example.md').write_text('Token: ' + 'sk-' + 'x' * 32)
    destination = tmp_path / 'source.zip'
    result = run_packager(entrypoint, source_tree, destination)
    assert result.returncode != 0
    assert 'Possible secret in eligible source file: docs/example.md' in result.stderr
    assert not destination.exists()


@pytest.mark.parametrize('entrypoint', ['package_source.py', 'release.py'])
def test_packaging_clis_share_public_allowlist_and_exclude_private_files(source_tree, tmp_path, entrypoint):
    private_files = [
        '.env', '.env.local', 'secrets.json', 'docs/.env', 'docs/.env.production',
        'docs/credentials.yaml', 'docs/owner-token.txt', 'docs/private.key',
        'docs/secrets/notes.md', 'docs/output/result.json', 'var/projects/run.py',
        'docs/arbitrary.bin',
        'docs/api-key.txt', 'docs/tokens.json', 'docs/id_ecdsa',
        'docs/tmp/local-notes.md', 'docs/.vscode/settings.json',
        'docs/.cache/state.json', 'docs/forest.egg-info/PKG-INFO.txt',
        'docs/._guide.md', 'tmp/notes.md',
        'docs/ACCEPTANCE.md', 'docs/QUALIFICATION_REPORT.md',
        'docs/RESEARCH_FINDINGS.md', 'docs/WRITING_REVIEW.md',
        'docs/FEATURES.md', 'docs/RUNTIME_UPGRADE.md',
    ]
    for name in private_files:
        path = source_tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Private value: ' + 'sk-' + 'x' * 32)
    (source_tree / '.env.example').write_text('EXAMPLE_SETTING=\n')
    (source_tree / 'docs' / 'public.md').write_text('Public guide\n')
    destination = tmp_path / 'source.zip'
    result = run_packager(entrypoint, source_tree, destination)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist()) == {
            'forest/README.md', 'forest/.env.example', 'forest/docs/public.md',
            'forest/scripts/package_source.py', 'forest/scripts/release.py',
        }
        assert archive.read('forest/docs/public.md') == b'Public guide\n'
        assert archive.testzip() is None


@pytest.mark.parametrize('token', [
    'github_pat_' + 'x' * 50,
    'glpat-' + 'x' * 25,
    'xoxb-' + '1' * 30,
    'ASIA' + 'A' * 16,
    '-----BEGIN ' + 'ENCRYPTED PRIVATE KEY-----',
])
def test_packaging_rejects_additional_credential_formats_without_exposing_values(source_tree, tmp_path, token):
    (source_tree / 'docs' / 'example.md').write_text('Accidental credential: ' + token)
    result=run_packager('package_source.py',source_tree,tmp_path/'source.zip')
    assert result.returncode != 0
    assert 'Possible secret in eligible source file: docs/example.md' in result.stderr
    assert token not in result.stderr
    assert token not in result.stdout


def test_packaging_does_not_follow_source_or_directory_symlinks(source_tree,tmp_path):
    private=tmp_path/'private'
    private.mkdir()
    (private/'notes.md').write_text('Secret: '+'sk-'+'x'*32)
    (source_tree/'docs'/'linked.md').symlink_to(private/'notes.md')
    (source_tree/'docs'/'linked-directory').symlink_to(private,target_is_directory=True)
    (source_tree/'examples').symlink_to(private,target_is_directory=True)
    destination=tmp_path/'source.zip'
    result=run_packager('package_source.py',source_tree,destination)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(destination) as archive:
        assert not any('linked' in name or name.startswith('forest/examples/') for name in archive.namelist())


def test_directory_export_matches_source_zip_and_preserves_source_modes(source_tree,tmp_path):
    script=source_tree/'scripts'/'run.sh'
    script.write_text('#!/bin/sh\nexit 0\n')
    script.chmod(0o755)
    (source_tree/'docs'/'QUALIFICATION_REPORT.md').write_text('Private development history')
    (source_tree/'docs'/'PUBLIC.md').write_text('Public operating guide')
    destination=tmp_path/'github'
    result=subprocess.run([sys.executable,str(source_tree/'scripts'/'package_source.py'),'--directory',str(destination)],capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    zipped=tmp_path/'source.zip'
    create_source(zipped,source_tree)
    with zipfile.ZipFile(zipped) as archive:
        assert {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()} == {name.removeprefix('forest/') for name in archive.namelist()}
        for name in archive.namelist():
            assert (destination/name.removeprefix('forest/')).read_bytes()==archive.read(name)
    assert (destination/'scripts'/'run.sh').stat().st_mode & 0o111
    assert not (destination/'.git').exists()


def test_directory_export_rejects_existing_files_or_secrets_without_partial_output(source_tree,tmp_path):
    destination=tmp_path/'github'
    destination.mkdir()
    existing=destination/'README.md'
    existing.write_text('Existing user content')
    with pytest.raises(ValueError,match='new or empty'):
        export_source_tree(destination,source_tree)
    assert existing.read_text()=='Existing user content'
    existing.unlink()
    (source_tree/'docs'/'oops.md').write_text('sk-'+'x'*32)
    with pytest.raises(ValueError,match='Possible secret'):
        export_source_tree(destination,source_tree)
    assert list(destination.iterdir())==[]


def test_release_preserves_existing_destination(source_tree, tmp_path):
    destination = tmp_path / 'source.zip'
    destination.write_bytes(b'existing delivery')
    with pytest.raises(ValueError, match='already exists'):
        create_source(destination, source_tree)
    assert destination.read_bytes() == b'existing delivery'


def test_release_rejects_nonportable_name_before_writing_archive(source_tree, tmp_path):
    (source_tree / 'docs' / 'CON.md').write_text('Incompatible Windows filename\n')
    destination = tmp_path / 'source.zip'
    with pytest.raises(ValueError, match='Windows-incompatible'):
        create_source(destination, source_tree)
    assert not destination.exists()
