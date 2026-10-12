"""The instance config: one TOML file declares the accounts, nothing else does.

Where it is: $MAILKIT_CONFIG, else $XDG_CONFIG_HOME/mailkit/config.toml. A
project that carries its own accounts (a work repo) exports MAILKIT_CONFIG
from its .envrc; the personal accounts use the default path.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TLS_MODES = ("tls", "starttls", "none")
SEND_POLICIES = ("direct", "approved")
SECRET_BACKENDS = ("secret-service", "command")
LOCALHOSTS = ("localhost", "127.0.0.1", "::1")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Server:
    host: str
    port: int
    tls: str


@dataclass(frozen=True)
class Folders:
    """IMAP names, as the server spells them (`INBOX.Sent Items`)."""

    inbox: str = "INBOX"
    sent: str | None = None
    drafts: str | None = None
    trash: str | None = None
    spam: str | None = None


@dataclass(frozen=True)
class Account:
    handle: str
    address: str
    name: str
    login: str
    keyring: str | None
    imap: Server
    smtp: Server
    folders: Folders
    # False when the server files its own copy of what SMTP sends (Gmail).
    sent_copy: bool
    seal: bool
    # True: a message deleted on the server disappears from the mirror too.
    expunge: bool
    instance: Instance = field(repr=False, compare=False)

    @property
    def mountpoint(self) -> Path:
        """What the tools read: the plaintext view (a FUSE mount when sealed)."""
        return self.instance.root / self.handle

    @property
    def cipher_dir(self) -> Path:
        return self.instance.root / ".cipher" / self.handle

    @property
    def mirror(self) -> Path:
        """mbsync's Maildir tree. Nothing else writes here."""
        return self.mountpoint / "mail"

    @property
    def outbox(self) -> Path:
        """Drafts waiting to be sent: a Maildir of MML files."""
        return self.mountpoint / "outbox"

    @property
    def sent_local(self) -> Path:
        """What mailkit sent, as sent (compiled MIME): the record if the IMAP copy failed."""
        return self.mountpoint / "sent"

    @property
    def profile(self) -> str:
        # aerc reads the notmuch profile out of `notmuch://<profile>` with
        # url.Hostname(): letters, digits, '-' and '.' only, so no '@' or '_'.
        return f"mailkit.{self.instance.name}.{self.handle}"

    @property
    def from_header(self) -> str:
        """Readable UTF-8, not RFC 2047 (`=?utf-8?q?…`): it is written into
        drafts people edit and into aerc's config; the encoding happens when
        the message is compiled."""
        name = self.name
        if not name or name == self.address:
            return self.address
        if re.search(r'[()<>@,;:\\".\[\]]', name):
            name = '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return f"{name} <{self.address}>"


@dataclass
class Instance:
    name: str
    root: Path
    send_policy: str
    secret_backend: str
    secret_command: str | None
    config_path: Path
    accounts: dict[str, Account] = field(default_factory=dict)

    def account(self, handle: str) -> Account:
        try:
            return self.accounts[handle]
        except KeyError:
            raise ConfigError(
                f"unknown account '{handle}' (one of: {', '.join(self.accounts)}, all)"
            ) from None

    def select(self, selector: str | None) -> list[Account]:
        """`<handle|all>` → accounts. None is an error on purpose: naming the
        account is the point of per-account seals."""
        if selector is None:
            raise ConfigError(f"which account? ({', '.join(self.accounts)}, all)")
        if selector == "all":
            return list(self.accounts.values())
        return [self.account(selector)]


def config_path() -> Path:
    env = os.environ.get("MAILKIT_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "mailkit" / "config.toml"


def _server(raw: object, what: str, handle: str) -> Server:
    if not isinstance(raw, dict):
        raise ConfigError(f"{handle}: '{what}' must be a table {{ host, port, tls }}")
    try:
        host, port = str(raw["host"]), int(raw["port"])
    except KeyError as e:
        raise ConfigError(f"{handle}: '{what}' is missing {e}") from None
    tls = raw.get("tls", "tls")
    if tls not in TLS_MODES:
        raise ConfigError(f"{handle}: {what}.tls is one of {TLS_MODES}, got '{tls}'")
    if tls == "none" and host not in LOCALHOSTS:
        raise ConfigError(f"{handle}: {what}.tls = 'none' is only allowed for localhost")
    return Server(host, port, tls)


def _merge(defaults: dict, own: dict) -> dict:
    """`[defaults]` under each account; tables (imap, smtp, folders) merge one level."""
    out = dict(defaults)
    for k, v in own.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def load(path: Path | None = None) -> Instance:
    path = path or config_path()
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"no config at {path} (set MAILKIT_CONFIG, or see docs/config.md)") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None

    top = data.get("mailkit", {})
    name = top.get("instance", "default")
    if not HANDLE_RE.match(name):
        raise ConfigError(f"mailkit.instance '{name}': lowercase letters, digits and '-' only")
    root = Path(os.path.expanduser(top.get("root", f"~/Mail/{name}")))
    if not root.is_absolute():
        root = (path.parent / root).resolve()
    policy = top.get("send_policy", "direct")
    if policy not in SEND_POLICIES:
        raise ConfigError(f"mailkit.send_policy is one of {SEND_POLICIES}, got '{policy}'")
    secrets = data.get("secrets", {})
    backend = secrets.get("backend", "secret-service")
    if backend not in SECRET_BACKENDS:
        raise ConfigError(f"secrets.backend is one of {SECRET_BACKENDS}, got '{backend}'")
    command = secrets.get("command")
    if backend == "command" and not command:
        raise ConfigError("secrets.backend = 'command' needs secrets.command")

    inst = Instance(name, root, policy, backend, command, path)
    defaults = data.get("defaults", {})
    accounts = data.get("accounts", {})
    if not accounts:
        raise ConfigError(f"{path}: no [accounts.<handle>] declared")
    for handle, own in accounts.items():
        if not HANDLE_RE.match(handle):
            raise ConfigError(f"account '{handle}': lowercase letters, digits and '-' only")
        if handle == "all":
            raise ConfigError("'all' is reserved, it selects every account")
        a = _merge(defaults, own)
        try:
            address = a["address"]
        except KeyError:
            raise ConfigError(f"{handle}: 'address' is required") from None
        folders = a.get("folders", {})
        unknown = set(folders) - {"inbox", "sent", "drafts", "trash", "spam"}
        if unknown:
            raise ConfigError(f"{handle}: unknown folder keys {sorted(unknown)}")
        keyring = a.get("keyring")
        if backend == "secret-service" and not keyring:
            raise ConfigError(f"{handle}: 'keyring' (the KeePassXC database name) is required")
        inst.accounts[handle] = Account(
            handle=handle,
            address=address,
            name=a.get("name", address),
            login=a.get("login", address),
            keyring=keyring,
            imap=_server(a.get("imap"), "imap", handle),
            smtp=_server(a.get("smtp"), "smtp", handle),
            folders=Folders(**folders),
            sent_copy=bool(a.get("sent_copy", True)),
            seal=bool(a.get("seal", True)),
            expunge=bool(a.get("expunge", True)),
            instance=inst,
        )
    return inst
