"""The sealed store for real: gocryptfs + FUSE under /tmp. Opt-in
(MAILKIT_TEST_FUSE=1): build sandboxes have no /dev/fuse."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mailkit import cli

ENABLED = os.environ.get("MAILKIT_TEST_FUSE") == "1" and shutil.which("gocryptfs") and shutil.which("notmuch")


def run(*argv, stdin=None):
    r = subprocess.run([sys.executable, "-m", "mailkit", *argv], input=stdin, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


@unittest.skipUnless(ENABLED, "set MAILKIT_TEST_FUSE=1 (needs gocryptfs, notmuch, /dev/fuse)")
class SealTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mailkit-seal-"))
        cfg = self.tmp / "config.toml"
        # The command backend prints the same passphrase each time: the test
        # needs no keyring, and `init` must not try to store into one.
        cfg.write_text(f"""
[mailkit]
instance = "seal"
root = "{self.tmp}/Mail"
[secrets]
backend = "command"
command = "printf passphrase-{{handle}}-{{kind}}"
[accounts.box]
address = "box@example.org"
imap = {{ host = "imap.example.org", port = 993 }}
smtp = {{ host = "smtp.example.org", port = 465 }}
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
        self.mnt = self.tmp / "Mail" / "box"

    def tearDown(self):
        if os.path.ismount(self.mnt):
            subprocess.run(["fusermount3", "-u", str(self.mnt)], capture_output=True)
            subprocess.run(["fusermount", "-u", str(self.mnt)], capture_output=True)
        os.environ.clear()
        os.environ.update(self.env_backup)
        if os.path.isdir(self.mnt):
            os.chmod(self.mnt, 0o700)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_open_draft_close(self):
        rc, out, err = run("init", "box")
        self.assertEqual(rc, 0, err)
        self.assertTrue((self.tmp / "Mail/.cipher/box/gocryptfs.conf").is_file())
        self.assertEqual(self.mnt.stat().st_mode & 0o777, 0o500, "closed mount point must be 500")
        rc, _, err = run("draft", "new", "box", stdin="To: a@b.c\nSubject: s\n\ntopsecret\n")
        self.assertEqual(rc, 1)
        self.assertIn("closed", err)

        self.assertEqual(run("open", "box")[0], 0)
        self.assertTrue(os.path.ismount(self.mnt))
        rc, out, err = run("draft", "new", "box", stdin="To: a@b.c\nSubject: s\n\ntopsecret\n")
        self.assertEqual(rc, 0, err)
        rc, out, _ = run("status", "--json")
        self.assertIn('"state": "open"', out)
        self.assertIn('"drafts": 1', out)

        rc, out, err = run("close")
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.ismount(self.mnt))
        self.assertEqual(self.mnt.stat().st_mode & 0o777, 0o500)
        # Nothing readable outside: the draft text is only in ciphertext.
        hits = subprocess.run(["grep", "-r", "-l", "topsecret", str(self.tmp / "Mail")], capture_output=True)
        self.assertEqual(hits.stdout, b"")

    def test_close_reports_busy(self):
        run("init", "box")
        run("open", "box")
        p = subprocess.Popen(["sleep", "30"], cwd=self.mnt)
        try:
            rc, out, err = run("close", "box")
            self.assertEqual(rc, 1)
            self.assertIn("STILL MOUNTED", err)
            self.assertTrue(os.path.ismount(self.mnt))
        finally:
            p.kill()
            p.wait()
        self.assertEqual(run("close", "box")[0], 0)


if __name__ == "__main__":
    unittest.main()
