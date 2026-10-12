"""Per-account seal: a gocryptfs container with its own passphrase.

What it buys: the disk is presumably already encrypted (LUKS), so a second
layer only matters while the machine is up — against processes running as
this user (coding agents, a bad postinstall). Closed, they see ciphertext;
open, they see everything. Hence: open for a session, close after.

gocryptfs because it needs no root, grows on its own, and its ciphertext
directory rsyncs incrementally. Not `-fido2`: gocryptfs.conf holds ONE
credential, so a backup key could never open it.

`seal = false` accounts have a plain directory instead and every verb here is
a no-op for them: always "open".
"""

from __future__ import annotations

import os
import secrets as pysecrets
import subprocess
from pathlib import Path

from . import secrets
from .config import Account
from .util import MailkitError, child_env, log, self_cmd

OPEN, CLOSED, UNINIT, PLAIN = "open", "closed", "uninit", "plain"


def is_mounted(p: Path) -> bool:
    try:
        return os.path.ismount(p)
    except OSError:
        return False


def state(a: Account) -> str:
    if not a.seal:
        return PLAIN if a.mountpoint.is_dir() else UNINIT
    if not (a.cipher_dir / "gocryptfs.conf").is_file():
        return UNINIT
    return OPEN if is_mounted(a.mountpoint) else CLOSED


def is_open(a: Account) -> bool:
    return state(a) in (OPEN, PLAIN)


def require_open(a: Account) -> None:
    s = state(a)
    if s == CLOSED:
        raise MailkitError(f"{a.handle} is closed — run: mailkit open {a.handle}")
    if s == UNINIT:
        raise MailkitError(f"{a.handle} has no store yet — run: mailkit init {a.handle}")


def _layout(a: Account) -> None:
    """The tree inside the (open) store. Created on every open: cheap, and a
    store made by an older version gets what it lacks."""
    for d in (a.mirror, a.mountpoint / ".notmuch"):
        d.mkdir(parents=True, exist_ok=True)
    for box in (a.outbox, a.sent_local):
        for sub in ("cur", "new", "tmp"):
            (box / sub).mkdir(parents=True, exist_ok=True)


def _seal_dir(a: Account) -> None:
    # Closed mount points stay mode 500: a stray mbsync against the empty
    # directory fails with EACCES instead of writing plaintext to disk.
    os.chmod(a.mountpoint, 0o500)


def init(a: Account) -> None:
    a.instance.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not a.seal:
        a.mountpoint.mkdir(mode=0o700, exist_ok=True)
        _layout(a)
        print(f"{a.handle}: plain store at {a.mountpoint} (seal = false)")
        return
    if state(a) != UNINIT:
        print(f"{a.handle}: already initialised")
        return
    if not secrets.exists(a, "cipher"):
        print(f"{a.handle}: generating a container passphrase → {a.keyring}")
        secrets.store(a, "cipher", pysecrets.token_urlsafe(48))
    a.cipher_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(a.cipher_dir.parent, 0o700)
    r = subprocess.run(["gocryptfs", "-q", "-init", *_extpass(a), str(a.cipher_dir)],
                       env=child_env(a.instance.config_path))
    if r.returncode != 0:
        raise MailkitError(f"{a.handle}: gocryptfs -init failed")
    a.mountpoint.mkdir(mode=0o700, exist_ok=True)
    _seal_dir(a)
    print(f"{a.handle}: container created at {a.cipher_dir}")


def _extpass(a: Account) -> list[str]:
    # One -extpass per argv element, in the `=` form: Go's flag parser would
    # take a separate value starting with '-' (`-m`, of `python -m`) for a flag.
    argv = [*self_cmd(), "secret", "get", a.handle, "cipher"]
    return [f"-extpass={arg}" for arg in argv]


def open_(a: Account) -> bool:
    """Returns True if this call mounted it (so the caller may close it later)."""
    s = state(a)
    if s == UNINIT:
        raise MailkitError(f"{a.handle}: no store yet — run: mailkit init {a.handle}")
    if s in (OPEN, PLAIN):
        _layout(a)
        if s == OPEN:
            print(f"{a.handle}: already open")
        return False
    # fusermount refuses to mount on a directory the user cannot write.
    os.chmod(a.mountpoint, 0o700)
    r = subprocess.run(["gocryptfs", "-q", *_extpass(a), str(a.cipher_dir), str(a.mountpoint)],
                       env=child_env(a.instance.config_path))
    if r.returncode != 0 or not is_mounted(a.mountpoint):
        _seal_dir(a)
        raise MailkitError(f"{a.handle}: mount failed")
    _layout(a)
    print(f"{a.handle}: open")
    return True


def close(a: Account) -> bool:
    """Teardown verifies, it does not assume: a busy mount (aerc still inside,
    a shell cd'd there) stays mounted and says so. Returns True if closed."""
    if not a.seal:
        print(f"{a.handle}: plain store, nothing to close")
        return True
    if not is_mounted(a.mountpoint):
        print(f"{a.handle}: closed")
        return True
    for tool in ("fusermount3", "fusermount"):
        try:
            subprocess.run([tool, "-u", str(a.mountpoint)], capture_output=True)
        except FileNotFoundError:
            continue
        if not is_mounted(a.mountpoint):
            break
    if is_mounted(a.mountpoint):
        log(f"{a.handle}: STILL MOUNTED (busy? something has it open)")
        return False
    _seal_dir(a)
    print(f"{a.handle}: closed")
    return True


def du(p: Path) -> str:
    r = subprocess.run(["du", "-sh", str(p)], capture_output=True, text=True)
    return r.stdout.split("\t")[0] if r.returncode == 0 and r.stdout else "-"
