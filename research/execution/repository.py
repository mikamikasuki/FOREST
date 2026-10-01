"""Prepare pinned GitHub sources without sharing host authentication with tasks."""
from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

_GIT_TIMEOUT_SECONDS = 300


class RepositoryError(ValueError):
    def __init__(self, message: str, code: str = "REPOSITORY_ERROR"):
        super().__init__(message)
        self.code = code


def task_environment(overrides: dict | None = None) -> dict:
    """Prevent host Git authentication references from reaching task processes."""
    def admitted(key: str) -> bool:
        upper = key.upper()
        return not (upper.startswith("GIT_") or upper.startswith("GCM_") or
                    upper in {"FOREST_GIT_CREDENTIALS_FILE", "FOREST_GIT_TOKEN_FILE",
                              "SSH_AUTH_SOCK", "SSH_AGENT_PID", "SSH_ASKPASS",
                              "SSH_ASKPASS_REQUIRE", "SUDO_ASKPASS"})
    env = {key: value for key, value in os.environ.items() if admitted(key)}
    for key, value in (overrides or {}).items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise RepositoryError("Task environment entries must be strings.", "INVALID_TASK_ENV")
        if not admitted(key):
            raise RepositoryError("Task environment cannot override host Git or SSH authentication.", "INVALID_TASK_ENV")
        env[key] = value
    return env


