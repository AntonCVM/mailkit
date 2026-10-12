"""The outbox: drafts waiting to go, one Maildir per account inside its store.

A draft is an MML file in `<store>/outbox/cur/`, named the Maildir way with
the D(raft) and S(een) flags, so notmuch indexes it like any message: it gets
`draft` and `review` from the tag rules and can be searched, read and tagged
with the rest of the mail. Its Message-ID is set at creation and is its id.

Review is a tag, not a place: `approve` swaps `review` for `approved`, and
`send --approved` sends exactly those. Whether `send` also takes a draft
nobody approved is the instance's `send_policy` — a convention for the
people and agents using it, not a lock: anything that can read the unlocked
keyring can send mail without mailkit. Locking KeePassXC is the lock.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as policy
from email.utils import make_msgid, parseaddr
from pathlib import Path

from . import index, mml, seal
from .config import Account, Instance
from .util import MailkitError

_seq = 0


@dataclass
class Entry:
    account: Account
    path: Path
    msgid: str
    tags: list[str]
    headers: dict[str, str]


def _unique() -> str:
    global _seq
    _seq += 1
    return f"{int(time.time())}.M{time.time_ns() % 1_000_000}P{os.getpid()}Q{_seq}.{socket.gethostname()}"


def _bare(msgid: str) -> str:
    msgid = msgid.strip()
    if msgid.startswith("id:"):
        msgid = msgid[3:]
    return msgid.strip("<>")


def deliver(box: Path, data: bytes, flags: str) -> Path:
    """Maildir delivery: write in tmp/, rename into cur/ (atomic for readers)."""
    name = _unique()
    tmp = box / "tmp" / name
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    final = box / "cur" / f"{name}:2,{flags}"
    os.rename(tmp, final)
    return final


def prepare(a: Account, raw: str) -> tuple[str, str]:
    """Fill what a draft must carry and refuse what it must not: From is the
    account (set if missing, an error if it names another address), and a
    Message-ID so notmuch and the reply that comes back can find it."""
    head, _ = mml.split(raw)
    headers = mml.parse_headers(head) if head.strip() else []
    from_ = next((v for k, v in headers if k.lower() == "from"), None)
    if from_ is None:
        raw = mml.set_header(raw, "From", a.from_header)
    elif parseaddr(from_)[1].lower() != a.address.lower():
        raise MailkitError(f"From: {from_} is not {a.handle} ({a.address}) — wrong account?")
    msgid = next((v for k, v in headers if k.lower() == "message-id"), None)
    if msgid is None:
        msgid = make_msgid(domain=a.address.rpartition("@")[2])
        raw = mml.set_header(raw, "Message-ID", msgid)
    return raw, _bare(msgid)


def new(a: Account, raw: str) -> Entry:
    seal.require_open(a)
    raw, msgid = prepare(a, raw)
    path = deliver(a.outbox, raw.encode("utf-8"), "DS")
    index.update(a)
    return Entry(a, path, msgid, tags_of(a, msgid), _headers(path))


def reply(a: Account, query: str, everyone: bool) -> Entry:
    seal.require_open(a)
    out = index.run(a, "reply", f"--reply-to={'all' if everyone else 'sender'}", query)
    if not out.strip():
        raise MailkitError(f"{a.handle}: nothing matches {query!r}")
    # notmuch picks From among the profile's addresses: that is this account.
    raw = mml.set_header(out, "From", a.from_header)
    return new(a, raw)


def forward(a: Account, query: str) -> Entry:
    seal.require_open(a)
    files = index.run(a, "search", "--output=files", "--limit=1", query).split("\n")
    path = files[0].strip() if files else ""
    if not path:
        raise MailkitError(f"{a.handle}: nothing matches {query!r}")
    with open(path, "rb") as f:
        orig = BytesParser(policy=policy).parse(f)
    body = orig.get_body(preferencelist=("plain",))
    text = body.get_content() if body is not None else "(no plain-text body: see the attached message)"
    subject = str(orig.get("subject", ""))
    quoted = "\n".join([
        "", "", "---------- Forwarded message ----------",
        f"From: {orig.get('from', '')}",
        f"Date: {orig.get('date', '')}",
        f"Subject: {subject}",
        f"To: {orig.get('to', '')}",
        "", text.rstrip("\n"), "",
        f"<#part type=message/rfc822 filename=\"{path}\" name=forwarded.eml><#/part>", "",
    ])
    raw = "\n".join([
        f"From: {a.from_header}",
        "To: ",
        f"Subject: {subject if subject.lower().startswith('fwd:') else 'Fwd: ' + subject}",
    ]) + "\n" + quoted
    return new(a, raw)


def _headers(path: Path) -> dict[str, str]:
    head, _ = mml.split(path.read_text(encoding="utf-8", errors="replace"))
    return {k.lower(): v for k, v in mml.parse_headers(head)}


def tags_of(a: Account, msgid: str) -> list[str]:
    out = index.run(a, "search", "--output=tags", f"id:{msgid}")
    return [t for t in out.split("\n") if t]


def entries(a: Account) -> list[Entry]:
    """Drafts of an open account, oldest first. Read from the files, not the
    index: a draft edited in place keeps its old text in the index until
    reindexed, and the file is what gets sent."""
    seal.require_open(a)
    files = sorted((p for p in (a.outbox / "cur").iterdir() if p.is_file()),
                   key=lambda p: p.stat().st_mtime)
    files += sorted(p for p in (a.outbox / "new").iterdir() if p.is_file())
    if not files:
        return []
    # One dump instead of one search per draft: `+tag +tag -- id:<msgid>`.
    tags: dict[str, list[str]] = {}
    for line in index.run(a, "dump", "--format=batch-tag", "--", 'path:"outbox/**"').split("\n"):
        if " -- id:" in line:
            t, _, mid = line.partition(" -- id:")
            tags[_unquote_id(mid)] = [x[1:] for x in t.split() if x.startswith("+")]
    result = []
    for p in files:
        h = _headers(p)
        mid = _bare(h.get("message-id", ""))
        result.append(Entry(a, p, mid, tags.get(mid, []), h))
    return result


def _unquote_id(s: str) -> str:
    # batch-tag quotes ids with special characters as "..." with "" escapes,
    # and hex-encodes tags; ids we generate are plain, so this is enough.
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1].replace('""', '"')
    return s


def find(inst: Instance, ref: str) -> Entry:
    """A draft by file path or by Message-ID (with or without id:/<>), in any open account."""
    p = Path(ref).expanduser()
    if p.is_file():
        rp = p.resolve()
        for a in inst.accounts.values():
            if seal.is_open(a) and a.outbox.resolve() in rp.parents:
                mid = _bare(_headers(rp).get("message-id", ""))
                return Entry(a, rp, mid, tags_of(a, mid) if mid else [], _headers(rp))
        raise MailkitError(f"{ref} is not in the outbox of any open account")
    want = _bare(ref)
    for a in inst.accounts.values():
        if not seal.is_open(a):
            continue
        for e in entries(a):
            if e.msgid == want:
                return e
    raise MailkitError(f"no draft with id {want} in any open account (closed ones are not searched)")


def tag(e: Entry, *changes: str) -> None:
    index.run(e.account, "tag", *changes, "--", f"id:{e.msgid}")


def discard(e: Entry) -> None:
    e.path.unlink()
    index.update(e.account)


def edit(e: Entry) -> None:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    subprocess.run([*editor.split(), str(e.path)])
    index.run(e.account, "reindex", f"id:{e.msgid}", check=False)
