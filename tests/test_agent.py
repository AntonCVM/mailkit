"""`mailkit agent` end to end on two plain stores under /tmp, real notmuch:
cutoff, whole threads, marking on read only, dedup across accounts, answered,
spam, ignore, undo, done, the ledger surviving an index rebuild, attachments."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from mailkit import cli

from .test_flow import HAVE_NOTMUCH, run

PDF = "JVBERiBmYWtl"  # base64 of "%PDF fake"


def msg(mid, frm, to, day, subject, body="hola\n", refs=None, extra=""):
    h = f"Message-ID: <{mid}>\nFrom: {frm}\nTo: {to}\nDate: {day:02d} Nov 2026 10:00:00 +0000\nSubject: {subject}\n"
    if refs:
        h += f"In-Reply-To: <{refs[-1]}>\nReferences: {' '.join(f'<{r}>' for r in refs)}\n"
    return h + extra + "\n" + body


@unittest.skipUnless(HAVE_NOTMUCH, "notmuch not on PATH")
class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mailkit-agent-"))
        self.cfg = self.tmp / "config.toml"
        self.write_cfg('since = "2026-11-01"\npending = "unseen"\nignore = ["from:wordpress@blog.org"]\n'
                       'extract = true\n')
        self.env_backup = dict(os.environ)
        os.environ.update({
            "MAILKIT_CONFIG": str(self.cfg),
            "XDG_CONFIG_HOME": str(self.tmp / "xdg"),
            "XDG_RUNTIME_DIR": str(self.tmp / "run"),
            "PYTHONPATH": str(Path(cli.__file__).parents[1]),
        })
        (self.tmp / "run").mkdir()
        for h in ("admin", "anton"):
            self.assertEqual(run("init", h)[0], 0)
        A = "admin@example.org"
        self.put("admin", "INBOX", "old", msg("old@x", "Viejo <v@x.org>", A, 1, "Antiguo").replace("01 Nov", "20 Oct"))
        # A thread: their question, our reply from Sent, their follow-up (pending).
        self.put("admin", "INBOX", "q1", msg("q1@x", "Juan <juan@x.org>", A, 2, "Expediente"))
        self.put("admin", "Sent", "r1", msg("r1@us", A, "juan@x.org", 3, "Re: Expediente", "visto\n", ["q1@x"]))
        self.put("admin", "INBOX", "q2", msg("q2@x", "Juan <juan@x.org>", A, 4, "Re: Expediente", "gracias\n",
                                           ["q1@x", "r1@us"]))
        # Answered from another account, in another thread, to the same sender.
        self.put("admin", "INBOX", "ana", msg("ana@x", "Ana <ana@y.org>", A, 5, "Plazo"))
        self.put("anton", "Sent", "ra", msg("ra@us", "anton@example.org", "ana@y.org", 6, "Plazo, contestado"))
        # The same Message-ID in both accounts (a forward/Cc).
        dup = msg("dup@x", "Red <avisos@red.es>", f"{A}, anton@example.org", 7, "Notificación")
        self.put("admin", "INBOX", "dup", dup)
        self.put("anton", "INBOX", "dup", dup)
        # Answered from another account as a reply (In-Reply-To): confirmed.
        self.put("admin", "INBOX", "kd", msg("kd@x", "Red <avisos@red.es>", A, 7, "Requerimiento KD/0001"))
        self.put("anton", "Sent", "rkd", msg("rkd@us", "anton@example.org", "avisos@red.es", 8,
                                             "Re: Requerimiento KD/0001", "adjunto\n", ["kd@x"]))
        # A campaign of ours to the sender later on is no answer at all.
        self.put("admin", "INBOX", "pepe", msg("pepe@x", "Pepe <pepe@z.org>", A, 7, "Duda"))
        self.put("anton", "Sent", "news", msg("news@us", "anton@example.org", "pepe@z.org", 8, "Novedades",
                                              extra="List-Unsubscribe: <mailto:baja@example.org>\n"))
        self.put("admin", "Spam", "bounce", msg("bounce@x", "MAILER-DAEMON <md@x.org>", A, 8, "Undelivered"))
        self.put("admin", "INBOX", "wp", msg("wp@x", "wordpress@blog.org", A, 9, "Comentario pendiente"))
        html = ("Content-Type: multipart/mixed; boundary=B\n", "--B\nContent-Type: text/html\n\n"
                "<p>Su <b>factura</b> <a href='https://r.example/f'>aquí</a></p><script>x()</script>\n"
                "--B\nContent-Type: application/pdf; name=f.pdf\nContent-Disposition: attachment; filename=f.pdf\n"
                f"Content-Transfer-Encoding: base64\n\n{PDF}\n--B--\n")
        self.put("admin", "INBOX", "html", msg("html@x", "Banco <b@bank.es>", A, 10, "Factura", html[1], extra=html[0]))
        for h in ("admin", "anton"):
            self.assertEqual(run("index", h)[0], 0)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env_backup)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_cfg(self, agent_table):
        self.cfg.write_text(f"""
