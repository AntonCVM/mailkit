"""The agent ledger: what an agent has been shown, apart from what a person read.

`unread` is the person's: it mirrors the server's \\Seen flag. An agent that
sweeps the mail every day needs its own record, so that nothing slips between
two sweeps — and reading from the mirror must not change the person's state.

Per message, three states:

    pending ──agent read/next──▶ seen ──agent done──▶ done
        ▲                                               │
        └───────────────── agent unsee ◀────────────────┘

Marking happens in the command that prints the mail (`agent next`, `agent
read`), after the output was written: it never depends on the agent
remembering. `agent show` prints the same thread and marks nothing — mail as
context. What counts as processed is `[agent] pending`: "unseen" (shown once
is enough) or "undone" (until `agent done`, for flows where shown is not yet
handled).

The truth is the ledger, `<store>/agent/log.jsonl`, append-only and inside the
seal: msgid, operation, time. The `agent-seen` / `agent-done` tags are its
projection into the index, for queries; `agent restore` rebuilds them after a
rebuilt index. Nothing here touches the server or the `unread` tag.

A message is identified by Message-ID across the instance's accounts: the same
id in two accounts (a forward, a Cc) is listed once, and marked in every open
account that has it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from pathlib import Path

from . import index, seal
from .config import Account, Instance
from .util import MailkitError

SEEN, DONE = "agent-seen", "agent-done"
SCHEMA = 1  # the JSON output's "schema": bumped only on an incompatible change
SPAM_MODES = ("include", "exclude", "only")
_ID_RE = re.compile(r'id:("(?:[^"]|"")*"|\S+)')


# ---------------------------------------------------------------- helpers

def bare(ref: str) -> str:
    ref = ref.strip()
    if ref.startswith("id:"):
        ref = ref[3:]
        if len(ref) > 1 and ref[0] == ref[-1] == '"':
            ref = ref[1:-1].replace('""', '"')
    return ref.strip("<>")


def idq(msgid: str) -> str:
    return 'id:"' + msgid.replace('"', '""') + '"'


def _ids_query(ids) -> str:
    return " or ".join(idq(i) for i in ids)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes")


def _flatten(node, out: list) -> list:
    """notmuch show's threads → [message, replies] pairs, nested → messages."""
    if isinstance(node, dict):
        out.append(node)
    elif isinstance(node, list):
        for n in node:
            _flatten(n, out)
    return out


def _chunks(seq, n=200):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _open(inst: Instance, accts=None) -> list[Account]:
    return [a for a in (accts or inst.accounts.values()) if seal.is_open(a)]


def _show(a: Account, query: str, entire: bool = False) -> list[dict]:
    out = index.run(a, "show", "--format=json", "--body=false", "--exclude=false",
                    f"--entire-thread={'true' if entire else 'false'}", query)
    return _flatten(json.loads(out or "[]"), [])


def _messages(a: Account, query: str) -> list[str]:
    out = index.run(a, "search", "--output=messages", "--exclude=false", query)
    return [bare(i) for i in out.split("\n") if i]


def _has(a: Account, ids) -> set[str]:
    found: set[str] = set()
    for part in _chunks(ids):
        found.update(_messages(a, _ids_query(part)))
    return found


def _addr(h: str) -> str:
    return parseaddr(h or "")[1].lower()


# ---------------------------------------------------------------- the ledger

def _entries(a: Account) -> list[dict]:
    try:
        lines = a.agent_log.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue  # a torn last line after a crash: the rest stands
        if isinstance(e, dict) and "id" in e and "op" in e:
            out.append({**e, "account": a.handle})
    return out


def _replay(entries) -> dict[str, dict]:
    """msgid → {"seen": time|None, "done": time|None}; unmarked ids are absent."""
    state: dict[str, dict] = {}
    for e in entries:
        s = state.setdefault(e["id"], {"seen": None, "done": None})
        if e["op"] == "seen":
            s["seen"] = s["seen"] or e["at"]
        elif e["op"] == "done":
            s["done"] = e["at"]
            s["seen"] = s["seen"] or e["at"]
        elif e["op"] == "unsee":
            s.update(seen=None, done=None)
        elif e["op"] == "undone":
            s["done"] = None
    return {k: v for k, v in state.items() if v["seen"]}


