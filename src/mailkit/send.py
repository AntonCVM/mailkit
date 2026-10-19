"""Sending: compile the draft, SMTP, a copy in the server's Sent, then the
draft leaves the outbox.

Order matters. The SMTP hand-off is the point of no return: once it succeeded
the draft is gone from the outbox (sending it twice is worse than a missing
copy) and the compiled message is kept in `<store>/sent/`, which notmuch
indexes — so what was sent is on record even if the IMAP copy fails. A failed
copy is reported, tagged `copy-failed`, and the command exits non-zero.
"""

from __future__ import annotations

import imaplib
import smtplib
import ssl
import time
from email.message import EmailMessage

from . import index, mml, outbox, secrets
from .config import Account, Server
from .util import MailkitError, log


def _smtp(s: Server) -> smtplib.SMTP:
    ctx = ssl.create_default_context()
    if s.tls == "tls":
        return smtplib.SMTP_SSL(s.host, s.port, context=ctx, timeout=60)
    conn = smtplib.SMTP(s.host, s.port, timeout=60)
    if s.tls == "starttls":
        conn.starttls(context=ctx)
    return conn


def _imap(s: Server) -> imaplib.IMAP4:
    if s.tls == "tls":
        return imaplib.IMAP4_SSL(s.host, s.port, ssl_context=ssl.create_default_context(), timeout=60)
    conn = imaplib.IMAP4(s.host, s.port, timeout=60)
    if s.tls == "starttls":
        conn.starttls(ssl_context=ssl.create_default_context())
    return conn


def imap_utf7(name: str) -> str:
    """RFC 3501 modified UTF-7 for mailbox names ("Drafts" stays as is,
    "Sent Items" too; anything non-ASCII gets &…- encoded)."""
    import base64

    out, buf = [], []

    def flush():
        if buf:
            b = base64.b64encode("".join(buf).encode("utf-16-be")).decode().rstrip("=").replace("/", ",")
            out.append(f"&{b}-")
            buf.clear()

    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def _quote(mailbox: str) -> str:
    return '"' + imap_utf7(mailbox).replace("\\", "\\\\").replace('"', '\\"') + '"'


def append_sent(a: Account, password: str, data: bytes) -> None:
    conn = _imap(a.imap)
    try:
        conn.login(a.login, password)
        typ, resp = conn.append(_quote(a.folders.sent or ""), r"(\Seen)",
                                imaplib.Time2Internaldate(time.time()), data)
        if typ != "OK":
            raise MailkitError(f"APPEND to {a.folders.sent}: {resp}")
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def compile_entry(e: outbox.Entry) -> tuple[EmailMessage, list[str]]:
    raw = e.path.read_text(encoding="utf-8")
    msg, rcpts = mml.compile_draft(raw)
    from email.utils import parseaddr

    if parseaddr(str(msg["From"]))[1].lower() != e.account.address.lower():
        raise MailkitError(f"From: {msg['From']} is not {e.account.handle} ({e.account.address})")
    return msg, rcpts


def send(e: outbox.Entry, password: str) -> bool:
    """Returns False if sent but the Sent copy failed (still sent!)."""
    a = e.account
    msg, rcpts = compile_entry(e)
    head, _ = mml.split(e.path.read_text(encoding="utf-8"))
    bcc = [v for k, v in mml.parse_headers(head) if k.lower() == "bcc"]
    conn = _smtp(a.smtp)
    try:
        conn.login(a.login, password)
        refused = conn.send_message(msg, from_addr=a.address, to_addrs=rcpts)
    finally:
        try:
            conn.quit()
        except Exception:
            pass
    # ---- sent: from here on, never raise before the draft is out of the outbox.
    # Bcc never travels in the message, but the sender's own copy keeps it.
    for v in bcc:
        msg["Bcc"] = v
    data = msg.as_bytes()
    outbox.deliver(a.sent_local, data, "S")
    e.path.unlink()
    for addr, (code, why) in (refused or {}).items():
        log(f"{a.handle}: server refused {addr}: {code} {why!r}")
    ok = True
    if a.sent_copy and a.folders.sent:
        try:
            append_sent(a, password, data)
        except Exception as ex:  # noqa: BLE001 — any failure here is reported, not fatal
            log(f"{a.handle}: SENT, but the copy to '{a.folders.sent}' failed: {ex} "
                f"(kept in {a.sent_local})")
            ok = False
    # The sent/ copy carries the Date the draft lacked: reindex, or notmuch
    # keeps the draft's headers (dated 1970) for it.
    index.refresh(a, e.msgid)
    changes = ["-review", "-approved", "-draft", "+sent"] + ([] if ok else ["+copy-failed"])
    index.run(a, "tag", *changes, "--", f"id:{e.msgid}")
    return ok


def send_many(entries: list[outbox.Entry], dry_run: bool = False) -> int:
    """One secret lookup per account, every draft checked before any is sent."""
    for e in entries:
        compile_entry(e)  # raises on the first unsendable one, before anything left
    if dry_run:
        for e in entries:
            msg, rcpts = compile_entry(e)
            print(f"== {e.account.handle} {e.msgid} → {', '.join(rcpts)}")
            print(msg.as_string())
        return 0
    passwords: dict[str, str] = {}
    rc = 0
    for e in entries:
        a = e.account
        if a.handle not in passwords:
            passwords[a.handle] = secrets.get(a, "password")
        if send(e, passwords[a.handle]):
            print(f"{a.handle}: sent {e.msgid} — {e.headers.get('subject', '')}")
        else:
            print(f"{a.handle}: sent {e.msgid} (copy to Sent FAILED, see above)")
            rc = 1
    return rc