[mailkit]
instance = "ag"
root = "{self.tmp}/Mail"

[secrets]
backend = "command"
command = "printf pw"

[defaults]
seal = false
imap = {{ host = "127.0.0.1", port = 1, tls = "none" }}
smtp = {{ host = "127.0.0.1", port = 1, tls = "none" }}
folders = {{ inbox = "INBOX", sent = "INBOX.Sent", spam = "INBOX.Spam", trash = "INBOX.Trash" }}

[accounts.admin]
address = "admin@example.org"

[accounts.anton]
address = "anton@example.org"

[agent]
{agent_table}""")

    def put(self, acct, folder, name, text):
        box = self.tmp / "Mail" / acct / "mail" / folder
        for sub in ("cur", "new", "tmp"):
            (box / sub).mkdir(parents=True, exist_ok=True)
        (box / "cur" / f"{name}:2,S").write_text(text)

    def pending(self, *extra):
        rc, out, err = run("agent", "pending", "--json", *extra)
        self.assertEqual(rc, 0, err)
        return json.loads(out)

    def pending_ids(self, *extra):
        return [p["id"] for t in self.pending(*extra)["threads"] for p in t["pending"]]

    def test_the_daily_sweep(self):
        doc = self.pending()
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["since"], "2026-11-01")
        # Oldest first; before the cutoff, ours, and ignored noise are not pending.
        self.assertEqual(self.pending_ids(),
                         ["q1@x", "q2@x", "ana@x", "dup@x", "kd@x", "pepe@x", "bounce@x", "html@x"])
        self.assertEqual(doc["ignored"], 1)
        by = {t["ref"]: t for t in doc["threads"]}
        # q1 and q2 are one thread; the reply of ours (3 Nov) is older than q2: not answered.
        self.assertEqual(by["id:q1@x"]["messages"], 3)
        self.assertIsNone(by["id:q1@x"]["answered"])
        # Ana: we only wrote to her later, from the other account — not confirmed.
        self.assertEqual(by["id:ana@x"]["answered"]["how"], "sender")
        self.assertFalse(by["id:ana@x"]["answered"]["confirmed"])
        self.assertEqual(by["id:ana@x"]["answered"]["account"], "anton")
        # KD: a reply by its headers, from the other account — confirmed.
        self.assertEqual((by["id:kd@x"]["answered"]["how"], by["id:kd@x"]["answered"]["confirmed"]),
                         ("reply", True))
        self.assertIsNone(by["id:pepe@x"]["answered"])  # the campaign does not count
        text = run("agent", "pending")[1]
        self.assertIn("[answered? 2026-11-06: wrote to sender — confirm]", text)
        self.assertIn("[answered 2026-11-08: reply]", text)
        # One Message-ID, two accounts: listed once.
        self.assertEqual(by["id:dup@x"]["accounts"], ["admin", "anton"])
        self.assertTrue(by["id:bounce@x"]["spam"])
        self.assertEqual(self.pending_ids("--spam", "exclude").count("bounce@x"), 0)
        self.assertEqual(self.pending_ids("--spam", "only"), ["bounce@x"])
        # Listing marks nothing, and `unread` is never touched by any of this.
        self.assertEqual(len(self.pending_ids()), 8)

        # show: the whole thread, ours included, labelled; marks nothing.
        rc, out, err = run("agent", "show", "--json", "id:q2@x")
        self.assertEqual(rc, 0, err)
        t = json.loads(out)["threads"][0]
        self.assertEqual([(m["id"], m["state"]) for m in t["messages"]],
                         [("q1@x", "new"), ("r1@us", "ours"), ("q2@x", "new")])
        self.assertEqual(len(self.pending_ids()), 8)

        # next: the oldest thread, then marked seen — only what was new in it.
        rc, out, err = run("agent", "next")
        self.assertEqual(rc, 0, err)
        self.assertIn("Expediente", out)
        self.assertIn("visto", out)  # our reply, from Sent
        self.assertIn("marked seen: 2", err)
        self.assertIn("==== END: 1 thread(s), 2 message(s) marked seen · pending left: 6", out)
        self.assertEqual(self.pending_ids(), ["ana@x", "dup@x", "kd@x", "pepe@x", "bounce@x", "html@x"])
        # Read again: the same messages are now context-for-the-record (seen), nothing new.
        rc, out, err = run("agent", "read", "id:q1@x")
        self.assertIn("seen", out)
        self.assertIn("nothing marked", err)

        # The duplicate is marked in both accounts' ledgers.
        rc, _, err = run("agent", "read", "id:dup@x")
        self.assertEqual(rc, 0, err)
        self.assertIn("admin: 1", err)
        self.assertIn("anton: 1", err)
        out = run("nm", "anton", "search", "--output=tags", "id:dup@x")[1].split()
        self.assertIn("agent-seen", out)

        # HTML-only body as text, scripts out, link target kept; the attachment listed and extracted.
        rc, out, err = run("agent", "show", "id:html@x")
        self.assertIn("Su factura aquí [https://r.example/f]", out)
        self.assertNotIn("x()", out)
        self.assertIn("Attachment: part 3 · f.pdf · application/pdf · 9 B", out)
        dest = self.tmp / "out"
        dest.mkdir()
        rc, out, err = run("agent", "attach", "id:html@x", "f.pdf", str(dest))
        self.assertEqual(rc, 0, err)
        self.assertEqual((dest / "f.pdf").read_bytes(), b"%PDF fake")
        self.assertEqual(run("agent", "attach", "id:html@x", "3", str(dest))[0], 1)  # not overwritten

        # Undo the last batch: dup is pending again.
        rc, out, err = run("agent", "unsee", "--last")
        self.assertEqual(rc, 0, err)
        self.assertIn("dup@x", self.pending_ids())
        self.assertNotIn("q1@x", self.pending_ids())

        # The ledger survives a lost index: rebuild, restore, same state.
        shutil.rmtree(self.tmp / "Mail" / "admin" / ".notmuch")
        run("index", "admin")
        self.assertNotIn("q1@x", self.pending_ids())  # pending is computed from the ledger
        rc, out, err = run("agent", "restore", "admin")
        self.assertEqual(rc, 0, err)
        self.assertIn("ledger 2 marked", out)
        self.assertIn("agent-seen", run("nm", "admin", "search", "--output=tags", "id:q2@x")[1])
        rc, out, _ = run("agent", "log", "--json")
        self.assertEqual({e["op"] for e in json.loads(out)}, {"seen", "unsee"})

    def test_next_stops_at_the_budget_and_marks_only_what_it_printed(self):
        rc, out, err = run("agent", "next", "-n", "5", "--budget", "500")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.count("━━━━ "), 1)  # the first thread always, then over budget
        self.assertIn("pending left: 6", out)
        self.assertEqual(self.pending_ids()[:2], ["ana@x", "dup@x"])
        rc, out, _ = run("agent", "next", "-n", "5", "--budget", "0", "--json")
        doc = json.loads(out)
        self.assertEqual((len(doc["threads"]), doc["left"]), (5, 1))

    def test_pending_undone_and_done(self):
        self.write_cfg('since = "2026-11-01"\n')  # undone is the default
        rc, _, err = run("agent", "done", "id:q1@x")
        self.assertEqual(rc, 1)
        self.assertIn("never shown", err)
        run("agent", "read", "id:q1@x")
        # Shown is not enough: still pending, labelled seen.
        t = next(t for t in self.pending()["threads"] if t["ref"] == "id:q1@x")
        self.assertEqual([p["state"] for p in t["pending"]], ["seen", "seen"])
        rc, out, err = run("agent", "done", "id:q1@x")
        self.assertEqual(rc, 0, err)
        self.assertEqual([i for i in self.pending_ids() if i.startswith("q")], ["q2@x"])
        run("agent", "done", "--thread", "id:q1@x")
        self.assertNotIn("q2@x", self.pending_ids())
        # next serves only threads with new mail, not the shown-but-open ones again.
        run("agent", "read", "id:ana@x")
        rc, out, _ = run("agent", "next", "--json")
        self.assertEqual(json.loads(out)["threads"][0]["ref"], "id:dup@x")

    def test_no_cutoff_is_refused_and_extract_is_opt_in(self):
        self.write_cfg("")
        rc, _, err = run("agent", "pending")
        self.assertEqual(rc, 1)
        self.assertIn("since", err)
        self.assertEqual(run("agent", "show", "id:q1@x")[0], 0)  # reading still works
        rc, _, err = run("agent", "attach", "id:html@x", "3", str(self.tmp))
        self.assertEqual(rc, 1)
        self.assertIn("extract", err)

    def test_help_touches_nothing(self):
        os.environ["MAILKIT_CONFIG"] = str(self.tmp / "missing.toml")
        for argv in (["agent", "--help"], ["agent", "next", "--help"]):
            rc, out, _ = run(*argv)
            self.assertEqual(rc, 0, argv)
            self.assertIn("agent", out)


if __name__ == "__main__":
    unittest.main()