def ledger(inst: Instance) -> dict[str, dict]:
    """The state across the open accounts: an id marked in any is marked. An
    account closed when another one was marked missed that entry; its own
    ledger alone would bring the message back as pending."""
    merged: dict[str, dict] = {}
    for a in _open(inst):
        for k, v in _replay(_entries(a)).items():
            m = merged.setdefault(k, {"seen": None, "done": None})
            m["seen"] = min(filter(None, (m["seen"], v["seen"])))
            if v["done"]:
                m["done"] = max(filter(None, (m["done"], v["done"])))
    return merged


_TAGS = {"seen": f"+{SEEN}", "done": f"+{SEEN} +{DONE}",
         "unsee": f"-{SEEN} -{DONE}", "undone": f"-{DONE}"}


def record(inst: Instance, op: str, ids, batch: str | None = None) -> dict[str, list[str]]:
    """Append `op` for `ids` to the ledger of every open account that has the
    message, then project it onto the tags. Ledger first: it is the truth.
    Returns account → ids marked there."""
    ids = list(dict.fromkeys(ids))
    at = _now()
    batch = batch or datetime.now().astimezone().isoformat(timespec="microseconds")
    marked: dict[str, list[str]] = {}
    for a in _open(inst):
        has = _has(a, ids) if ids else set()
        here = [i for i in ids if i in has]
        if not here:
            continue
        a.agent_log.parent.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(a.agent_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            for i in here:
                f.write(json.dumps({"at": at, "op": op, "id": i, "batch": batch},
                                   ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        index.run(a, "tag", "--batch", input="".join(f"{_TAGS[op]} -- {idq(i)}\n" for i in here))
        marked[a.handle] = here
    return marked


def entries(inst: Instance, accts=None) -> list[dict]:
    return sorted((e for a in _open(inst, accts) for e in _entries(a)), key=lambda e: e["at"])


def restore(a: Account) -> tuple[int, int, int]:
    """Re-project the ledger onto the index tags. Returns (in the ledger,
    tagged agent-seen in the index afterwards, ledger ids not in the index)."""
    state = _replay(_entries(a))
    lines = [f"-{SEEN} -{DONE} -- tag:{SEEN} or tag:{DONE}"]
    lines += [f"{_TAGS['done' if v['done'] else 'seen']} -- {idq(k)}" for k, v in state.items()]
    index.run(a, "tag", "--batch", input="\n".join(lines) + "\n")
    tagged = int(index.run(a, "count", "--exclude=false", f"tag:{SEEN}").strip() or 0)
    missing = len(state) - len(_has(a, state)) if state else 0
    return len(state), tagged, missing


# ---------------------------------------------------------------- queries

def require_since(inst: Instance) -> str:
    since = inst.agent.since
    if since is None:
        raise MailkitError(
            f"no agent cutoff: add to {inst.config_path}\n"
            f"  [agent]\n  since = \"YYYY-MM-DD\"   # older mail is never pending; \"all\": no cutoff")
    return since


def ours(inst: Instance) -> str:
    """What we sent: the Sent folders and sent/ (tag:sent), or From any of our addresses."""
    return " or ".join(["tag:sent"] + [f'from:"{a.address}"' for a in inst.accounts.values()])


def base(inst: Instance) -> str:
    """Could be pending: since the cutoff, received (not ours), not a draft,
    not in the trash. Spam is in: legitimate notices land there."""
    since = require_since(inst)
    q = f"not tag:draft and not tag:deleted and not ({ours(inst)})"
    return q if since == "all" else f"date:{since}.. and {q}"


def _not_ignored(inst: Instance) -> str:
    ig = inst.agent.ignore
    return f" and not ({' or '.join(f'({q})' for q in ig)})" if ig else ""


def _spam(mode: str) -> str:
    return {"include": "", "exclude": " and not tag:spam", "only": " and tag:spam"}[mode]


def closed(inst: Instance, st: dict | None) -> bool:
    if not st:
        return False
    return bool(st["done"] if inst.agent.pending == "undone" else st["seen"])


# ---------------------------------------------------------------- answered?

@dataclass
class Sent:
    account: str
    id: str
    ts: int
    thread: str
    rcpts: set[str]


def _sent_since(inst: Instance, ts: int) -> list[Sent]:
    out = []
    q = f"({ours(inst)}) and date:@{ts}.."
    for a in _open(inst):
        thread_of = {}
        summary = json.loads(index.run(a, "search", "--format=json", "--output=summary",
                                       "--exclude=false", q) or "[]")
        for t in summary:
            for m in _ID_RE.findall(t["query"][0] or ""):
                thread_of[bare("id:" + m)] = t["thread"]
        if not thread_of:
            continue
        for part in _chunks(thread_of):
            for m in _show(a, _ids_query(part)):
                h = m.get("headers", {})
                # Empty fields out: a strict getaddresses() rejects the whole list for one.
                fields = [v for v in (h.get("To"), h.get("Cc"), h.get("Bcc")) if v]
                rc = {addr.lower() for _, addr in getaddresses(fields) if addr}
                out.append(Sent(a.handle, m["id"], m.get("timestamp", 0), thread_of.get(m["id"], ""), rc))
    return out


def answered(sent: list[Sent], account: str, thread: str, sender: str, after: int) -> dict | None:
    """A message of ours later than `after`: in the same thread of that
    account, else to the same sender from any account. None: none found
    (among the open accounts — a closed one was not looked at)."""
    later = sorted((s for s in sent if s.ts > after), key=lambda s: s.ts)
    for how, hit in (("thread", lambda s: s.account == account and s.thread == thread),
                     ("sender", lambda s: bool(sender) and sender in s.rcpts)):
        for s in later:
            if hit(s):
                return {"how": how, "account": s.account, "id": s.id, "date": _iso(s.ts)}
    return None


# ---------------------------------------------------------------- pending

@dataclass
class Item:
    """One thread with pending mail, in the first account that has it."""

    account: str
    thread: str
    ids: list[str]                       # pending, oldest first
    accounts: list[str]
    subject: str = ""
    sender: str = ""                     # From of the newest pending message
    first: int = 0
    last: int = 0
    total: int = 0
    spam: bool = False
    states: dict[str, str] = field(default_factory=dict)  # id → new | seen
    answered: dict | None = None

    def json(self) -> dict:
        return {"ref": f"id:{self.ids[0]}", "account": self.account, "accounts": self.accounts,
                "thread": self.thread, "subject": self.subject, "from": self.sender,
                "first": _iso(self.first), "last": _iso(self.last), "messages": self.total,
                "pending": [{"id": i, "state": self.states.get(i, "new")} for i in self.ids],
                "spam": self.spam, "answered": self.answered}


def pending(inst: Instance, accts: list[Account], spam: str = "include") -> tuple[list[Item], dict]:
    state = ledger(inst)
    q = base(inst) + _spam(spam)
    items: list[Item] = []
    claimed: dict[str, Item] = {}
    ignored = 0
    for a in _open(inst, accts):
        if inst.agent.ignore:
            ignored += int(index.run(a, "count", "--exclude=false",
                                     f"({q}) and not ({q}{_not_ignored(inst)})").strip() or 0)
        summary = json.loads(index.run(a, "search", "--format=json", "--output=summary",
                                       "--exclude=false", "--sort=oldest-first",
                                       q + _not_ignored(inst)) or "[]")
        mine: list[Item] = []
        for t in summary:
            ids = [bare("id:" + m) for m in _ID_RE.findall(t["query"][0] or "")]
            ids = [i for i in ids if not closed(inst, state.get(i))]
            fresh = []
            for i in ids:
                if i in claimed:
                    if a.handle not in claimed[i].accounts:
                        claimed[i].accounts.append(a.handle)
                else:
                    fresh.append(i)
            if not fresh:
                continue
            it = Item(a.handle, t["thread"], fresh, [a.handle], " ".join(t.get("subject", "").split()),
                      total=t.get("total", 0))
            for i in fresh:
                claimed[i] = it
            mine.append(it)
        heads = {m["id"]: m for part in _chunks(i for it in mine for i in it.ids)
                 for m in _show(a, _ids_query(part))}
        for it in mine:
            msgs = sorted((heads[i] for i in it.ids if i in heads), key=lambda m: m.get("timestamp", 0))
            if not msgs:
                continue
            it.ids = [m["id"] for m in msgs]
            it.first, it.last = msgs[0].get("timestamp", 0), msgs[-1].get("timestamp", 0)
            it.sender = msgs[-1].get("headers", {}).get("From", "")
            it.spam = any("spam" in m.get("tags", []) for m in msgs)
            it.states = {i: ("seen" if (state.get(i) or {}).get("seen") else "new") for i in it.ids}
        items += [it for it in mine if it.first or it.last]
    items.sort(key=lambda it: it.first)
    if items:
        sent = _sent_since(inst, min(it.last for it in items) + 1)
        for it in items:
            it.answered = answered(sent, it.account, it.thread, _addr(it.sender), it.last)
    info = {"since": inst.agent.since, "pending_means": inst.agent.pending, "spam": spam,
            "ignored": ignored, "closed_accounts": [a.handle for a in accts if not seal.is_open(a)]}
    return items, info


# ---------------------------------------------------------------- reading a thread

class _Text(HTMLParser):
    """HTML → plain text, for mail whose text part is empty: never rendered,
    nothing fetched. Links keep their target in brackets, as text."""

    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol",
             "blockquote", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.href: list[str | None] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "a":
            self.href.append(dict(attrs).get("href"))

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag == "a" and self.href:
            h = self.href.pop()
            if h and not h.startswith("mailto:"):
                self.out.append(f" [{h}]")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)

    def text(self) -> str:
        lines = [" ".join(ln.split()) for ln in "".join(self.out).split("\n")]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def html_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    p.close()
    return p.text()


def _content(part) -> str:
    try:
        text = part.get_content()
        # No charset declared means ASCII, but 8-bit UTF-8 without one is common.
        if "\ufffd" not in text or part.get_param("charset"):
            return text
    except (LookupError, ValueError, AttributeError):
        pass
    raw = part.get_payload(decode=True) or b""
    return raw.decode("utf-8", errors="replace")


def _parse(path: str):
    with open(path, "rb") as f:
        return BytesParser(policy=email_policy).parse(f)


def parts(msg) -> list[tuple[int, object]]:
    """Leaf parts numbered depth-first from 1 (the whole message), as notmuch does."""
    return [(n, p) for n, p in enumerate(msg.walk(), 1) if not p.is_multipart()]


def read_file(path: str) -> tuple[str, str, list[dict]]:
    """(body text, "plain" | "html" | "none", attachments) of a message file."""
    msg = _parse(path)
    body_part, kind, text = None, "none", ""
    plain = msg.get_body(preferencelist=("plain",))
    if plain is not None and _content(plain).strip():
        body_part, kind, text = plain, "plain", _content(plain)
    else:
        html = msg.get_body(preferencelist=("html",))
        if html is not None:
            body_part, kind, text = html, "html", html_text(_content(html))
    atts = []
    for n, p in parts(msg):
        if p is body_part:
            continue
        name = p.get_filename()
        ctype = p.get_content_type()
        if name or p.get_content_disposition() == "attachment" or not ctype.startswith("text/"):
            atts.append({"part": n, "filename": name or "", "type": ctype,
                         "size": len(p.get_payload(decode=True) or b"")})
    return text, kind, atts


def _where(inst: Instance, msgid: str) -> list[Account]:
    found = [a for a in _open(inst) if _messages(a, idq(msgid))]
    if not found:
        closed_ = [a.handle for a in inst.accounts.values() if not seal.is_open(a)]
        raise MailkitError(f"id:{msgid}: in no open account"
                           + (f" (closed: {', '.join(closed_)})" if closed_ else ""))
    return found


def thread(inst: Instance, ref: str, max_body: int | None = None) -> dict:
    """The whole thread of a message, oldest first, each message labelled:
    new (pending, never shown), seen / done (in the ledger), ours (sent by
    us), context (before the cutoff, ignored, or a draft/trash). `pending`:
    the ids an `agent read` of it marks."""
    msgid = bare(ref)
    accts = _where(inst, msgid)
    a = accts[0]
    tid = index.run(a, "search", "--output=threads", "--exclude=false", idq(msgid)).split("\n")[0]
    tid = tid.removeprefix("thread:")
    msgs = sorted(_show(a, f"thread:{tid}", entire=True), key=lambda m: m.get("timestamp", 0))
    state = ledger(inst)
    ours_ids = set(_messages(a, f"thread:{tid} and ({ours(inst)})"))
    try:
        could = set(_messages(a, f"thread:{tid} and ({base(inst)}){_not_ignored(inst)}"))
    except MailkitError:
        could = set()  # no cutoff decided: reading still works, nothing is pending
    elsewhere = {b.handle: _has(b, [m["id"] for m in msgs]) for b in _open(inst) if b is not a}
    out, last_in = [], None
    for m in msgs:
        mid, h, st = m["id"], m.get("headers", {}), state.get(m["id"])
        if mid in ours_ids:
            label = "ours"
        elif mid in could:
            label = "done" if st and st["done"] else "seen" if st else "new"
            last_in = m
        else:
            label = "context"
            last_in = m if "draft" not in m.get("tags", []) else last_in
        files = m.get("filename") or []
        try:
            body, kind, atts = read_file(files[0]) if files else ("", "none", [])
        except OSError as e:
            body, kind, atts = f"(could not read the file: {e})", "none", []
        full = len(body)
        if max_body and full > max_body:
            body = body[:max_body] + (f"\n[… {full - max_body} more characters: "
                                      f"mailkit agent show --full id:{mid}]\n")
        out.append({"id": mid, "state": label,
                    "pending": label == "new" or (label == "seen" and inst.agent.pending == "undone"),
                    "accounts": [a.handle] + [k for k, v in elsewhere.items() if mid in v],
                    "date": _iso(m.get("timestamp", 0)), "from": h.get("From", ""),
                    "to": h.get("To", ""), "cc": h.get("Cc", ""), "subject": h.get("Subject", ""),
                    "tags": m.get("tags", []), "spam": "spam" in m.get("tags", []),
                    "body_from": kind, "body": body, "attachments": atts})
    ans = None
    if last_in is not None:
        sent = _sent_since(inst, last_in.get("timestamp", 0) + 1)
        ans = answered(sent, a.handle, tid, _addr(last_in.get("headers", {}).get("From", "")),
                       last_in.get("timestamp", 0))
    return {"ref": f"id:{msgid}", "account": a.handle, "accounts": [b.handle for b in accts],
            "thread": tid, "subject": " ".join((msgs[0].get("headers", {}).get("Subject", "")
                                                if msgs else "").split()),
            "answered": ans, "messages": out}


def new_ids(view: dict) -> list[str]:
    return [m["id"] for m in view["messages"] if m["state"] == "new"]


# ---------------------------------------------------------------- attachments

def attach(inst: Instance, ref: str, which: str, dest: str) -> tuple[Path, int]:
    if not inst.agent.extract:
        raise MailkitError(f"attachments are not extracted on this instance "
                           f"([agent] extract = true in {inst.config_path} allows it)")
    msgid = bare(ref)
    a = _where(inst, msgid)[0]
    files = [f for f in index.run(a, "search", "--output=files", "--exclude=false",
                                  idq(msgid)).split("\n") if f]
    msg = _parse(files[0])
    leaves = parts(msg)
    hit = [p for n, p in leaves if str(n) == which] or [p for _, p in leaves if p.get_filename() == which]
    if not hit:
        raise MailkitError(f"id:{msgid}: no part {which!r} (parts and names: mailkit agent show id:{msgid})")
    if len(hit) > 1:
        raise MailkitError(f"id:{msgid}: {len(hit)} parts named {which!r}: give the part number")
    data = hit[0].get_payload(decode=True) or b""
    path = Path(dest).expanduser()
    if path.is_dir():
        name = os.path.basename(hit[0].get_filename() or "") or f"part-{which}"
        path = path / name
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise MailkitError(f"{path} exists: not overwritten") from None
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path, len(data)


# ---------------------------------------------------------------- text output

def _size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _ans(ans: dict | None) -> str:
    if not ans:
        return "no reply of ours found after the last received message (open accounts)"
    where = "in this thread" if ans["how"] == "thread" else "to the same sender"
    return f"yes — {ans['date']} from {ans['account']}, {where}: id:{ans['id']}"


def render(view: dict) -> str:
    msgs = view["messages"]
    head = (f"━━━━ {view['subject'] or '(no subject)'}\n"
            f"thread {view['account']}:{view['thread']} · {len(msgs)} message(s)"
            + (f" · also in: {', '.join(view['accounts'][1:])}" if len(view["accounts"]) > 1 else "")
            + f"\nanswered: {_ans(view['answered'])}\n")
    out = [head]
    for n, m in enumerate(msgs, 1):
        flags = m["state"] + (" · pending" if m["pending"] and m["state"] != "new" else "") \
            + (" · spam" if m["spam"] else "")
        lines = [f"──── [{n}/{len(msgs)}] {flags} · {m['date']} · id:{m['id']}",
                 f"From: {m['from']}", f"To: {m['to']}"]
        if m["cc"]:
            lines.append(f"Cc: {m['cc']}")
        lines.append(f"Subject: {m['subject']}")
        if len(m["accounts"]) > 1:
            lines.append(f"Accounts: {', '.join(m['accounts'])}")
        for t in m["attachments"]:
            lines.append(f"Attachment: part {t['part']} · {t['filename'] or '(no name)'} · "
                         f"{t['type']} · {_size(t['size'])}")
        if m["body_from"] == "html":
            lines.append("(text from the HTML part)")
        lines += ["", m["body"].rstrip("\n") if m["body"] else "(no text)", ""]
        out.append("\n".join(lines))
    return "\n".join(out) + "\n"
