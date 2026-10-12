"""End to end on a plain (unsealed) store under /tmp, with real notmuch and fake
SMTP/IMAP servers on localhost: draft → review → approve → send → Sent copy.

The sealed path (gocryptfs + FUSE) is in test_seal.py, opt-in."""

import base64
import os
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from mailkit import cli

HAVE_NOTMUCH = shutil.which("notmuch") is not None


class FakeSMTP(socketserver.StreamRequestHandler):
    def handle(self):
        srv = self.server
        w = self.wfile.write
        w(b"220 fake ESMTP\r\n")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            cmd = line.decode().strip()
            up = cmd.upper()
            if up.startswith("EHLO"):
                w(b"250-fake\r\n250 AUTH PLAIN\r\n")
            elif up.startswith("AUTH PLAIN"):
                srv.auth.append(base64.b64decode(cmd.split()[2]).split(b"\0"))
                w(b"235 ok\r\n")
            elif up.startswith("MAIL FROM"):
                srv.mail_from.append(cmd)
                w(b"250 ok\r\n")
            elif up.startswith("RCPT TO"):
                srv.rcpt.append(cmd[8:].strip().strip("<>"))
                w(b"250 ok\r\n")
            elif up == "DATA":
                w(b"354 go\r\n")
                data = b""
                while not data.endswith(b"\r\n.\r\n"):
                    data += self.rfile.readline()
                srv.messages.append(data)
                w(b"250 queued\r\n")
            elif up == "QUIT":
                w(b"221 bye\r\n")
                return
            else:
                w(b"250 ok\r\n")


class FakeIMAP(socketserver.StreamRequestHandler):
    def handle(self):
        srv = self.server
        w = self.wfile.write
        w(b"* OK fake IMAP\r\n")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            tag, _, rest = line.decode().strip().partition(" ")
            cmd = rest.split(" ", 1)[0].upper()
            if cmd == "CAPABILITY":
                w(b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n" + f"{tag} OK done\r\n".encode())
            elif cmd == "LOGIN":
                srv.logins.append(rest)
                w(f"{tag} OK logged in\r\n".encode())
            elif cmd == "APPEND":
                size = int(rest[rest.rindex("{") + 1:rest.rindex("}")])
                w(b"+ go\r\n")
                data = self.rfile.read(size)
                self.rfile.readline()
                srv.appends.append((re.match(r"APPEND (\"[^\"]*\"|\S+)", rest).group(1), data))
                w(f"{tag} OK appended\r\n".encode())
            elif cmd == "LOGOUT":
                w(b"* BYE\r\n" + f"{tag} OK done\r\n".encode())
                return
            else:
                w(f"{tag} OK done\r\n".encode())


def serve(handler, **lists):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    for k, v in lists.items():
        setattr(srv, k, v)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run(*argv, stdin=None):
    """mailkit in a subprocess: what an agent or a script actually does."""
    r = subprocess.run([sys.executable, "-m", "mailkit", *argv], input=stdin,
                       capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


@unittest.skipUnless(HAVE_NOTMUCH, "notmuch not on PATH")
class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mailkit-flow-"))
        self.smtp = serve(FakeSMTP, auth=[], mail_from=[], rcpt=[], messages=[])
        self.imap = serve(FakeIMAP, logins=[], appends=[])
        cfg = self.tmp / "config.toml"
        cfg.write_text(f"""
[mailkit]
instance = "flow"
root = "{self.tmp}/Mail"
send_policy = "approved"

[secrets]
backend = "command"
command = "printf pw-{{handle}}-{{kind}}"

[accounts.admin]
address = "admin@example.org"
name = "Admin Café"
seal = false
imap = {{ host = "127.0.0.1", port = {self.imap.server_address[1]}, tls = "none" }}
smtp = {{ host = "127.0.0.1", port = {self.smtp.server_address[1]}, tls = "none" }}
folders = {{ inbox = "INBOX", sent = "INBOX.Sent Items" }}
""")
        self.env_backup = dict(os.environ)
        os.environ.update({
            "MAILKIT_CONFIG": str(cfg),
            "XDG_CONFIG_HOME": str(self.tmp / "xdg"),
            "XDG_RUNTIME_DIR": str(self.tmp / "run"),
            "PYTHONPATH": str(Path(cli.__file__).parents[1]),
        })
        os.environ.pop("MAILKIT_BIN", None)
        (self.tmp / "run").mkdir()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env_backup)
        for srv in (self.smtp, self.imap):
            srv.shutdown()
            srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_draft_review_approve_send(self):
        self.assertEqual(run("init", "admin")[0], 0)
        att = self.tmp / "doc.pdf"
        att.write_bytes(b"%PDF fake")
        draft = (f"To: Client <c@example.com>\nBcc: copy@example.com\nSubject: Invoice for café\n\n"
                 f"Hello.\n<#part type=application/pdf filename={att}><#/part>\n")
        rc, out, err = run("draft", "new", "admin", stdin=draft)
        self.assertEqual(rc, 0, err)
        msgid = out.split("id:")[1].split()[0]
        self.assertIn("review", out)

        rc, out, _ = run("draft", "list", "--json")
        import json
        listed = json.loads(out)
        self.assertEqual([d["id"] for d in listed], [msgid])
        self.assertIn("draft", listed[0]["tags"])

        # send_policy = approved: an unapproved draft does not leave.
        rc, _, err = run("send", msgid)
        self.assertEqual(rc, 1)
        self.assertIn("not", err)
        self.assertEqual(self.smtp.messages, [])

        self.assertEqual(run("draft", "approve", msgid)[0], 0)
        rc, out, err = run("send", "--approved")
        self.assertEqual(rc, 0, err)
        self.assertIn("sent", out)

        # SMTP got it, with the Bcc in the envelope but not in the message.
        self.assertEqual(self.smtp.auth, [[b"", b"admin@example.org", b"pw-admin-password"]])
        self.assertEqual(sorted(self.smtp.rcpt), ["c@example.com", "copy@example.com"])
        sent = self.smtp.messages[0]
        self.assertNotIn(b"Bcc:", sent)
        self.assertIn(msgid.encode(), sent)
        # The copy went to Sent, Bcc kept for the sender.
        box, data = self.imap.appends[0]
        self.assertEqual(box, '"INBOX.Sent Items"')
        self.assertIn(b"Bcc: copy@example.com", data)
        # Outbox empty, the sent record indexed and tagged.
        self.assertEqual(json.loads(run("draft", "list", "--json")[1]), [])
        rc, out, _ = run("nm", "admin", "search", "--output=tags", f"id:{msgid}")
        self.assertEqual(set(out.split()) - {"attachment"}, {"sent"})

    def test_wrong_from_is_refused(self):
        run("init", "admin")
        rc, _, err = run("draft", "new", "admin", stdin="From: x@evil.org\nTo: a@b.c\nSubject: s\n\nhi\n")
        self.assertEqual(rc, 1)
        self.assertIn("wrong account", err)

    def test_help_touches_nothing(self):
        os.environ["MAILKIT_CONFIG"] = str(self.tmp / "missing.toml")
        for argv in (["--help"], ["draft", "--help"], ["send", "--help"], ["__commands"]):
            rc, out, _ = run(*argv)
            self.assertEqual(rc, 0, argv)
            self.assertTrue(out)




if __name__ == "__main__":
    unittest.main()
