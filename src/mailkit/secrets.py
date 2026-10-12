"""Where passwords and container passphrases come from.

Two backends, chosen per instance in `[secrets]`:

- `secret-service` (default): the desktop's Secret Service, i.e. KeePassXC with
  FdoSecrets. Item attributes `mail <address> kind <password|cipher>`, stored in
  the collection named by the account's `keyring`. Lookups need no touch and no
  prompt: an unlocked database answers, a locked one answers nothing. Locking
  KeePassXC is the switch that stops every sync, send and mount at once.
- `command`: any program that prints the secret, e.g. `pass show mail/{address}/{kind}`.
  Placeholders: {address} {handle} {kind}.

Nothing here caches a secret, and nothing prints one except `get`.
"""

from __future__ import annotations

import shlex
import subprocess

from .config import Account
from .util import MailkitError

KINDS = ("password", "cipher")


def get(account: Account, kind: str = "password") -> str:
    if kind not in KINDS:
        raise MailkitError(f"secret kind is one of {KINDS}, got '{kind}'")
    inst = account.instance
    if inst.secret_backend == "command":
        argv = [a.format(address=account.address, handle=account.handle, kind=kind)
                for a in shlex.split(inst.secret_command or "")]
        what = f"secrets.command for {account.handle} ({kind})"
    else:
        argv = ["secret-tool", "lookup", "mail", account.address, "kind", kind]
        what = f"keyring item 'mail {account.address} kind {kind}'"
    try:
        r = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError:
        raise MailkitError(f"{argv[0]} not found (needed for {what})") from None
    value = r.stdout.rstrip("\n")
    # An empty password must never reach a login attempt.
    if r.returncode != 0 or not value:
        if inst.secret_backend == "command":
            raise MailkitError(f"{what} failed: {r.stderr.strip() or 'empty output'}")
        raise MailkitError(
            f"no {what} — its KeePassXC database '{account.keyring}' is locked, "
            f"or the item was never stored (mailkit secret set {account.handle} {kind})"
        )
    return value


def store(account: Account, kind: str, value: str | None = None) -> None:
    """Store into the account's own collection. value=None → secret-tool prompts."""
    inst = account.instance
    if inst.secret_backend != "secret-service":
        raise MailkitError("secrets.backend = 'command' is read-only: store the secret with that tool")
    if kind not in KINDS:
        raise MailkitError(f"secret kind is one of {KINDS}, got '{kind}'")
    col = collection_path(account.keyring or "")
    label = f"mail: {account.address} ({'gocryptfs passphrase' if kind == 'cipher' else kind})"
    argv = ["secret-tool", "store", f"--label={label}", f"--collection={col}",
            "mail", account.address, "kind", kind]
    r = subprocess.run(argv, input=value, text=True) if value is not None else subprocess.run(argv)
    if r.returncode != 0:
        raise MailkitError(f"could not store 'mail {account.address} kind {kind}' in {account.keyring}")


def exists(account: Account, kind: str) -> bool:
    try:
        get(account, kind)
        return True
    except MailkitError:
        return False


def _busctl(*args: str) -> str:
    r = subprocess.run(["busctl", "--user", "get-property", "org.freedesktop.secrets", *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise MailkitError("no Secret Service on the session bus (KeePassXC not running, or FdoSecrets off?)")
    return r.stdout.strip()


def collections() -> list[tuple[str, str, bool]]:
    """(object path, label, locked) for every collection = open KeePassXC database."""
    out = _busctl("/org/freedesktop/secrets", "org.freedesktop.Secret.Service", "Collections")
    # `ao 2 "/org/…/a" "/org/…/b"`
    paths = [p.strip('"') for p in out.split()[2:]]
    result = []
    for p in paths:
        label = _busctl(p, "org.freedesktop.Secret.Collection", "Label").split(" ", 1)[1].strip('"')
        locked = _busctl(p, "org.freedesktop.Secret.Collection", "Locked").split(" ", 1)[1] == "true"
        result.append((p, label, locked))
    return result


def collection_path(label: str) -> str:
    """secret-tool's --collection wants the D-Bus object path, not the label
    (KeePassXC encodes the name: pass-mail → pass_2Dmail), and only an
    unlocked database can be written to."""
    for path, lab, locked in collections():
        if lab == label:
            if locked:
                raise MailkitError(f"keyring '{label}' is locked — unlock it in KeePassXC first")
            return path
    raise MailkitError(
        f"keyring '{label}' is not on the Secret Service (database not open in KeePassXC, "
        "or its FdoSecrets integration is off? see: mailkit keyrings)"
    )
