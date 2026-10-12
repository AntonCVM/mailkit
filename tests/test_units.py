"""Pure units: config, MML, folder mapping, generated configs. No I/O beyond /tmp."""

import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from mailkit import aerc, index, mirror, mml
from mailkit.config import ConfigError, load
from mailkit.util import MailkitError

BASE = """
[mailkit]
instance = "t"
root = "{root}"

[defaults]
keyring = "kr"
imap = {{ host = "imap.example.org", port = 993, tls = "tls" }}
smtp = {{ host = "smtp.example.org", port = 587, tls = "starttls" }}
[defaults.folders]
inbox = "INBOX"
sent = "INBOX.Sent Items"
trash = "INBOX.Deleted Items"
spam = "INBOX.Spam"

[accounts.admin]
address = "admin@example.org"
name = "Admin"

[accounts.other]
address = "other@example.org"
smtp = {{ port = 465, tls = "tls" }}
seal = false
"""


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mailkit-test-"))

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self, text=BASE):
        p = self.tmp / "config.toml"
        p.write_text(text.format(root=self.tmp / "Mail"))
        return load(p)


class ConfigTest(Tmp):
    def test_defaults_merge_one_level(self):
        inst = self.cfg()
        other = inst.account("other")
        self.assertEqual(other.smtp.host, "smtp.example.org")  # kept from defaults
        self.assertEqual((other.smtp.port, other.smtp.tls), (465, "tls"))  # overridden
        self.assertEqual(other.folders.sent, "INBOX.Sent Items")
        self.assertFalse(other.seal)
        self.assertEqual(other.name, "other@example.org")

    def test_paths_and_profile(self):
        a = self.cfg().account("admin")
        self.assertEqual(a.mountpoint, self.tmp / "Mail" / "admin")
        self.assertEqual(a.cipher_dir, self.tmp / "Mail" / ".cipher" / "admin")
        self.assertEqual(a.profile, "mailkit.t.admin")

    def test_from_header_readable(self):
        inst = self.cfg(BASE.replace('name = "Admin"', 'name = "Administración Ejemplo, S.L."'))
        self.assertEqual(inst.account("admin").from_header,
                         '"Administración Ejemplo, S.L." <admin@example.org>')
        self.assertEqual(inst.account("other").from_header, "other@example.org")

    def test_select(self):
        inst = self.cfg()
        self.assertEqual([a.handle for a in inst.select("all")], ["admin", "other"])
        with self.assertRaises(ConfigError):
            inst.select(None)
        with self.assertRaises(ConfigError):
            inst.select("nope")

    def test_rejects(self):
        for bad, needle in [
            (BASE.replace('tls = "starttls"', 'tls = "none"'), "localhost"),
            (BASE.replace("[accounts.admin]", "[accounts.Admin_1]"), "lowercase"),
            (BASE.replace('keyring = "kr"', ""), "keyring"),
            (BASE.replace('instance = "t"', 'instance = "t"\nsend_policy = "yolo"'), "send_policy"),
        ]:
            with self.subTest(needle=needle), self.assertRaises(ConfigError) as cm:
                self.cfg(bad)
            self.assertIn(needle, str(cm.exception))


class MmlTest(Tmp):
    def test_attachments_html_and_escape(self):
        pdf = self.tmp / "f.pdf"
        pdf.write_bytes(b"%PDF-1.4 x")
        raw = textwrap.dedent(f"""\
            From: Admin <admin@example.org>
            To: a@x.org, B <b@y.org>
            Bcc: hidden@z.org
            Subject: Invoice for café

            Hello,
            literal text: <#!part type=text/html> end
            <#part type=application/pdf filename="{pdf}" name=invoice.pdf><#/part>
            <#part type=text/html><p>hello</p><#/part>
            """)
        msg, rcpts = mml.compile_draft(raw)
        self.assertEqual(rcpts, ["a@x.org", "b@y.org", "hidden@z.org"])
        self.assertIsNone(msg["Bcc"])  # envelope only
        self.assertIsNotNone(msg["Date"])
        plain = msg.get_body(("plain",)).get_content()
        self.assertIn("literal text: <#part type=text/html> end", plain)
        self.assertIn("<p>hello</p>", msg.get_body(("html",)).get_content())
        atts = list(msg.iter_attachments())
        self.assertEqual([a.get_filename() for a in atts], ["invoice.pdf"])
        self.assertEqual(atts[0].get_content(), b"%PDF-1.4 x")
        self.assertEqual(str(msg["Subject"]), "Invoice for café")

    def test_refuses_unsendable(self):
        raw = "From: a@x.org\nTo: b@y.org\nSubject: {{SUBJECT}}\n\n<#part filename=rel.pdf><#/part>\n"
        with self.assertRaises(MailkitError) as cm:
            mml.compile_draft(raw)
        s = str(cm.exception)
        self.assertIn("{{SUBJECT}}", s)
        self.assertIn("absolute", s)

    def test_missing_attachment_and_unclosed(self):
        with self.assertRaises(MailkitError):
            mml.compile_draft("From: a@x.org\nTo: b@y.org\nSubject: s\n\n<#part filename=/nope.pdf><#/part>")
        with self.assertRaises(MailkitError):
            mml.parse("From: a\n\n<#part type=text/html>x")

    def test_set_header_keeps_body_bytes(self):
        raw = "To: b@y.org\nSubject: s\n  folded\n\nbody <#part filename=/x><#/part>\n"
        out = mml.set_header(raw, "From", "A <a@x.org>")
        self.assertTrue(out.endswith("\n\nbody <#part filename=/x><#/part>\n"))
        self.assertIn("Subject: s\n  folded\nFrom: A <a@x.org>", out)
        out2 = mml.set_header(out, "Subject", "t")
        self.assertIn("Subject: t\n", out2)
        self.assertNotIn("folded", out2)


class FoldersTest(Tmp):
    def test_local_folder(self):
        self.assertEqual(index.local_folder("INBOX.Sent Items"), "Sent Items")
        self.assertEqual(index.local_folder("INBOX/Sent"), "Sent")
        self.assertEqual(index.local_folder("INBOX"), "INBOX")
        self.assertEqual(index.local_folder("[Gmail]/Sent Mail"), "[Gmail]/Sent Mail")
        self.assertEqual(index.local_folder("INBOXES"), "INBOXES")

    def test_tag_rules(self):
        rules = index.tag_rules(self.cfg().account("admin"))
        self.assertIn('-inbox -- tag:inbox and not folder:"mail/INBOX"', rules)
        self.assertIn('+sent -unread -- folder:"mail/Sent Items" and not tag:sent', rules)
        self.assertIn('+deleted -- folder:"mail/Deleted Items" and not tag:deleted', rules)


class GeneratedTest(Tmp):
    def test_mbsyncrc(self):
        inst = self.cfg()
        rc = mirror.rc(inst)
        for needle in ("Channel admin", "Channel other", "Sync Pull", "Create Near", "Expunge Near",
                       f"Path {self.tmp}/Mail/admin/mail/", "PassCmd \"", "secret get admin password"):
            self.assertIn(needle, rc)

    def test_aerc_accounts(self):
        conf = aerc.accounts_conf(self.cfg(), "/qm")
        self.assertIn("[admin]\nsource = imaps://admin%40example.org@imap.example.org:993", conf)
        self.assertIn("outgoing = smtp://admin%40example.org@smtp.example.org:587", conf)
        self.assertIn("copy-to = INBOX.Sent Items", conf)
        self.assertIn("[archive-admin]\nsource = notmuch://mailkit.t.admin", conf)


if __name__ == "__main__":
    os.sys.exit(unittest.main())