def normalize_repository(spec: dict) -> dict:
    """Validate a public repository specification; never read host credentials."""
    if not isinstance(spec, dict) or set(spec) - {"url", "ref", "directory", "transport", "credential"}:
        raise RepositoryError("Repository accepts only url, ref, directory, transport, and credential.", "INVALID_REPOSITORY")
    url = spec.get("url")
    if not isinstance(url, str) or len(url) > 512 or any(char.isspace() for char in url):
        raise RepositoryError("Use a GitHub HTTPS or SSH repository URL.", "INVALID_REPOSITORY_URL")
    if url.startswith("git@github.com:"):
        source_transport = "ssh"
        path = url[len("git@github.com:"):]
    else:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise RepositoryError("Use a GitHub HTTPS or SSH repository URL.", "INVALID_REPOSITORY_URL") from None
        if (parsed.hostname != "github.com" or port is not None or parsed.query or parsed.fragment or
                parsed.password is not None or
                (parsed.scheme == "https" and parsed.username is not None) or
                (parsed.scheme == "ssh" and parsed.username != "git") or
                parsed.scheme not in ("https", "ssh")):
            raise RepositoryError("Use an uncredentialed github.com HTTPS or git SSH URL.", "INVALID_REPOSITORY_URL")
        source_transport = "ssh" if parsed.scheme == "ssh" else "https"
        path = parsed.path.removeprefix("/")
    path = path.removesuffix(".git")
    pieces = path.split("/")
    if (len(pieces) != 2 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", pieces[0]) or
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", pieces[1]) or pieces[1] in (".", "..")):
        raise RepositoryError("Repository URL must identify one GitHub owner and repository.", "INVALID_REPOSITORY_URL")
    transport = spec.get("transport", "auto")
    if transport not in ("auto", "https", "ssh"):
        raise RepositoryError("Repository transport must be auto, https, or ssh.", "INVALID_REPOSITORY")
    transport = source_transport if transport == "auto" else transport
    canonical = ("https://github.com/" if transport == "https" else "git@github.com:") + "/".join(pieces) + ".git"
    ref = spec.get("ref", "HEAD")
    if (not isinstance(ref, str) or not ref or len(ref) > 256 or ref.startswith(("-", "/")) or
            ref.endswith(("/", ".")) or ".." in ref or "@{" in ref or "//" in ref or
            any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char in "~^:?*[\\" for char in ref) or
            any(part.startswith(".") or part.endswith(".lock") for part in ref.split("/")) or ref == "@"):
        raise RepositoryError("Repository ref must be a branch, tag, HEAD, or commit ID.", "INVALID_REPOSITORY_REF")
    directory = spec.get("directory", "source")
    if (not isinstance(directory, str) or len(directory) > 256 or
            not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part) for part in directory.split("/"))):
        raise RepositoryError("Repository directory must be a relative workspace directory without traversal.", "INVALID_REPOSITORY_DIRECTORY")
    credential = spec.get("credential")
    if credential is not None and (not isinstance(credential, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", credential)):
        raise RepositoryError("Repository credential must name an operator-owned profile.", "INVALID_REPOSITORY_CREDENTIAL")
    result = {"url": canonical, "ref": ref, "directory": directory, "transport": transport}
    if credential is not None:
        result["credential"] = credential
    return result


def _credential_file(value, *, private: bool) -> Path:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise RepositoryError("Credential profiles require absolute operator-owned file paths.", "INVALID_GIT_CREDENTIAL_PROFILE")
    path = Path(value)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or (private and info.st_mode & 0o077):
            raise RepositoryError("Git credential files must be regular files; secrets require private permissions.", "INVALID_GIT_CREDENTIAL_PROFILE")
        if private and hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise RepositoryError("Git secret files must belong to the worker account.", "INVALID_GIT_CREDENTIAL_PROFILE")
    except OSError:
        raise RepositoryError("A configured Git credential file is unavailable.", "GIT_CREDENTIAL_UNAVAILABLE") from None
    return path


def _profile(spec: dict) -> dict | None:
    name = spec.get("credential")
    if name is None:
        if spec["transport"] == "ssh":
            raise RepositoryError("SSH repository access requires a named credential profile with an identity and known_hosts file.", "GIT_CREDENTIAL_REQUIRED")
        return None
    config_path = os.environ.get("FOREST_GIT_CREDENTIALS_FILE")
    if not config_path:
        raise RepositoryError("Configure FOREST_GIT_CREDENTIALS_FILE on the worker for named Git credentials.", "GIT_CREDENTIAL_UNAVAILABLE")
    path = _credential_file(config_path, private=True)
    try:
        if path.stat().st_size > 65536:
            raise ValueError
        profiles = json.loads(path.read_text(encoding="utf-8"))["profiles"]
        profile = profiles[name]
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        raise RepositoryError("The requested Git credential profile is unavailable or invalid.", "INVALID_GIT_CREDENTIAL_PROFILE") from None
    if not isinstance(profile, dict) or profile.get("type") != spec["transport"]:
        raise RepositoryError("Git credential profile type must match the repository transport.", "INVALID_GIT_CREDENTIAL_PROFILE")
    allowed = {"type", "identity_file", "known_hosts_file"} if spec["transport"] == "ssh" else {"type", "token_file"}
    if set(profile) != allowed:
        raise RepositoryError("Git credential profile fields are invalid.", "INVALID_GIT_CREDENTIAL_PROFILE")
    result = {"type": spec["transport"]}
    for key in allowed - {"type"}:
        result[key] = _credential_file(profile[key], private=key != "known_hosts_file")
    if spec["transport"] == "https":
        try:
            token_path = result["token_file"]
            if token_path.stat().st_size > 16384:
                raise ValueError
            token = token_path.read_text(encoding="utf-8").strip()
            if not token or any(char.isspace() or ord(char) < 32 for char in token):
                raise ValueError
        except (OSError, UnicodeError, ValueError):
            raise RepositoryError("The configured Git token file is unavailable or invalid.", "INVALID_GIT_CREDENTIAL_PROFILE") from None
    return result


def _git_environment(auth_dir: Path, profile: dict | None) -> dict:
    # Avoid global Git config (including helpers, URL rewrites, proxy settings,
    # hooks and templates) and SSH agents. Auth files stay outside run artifacts.
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP") if key in os.environ}
    env.update(HOME=str(auth_dir), XDG_CONFIG_HOME=str(auth_dir), LANG="C", LC_ALL="C",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never", GIT_SSH_VARIANT="ssh")
    if profile and profile["type"] == "ssh":
        ssh_args = ["ssh", "-F", os.devnull, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                    "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
                    "-o", "StrictHostKeyChecking=yes", "-o", "GlobalKnownHostsFile=" + os.devnull,
                    "-o", 'UserKnownHostsFile="' + str(profile["known_hosts_file"]).replace('\\', '\\\\').replace('"', '\\"') + '"',
                    "-o", "IdentityAgent=none", "-o", "PasswordAuthentication=no",
                    "-o", "KbdInteractiveAuthentication=no", "-i", str(profile["identity_file"])]
        env["GIT_SSH_COMMAND"] = shlex.join(ssh_args)
    elif profile:
        askpass = auth_dir / "askpass"
        askpass.write_text('#!/bin/sh\ncase "$1" in\n*Username*) printf "%s\\n" x-access-token ;;\n*Password*) cat -- "$FOREST_GIT_TOKEN_FILE" ;;\n*) exit 1 ;;\nesac\n', encoding="utf-8")
        askpass.chmod(0o700)
        env.update(GIT_ASKPASS=str(askpass), FOREST_GIT_TOKEN_FILE=str(profile["token_file"]))
    return env


def _git(arguments: list[str], cwd: Path, env: dict) -> str:
    command = ["git", "-c", "credential.helper=", "-c", "core.hooksPath=" + os.devnull,
               "-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
               "-c", "protocol.https.allow=always", "-c", "protocol.ssh.allow=always",
               "-c", "http.followRedirects=false", "-c", "http.lowSpeedLimit=1", "-c", "http.lowSpeedTime=30",
               "-c", "submodule.recurse=false", *arguments]
    try:
        # Remain in the executor's process group so worker pause/cancel controls
        # cover Git and its helpers as well as the subsequent task.
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        stdout, _ = process.communicate(timeout=_GIT_TIMEOUT_SECONDS)
    except FileNotFoundError:
        raise RepositoryError("Install Git on the worker before preparing repository sources.", "GIT_UNAVAILABLE") from None
    except subprocess.TimeoutExpired:
        import psutil
        # Kill SSH/HTTPS descendants before Git: leaving a helper holding the
        # stdout pipe open can otherwise hang timeout cleanup indefinitely.
        try:
            descendants = psutil.Process(process.pid).children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            descendants = []
        for child in reversed(descendants):
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        process.kill()
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            if process.stdout:
                process.stdout.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        raise RepositoryError("Repository preparation timed out. Check repository access and retry.", "GIT_TIMEOUT") from None
    except OSError:
        raise RepositoryError("Unable to launch Git for repository preparation.", "GIT_UNAVAILABLE") from None
    if process.returncode:
        # Never relay Git/SSH stderr: helpers and servers can echo credentials.
        raise RepositoryError(f"Repository Git operation failed (exit code {process.returncode}). Check repository access and requested ref.", "GIT_OPERATION_FAILED")
    return stdout.strip()


def _target(workspace: Path, directory: str) -> Path:
    target = workspace / directory
    for path in (target, *target.parents):
        if path == workspace.parent:
            break
        if path.is_symlink():
            raise RepositoryError("Repository workspace directories cannot be symlinks.", "INVALID_REPOSITORY_DIRECTORY")
    if not target.resolve().is_relative_to(workspace.resolve()):
        raise RepositoryError("Repository directory must remain inside the run workspace.", "INVALID_REPOSITORY_DIRECTORY")
    return target


def _write_manifest(path: Path, manifest: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".repository-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2)
            stream.write("\n")
        Path(name).replace(path)
    finally:
        Path(name).unlink(missing_ok=True)


def prepare_repository(spec: dict, workspace: Path, output: Path) -> dict:
    """Clone a new source or retain edits to an already pinned same-run source."""
    spec = normalize_repository(spec)
    workspace, output = Path(workspace), Path(output)
    workspace.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    target = _target(workspace, spec["directory"])
    manifest_path = output / "repository.json"
    if manifest_path.is_symlink():
        raise RepositoryError("Repository source receipts must be regular files, not symlinks.", "REPOSITORY_SOURCE_MISMATCH")
    expected = {"url": spec["url"], "requested_ref": spec["ref"], "directory": spec["directory"], "transport": spec["transport"]}
    if target.exists():
        try:
            if manifest_path.stat().st_size > 8192:
                raise ValueError
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise RepositoryError("Repository directory is occupied without a matching source receipt; existing files were preserved.", "REPOSITORY_DIRECTORY_OCCUPIED") from None
        if (not isinstance(manifest, dict) or set(manifest) != {*expected, "commit"} or
                any(manifest.get(key) != value for key, value in expected.items()) or
                not isinstance(manifest.get("commit"), str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", manifest["commit"]) or
                not target.is_dir() or not (target / ".git").is_dir() or (target / ".git").is_symlink()):
            raise RepositoryError("Repository directory does not match this run's source receipt; existing files were preserved.", "REPOSITORY_SOURCE_MISMATCH")
        with tempfile.TemporaryDirectory(prefix="forest-git-") as auth:
            commit = _git(["rev-parse", "--verify", "HEAD^{commit}"], target, _git_environment(Path(auth), None))
        if commit != manifest["commit"]:
            raise RepositoryError("Repository HEAD differs from the pinned source receipt; existing files were preserved.", "REPOSITORY_SOURCE_MISMATCH")
        return manifest
    if manifest_path.exists():
        raise RepositoryError("The recorded repository checkout is missing; use a new run instead of replacing its source.", "REPOSITORY_SOURCE_MISMATCH")
    profile = _profile(spec)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="forest-git-") as auth, tempfile.TemporaryDirectory(prefix=".forest-source-", dir=target.parent) as staging:
        auth_dir = Path(auth)
        empty_template = auth_dir / "template"
        empty_template.mkdir()
        env = _git_environment(auth_dir, profile)
        staged = Path(staging) / "source"
        _git(["clone", "--quiet", "--no-checkout", "--no-tags", "--depth", "1", "--template=" + str(empty_template), "--", spec["url"], str(staged)], workspace, env)
        _git(["fetch", "--quiet", "--no-tags", "--depth", "1", "origin", spec["ref"]], staged, env)
        commit = _git(["rev-parse", "--verify", "FETCH_HEAD^{commit}"], staged, env)
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            raise RepositoryError("Git did not return a valid source commit.", "GIT_INVALID_COMMIT")
        _git(["checkout", "--quiet", "--detach", commit], staged, env)
        # Store only safe configuration in the checkout: never a helper, SSH
        # command, askpass path, token, or profile file reference.
        _git(["config", "core.hooksPath", os.devnull], staged, env)
        _git(["config", "submodule.recurse", "false"], staged, env)
        _git(["remote", "set-url", "origin", spec["url"]], staged, env)
        if target.exists() or target.is_symlink():
            raise RepositoryError("Repository directory became occupied; existing files were preserved.", "REPOSITORY_DIRECTORY_OCCUPIED")
        try:
            # An exclusive empty reservation makes a concurrent newly-created
            # directory a conflict, even when that directory would be empty.
            target.mkdir()
        except FileExistsError:
            raise RepositoryError("Repository directory became occupied; existing files were preserved.", "REPOSITORY_DIRECTORY_OCCUPIED") from None
        try:
            staged.rename(target)
        except OSError:
            try:
                target.rmdir()
            except OSError:
                pass
            raise RepositoryError("Unable to publish the prepared checkout; existing files were preserved.", "REPOSITORY_DIRECTORY_OCCUPIED") from None
    manifest = {**expected, "commit": commit}
    _write_manifest(manifest_path, manifest)
    return manifest
