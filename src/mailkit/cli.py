"""mailkit — terminal mail with a sealed local mirror per account.

One command, subcommands below; `mailkit <cmd> --help` on each.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys

from . import aerc, index, mirror, outbox, seal, secrets, send
from .config import ConfigError, Instance, load
from .util import MailkitError, log

DESCRIPTION = """\
Terminal mail with a local mirror per account (mbsync + notmuch), each mirror
sealed on its own (gocryptfs, own passphrase, own index), credentials from the
desktop keyring (KeePassXC over the Secret Service): an unlocked database is the
switch that lets sync, send and mounts happen; a locked one stops them all.

Accounts come from one TOML file: $MAILKIT_CONFIG, else
~/.config/mailkit/config.toml.
"""

EPILOG = """\
typical session:
  mailkit open all          mount the mirrors (passphrases from the keyring)
  mailkit sync              mbsync + notmuch new + tag rules, on what is open
  mailkit nm <acct> search tag:unread
  mailkit draft new <acct> < message.mml
  mailkit send <id>         or: mailkit draft approve <id>; mailkit send --approved all
  mailkit close             unmount, verified
"""


def _inst() -> Instance:
    return load()


# ---------------------------------------------------------------- verbs

def cmd_live(args) -> int:
    inst = _inst()
    accts = inst.select(args.account or "all")
    return aerc.run(inst, [a.handle for a in accts])


def cmd_archive(args) -> int:
    inst = _inst()
    accts = inst.select(args.account)
    opened = []
    try:
        for a in accts:
            if seal.open_(a):
                opened.append(a)
        for a in accts:
            try:
                mirror.sync(a)
            except MailkitError as e:
                log(f"mailkit: {e} — opening what is there")
        return aerc.run(inst, [f"archive-{a.handle}" for a in accts])
    finally:
        # Close only what this command opened: an `open` done by hand before
        # is the user's session, not ours to end.
        for a in opened:
            seal.close(a)


def cmd_open(args) -> int:
    for a in _inst().select(args.account):
        seal.open_(a)
    return 0


def cmd_close(args) -> int:
    ok = True
    for a in _inst().select(args.account or "all"):
        ok = seal.close(a) and ok
    return 0 if ok else 1


def cmd_status(args) -> int:
    inst = _inst()
    rows = []
    for a in inst.accounts.values():
        st = seal.state(a)
        drafts = len(outbox.entries(a)) if st in (seal.OPEN, seal.PLAIN) else None
        size = seal.du(a.cipher_dir if a.seal else a.mountpoint) if st != seal.UNINIT else "-"
        rows.append({"account": a.handle, "address": a.address, "state": st, "size": size,
                     "drafts": drafts, "keyring": a.keyring, "store": str(a.mountpoint)})
    if args.json:
        print(json.dumps({"instance": inst.name, "config": str(inst.config_path),
                          "send_policy": inst.send_policy, "accounts": rows}, indent=2))
        return 0
    print(f"instance {inst.name} · {inst.config_path} · send_policy={inst.send_policy}")
    print(f"{'ACCOUNT':<16} {'ADDRESS':<36} {'STATE':<7} {'SIZE':<6} {'DRAFTS':<6} KEYRING")
    for r in rows:
        d = "-" if r["drafts"] is None else str(r["drafts"])
        print(f"{r['account']:<16} {r['address']:<36} {r['state']:<7} {r['size']:<6} {d:<6} {r['keyring'] or '-'}")
    return 0


def cmd_sync(args) -> int:
    inst = _inst()
    if args.account:
        accts = inst.select(args.account)
        for a in accts:
            seal.open_(a)  # naming an account is consent to open it; stays open
    else:
        accts = [a for a in inst.accounts.values() if seal.is_open(a)]
        if not accts:
            raise MailkitError("nothing is open — run: mailkit open <account|all>, or name one")
    rc = 0
    for a in accts:
        print(f"== {a.handle}")
        try:
            mirror.sync(a)
        except MailkitError as e:
            log(f"mailkit: {e}")
            rc = 1
    return rc


def cmd_index(args) -> int:
    inst = _inst()
    accts = inst.select(args.account) if args.account else [a for a in inst.accounts.values() if seal.is_open(a)]
    for a in accts:
        seal.require_open(a)
        index.update(a)
        print(f"{a.handle}: indexed")
    return 0


def cmd_init(args) -> int:
    for a in _inst().select(args.account):
        seal.init(a)
    print("Back up the keyring: without its 'kind cipher' item a sealed store is unreadable.")
    return 0


def cmd_secret(args) -> int:
    a = _inst().account(args.account)
    if args.action == "get":
        print(secrets.get(a, args.kind))
    else:
        secrets.store(a, args.kind)
        print(f"stored 'mail {a.address} kind {args.kind}' in {a.keyring}")
    return 0


def cmd_keyrings(args) -> int:
    for _, label, locked in secrets.collections():
        print(f"{label:<28} {'locked' if locked else 'unlocked'}")
    return 0


def cmd_nm(args) -> int:
    import os

    a = _inst().account(args.account)
    seal.require_open(a)
    index.ensure(a)
    os.execvpe("notmuch", ["notmuch", *args.args], index.env(a))
    return 0  # not reached


def _print_entry(e: outbox.Entry, verb: str) -> None:
    print(f"{verb} {e.account.handle} id:{e.msgid}  [{' '.join(e.tags)}]  {e.path}")


def cmd_draft(args) -> int:
    inst = _inst()
    act = args.action
    if act == "new":
        a = inst.account(args.account)
        raw = sys.stdin.read() if args.file in (None, "-") else open(args.file, encoding="utf-8").read()
        _print_entry(outbox.new(a, raw), "draft")
    elif act == "reply":
        _print_entry(outbox.reply(inst.account(args.account), args.query, args.all), "draft")
    elif act == "forward":
        _print_entry(outbox.forward(inst.account(args.account), args.query), "draft")
    elif act == "list":
        accts = [a for a in inst.select(args.account or "all") if seal.is_open(a)]
        rows = [e for a in accts for e in outbox.entries(a)]
        if args.json:
            print(json.dumps([{"account": e.account.handle, "id": e.msgid, "tags": e.tags,
                               "path": str(e.path), "to": e.headers.get("to", ""),
                               "subject": e.headers.get("subject", "")} for e in rows], indent=2))
        else:
            for e in rows:
                flag = "approved" if "approved" in e.tags else "review" if "review" in e.tags else "-"
                print(f"{e.account.handle:<16} {flag:<9} {e.headers.get('to', '')[:34]:<34} "
                      f"{e.headers.get('subject', '')[:50]}  id:{e.msgid}")
            closed = [a.handle for a in inst.select(args.account or "all") if not seal.is_open(a)]
            if closed:
                log(f"(closed, not listed: {', '.join(closed)})")
    elif act in ("show", "path", "edit"):
        e = outbox.find(inst, args.ref)
        if act == "path":
            print(e.path)
        elif act == "edit":
            outbox.edit(e)
        else:
            print(f"# {e.account.handle} id:{e.msgid} [{' '.join(e.tags)}]")
            sys.stdout.write(e.path.read_text(encoding="utf-8"))
    elif act in ("approve", "unapprove", "discard"):
        for ref in args.refs:
            e = outbox.find(inst, ref)
            if act == "approve":
                outbox.tag(e, "+approved", "-review")
            elif act == "unapprove":
                outbox.tag(e, "-approved", "+review")
            else:
                outbox.discard(e)
            print(f"{act}d {e.account.handle} id:{e.msgid}" if act != "discard"
                  else f"discarded {e.account.handle} id:{e.msgid}")
    return 0


def cmd_send(args) -> int:
    inst = _inst()
    if args.approved is not None:
        if args.refs:
            raise MailkitError("send: either ids or --approved, not both")
        accts = [a for a in inst.select(args.approved) if seal.is_open(a)]
        entries = [e for a in accts for e in outbox.entries(a) if "approved" in e.tags]
        if not entries:
            print("nothing approved to send")
            return 0
    else:
        if not args.refs:
            raise MailkitError("send: which draft? (ids or paths, or --approved <account|all>)")
        entries = [outbox.find(inst, r) for r in args.refs]
        if inst.send_policy == "approved":
            pending = [e.msgid for e in entries if "approved" not in e.tags]
            if pending:
                raise MailkitError("send_policy = approved, and these are not: "
                                   + ", ".join(pending) + " (mailkit draft approve <id>)")
    return send.send_many(entries, dry_run=args.dry_run)


def cmd_config(args) -> int:
    inst = _inst()
    print(f"config       {inst.config_path}")
    print(f"instance     {inst.name}")
    print(f"root         {inst.root}")
    print(f"send_policy  {inst.send_policy}")
    print(f"secrets      {inst.secret_backend}" + (f" ({inst.secret_command})" if inst.secret_command else ""))
    for a in inst.accounts.values():
        print(f"\n[{a.handle}] {a.from_header}")
        print(f"  imap {a.imap.host}:{a.imap.port} {a.imap.tls} · smtp {a.smtp.host}:{a.smtp.port} {a.smtp.tls}")
        print(f"  store {a.mountpoint} ({'sealed: ' + str(a.cipher_dir) if a.seal else 'plain'})")
        print(f"  notmuch profile {a.profile} · keyring {a.keyring or '-'}")
        print(f"  folders {a.folders} · sent_copy={a.sent_copy} · expunge={a.expunge}")
    return 0


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mailkit", description=DESCRIPTION, epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    def add(name, func, help_, desc=None):
        sp = sub.add_parser(name, help=help_, description=desc or help_,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
        sp.set_defaults(func=func)
        return sp

    s = add("live", cmd_live, "aerc over IMAP, live — no mirror involved (the default with no command)")
    s.add_argument("account", nargs="?", help="account handle or 'all' (default: all)")

    s = add("archive", cmd_archive, "open + sync mirror(s), aerc on their notmuch index; closes on exit what it opened")
    s.add_argument("account", help="account handle or 'all'")

    s = add("open", cmd_open, "mount sealed store(s); passphrase from the keyring")
    s.add_argument("account", help="account handle or 'all'")

    s = add("close", cmd_close, "unmount sealed store(s), verified; exit 1 if one stays mounted")
    s.add_argument("account", nargs="?", help="account handle or 'all' (default: all)")

    s = add("status", cmd_status, "each account: open/closed/uninit/plain, size, drafts, keyring")
    s.add_argument("--json", action="store_true", help="machine-readable output")

    s = add("sync", cmd_sync, "mbsync + notmuch new + tag rules",
            "mbsync (read-only pull) + notmuch new + tag rules. A named account is opened\n"
            "first and left open; with no name, every account already open.")
    s.add_argument("account", nargs="?", help="account handle or 'all' (default: the open ones)")

    s = add("index", cmd_index, "notmuch new + tag rules, no network",
            "Index what is in the store and apply the tag rules, without syncing:\n"
            "after files were added or moved by hand. `sync` already does this.")
    s.add_argument("account", nargs="?", help="account handle or 'all' (default: the open ones)")

    s = add("init", cmd_init, "create the store(s): container + generated passphrase in the keyring")
    s.add_argument("account", help="account handle or 'all'")

    s = add("secret", cmd_secret, "read or store a credential in the account's keyring",
            "get: print the secret (what mbsync, gocryptfs and aerc call).\n"
            "set: store it in the account's own KeePassXC database (prompts for the value).\n"
            "kind: password (the IMAP/SMTP password, default) | cipher (the store passphrase).")
    s.add_argument("action", choices=["get", "set"])
    s.add_argument("account", help="account handle")
    s.add_argument("kind", nargs="?", default="password", choices=secrets.KINDS)

    add("keyrings", cmd_keyrings, "list the Secret Service collections (= open KeePassXC databases)")

    s = add("nm", cmd_nm, "run notmuch on one account's index",
            "Runs notmuch with the account's profile (its index, inside its store):\n"
            "  mailkit nm <account> search tag:unread\n"
            "  mailkit nm <account> show --format=json id:<msgid>")
    s.add_argument("account", help="account handle")
    s.add_argument("args", nargs=argparse.REMAINDER, help="notmuch arguments")

    s = add("draft", cmd_draft, "write, list, review drafts (the outbox)",
            "Drafts are MML files in the account's outbox, indexed by notmuch and tagged\n"
            "draft + review. Ids are Message-IDs (with or without id:), or a file path.\n"
            "MML: headers, blank line, body; attachments as\n"
            "  <#part type=application/pdf filename=/abs/path.pdf><#/part>")
    dsub = s.add_subparsers(dest="action", metavar="<action>", required=True)

    def dadd(name, help_):
        return dsub.add_parser(name, help=help_, description=help_)

    d = dadd("new", "new draft from a file or stdin (From and Message-ID filled in)")
    d.add_argument("account", help="account handle")
    d.add_argument("file", nargs="?", help="MML file, or - / nothing for stdin")
    d = dadd("reply", "draft a reply to the message matching a notmuch query")
    d.add_argument("account", help="account handle")
    d.add_argument("query", help="notmuch query, e.g. id:<msgid>")
    d.add_argument("--all", action="store_true", help="reply to all")
    d = dadd("forward", "draft a forward (quoted + original attached) of the first match")
    d.add_argument("account", help="account handle")
    d.add_argument("query", help="notmuch query")
    d = dadd("list", "drafts of the open accounts")
    d.add_argument("account", nargs="?", help="account handle or 'all' (default: all)")
    d.add_argument("--json", action="store_true", help="machine-readable output")
    for name, help_ in (("show", "print a draft"), ("path", "print a draft's file path"),
                        ("edit", "open a draft in $VISUAL/$EDITOR, reindex after")):
        d = dadd(name, help_)
        d.add_argument("ref", help="Message-ID or path")
    for name, help_ in (("approve", "mark draft(s) approved for `send --approved`"),
                        ("unapprove", "back to review"), ("discard", "delete draft(s)")):
        d = dadd(name, help_)
        d.add_argument("refs", nargs="+", help="Message-IDs or paths")

    s = add("send", cmd_send, "send draft(s): SMTP, copy to Sent, out of the outbox",
            "Sends the named drafts, or every approved one. All are checked first\n"
            "(placeholders, attachments, From); nothing leaves if one is unsendable.\n"
            "With send_policy = approved, a named draft must be approved too.")
    s.add_argument("refs", nargs="*", help="Message-IDs or paths")
    s.add_argument("--approved", metavar="ACCOUNT", nargs="?", const="all",
                   help="send every approved draft of ACCOUNT (default: all)")
    s.add_argument("--dry-run", action="store_true", help="print the compiled messages, send nothing")

    add("config", cmd_config, "print the resolved config")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    p = build_parser()
    # Completion and man-page helpers; answer before touching anything.
    if argv[:1] == ["__commands"]:
        print("\n".join(sorted(n for n in p._subparsers._group_actions[0].choices)))  # noqa: SLF001
        return 0
    if argv[:1] == ["__accounts"]:
        try:
            print("\n".join(load().accounts))
        except ConfigError:
            pass
        return 0
    args = p.parse_args(argv)
    # SIGTERM/SIGHUP unwind like Ctrl-C, so `archive` still closes what it opened.
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        if args.cmd is None:
            args.account = None
            return cmd_live(args)
        return args.func(args) or 0
    except (MailkitError, ConfigError) as e:
        print(f"mailkit: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
