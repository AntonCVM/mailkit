"""Small shared helpers: how to call ourselves, where generated files go."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path


class MailkitError(Exception):
    """An error the user can act on: printed as one line, exit 1."""


def self_cmd() -> list[str]:
    """argv that runs this same mailkit, for the commands mbsync, gocryptfs and
    aerc call back (PassCmd, -extpass, *-cred-cmd). MAILKIT_BIN is set by the
    Nix wrapper; otherwise whatever `mailkit` is on PATH; in a checkout without
    an install, this interpreter with `-m mailkit`."""
    env = os.environ.get("MAILKIT_BIN")
    if env:
        return [env]
    found = shutil.which("mailkit")
    if found:
        return [found]
    return [sys.executable, "-m", "mailkit"]


def runtime_dir(instance: str) -> Path:
    """Generated config (mbsyncrc, aerc accounts.conf): rewritten on every
    run, never edited, lost on logout — so it can never go stale."""
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(tempfile.gettempdir(), f"mailkit-{os.getuid()}")
    d = Path(base) / "mailkit" / instance
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)
    return d


def write_private(path: Path, text: str) -> None:
    """Write a 0600 file, replacing it atomically."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def child_env(config_path: Path) -> dict[str, str]:
    """Environment for every child that may call back into mailkit: it must
    find the same instance config."""
    env = dict(os.environ)
    env["MAILKIT_CONFIG"] = str(config_path)
    return env


def log(msg: str) -> None:
    print(msg, file=sys.stderr)
