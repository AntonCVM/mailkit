# mailkit — instructions for agents working on this repo

Terminal mail with a sealed local mirror per account. User docs: `README.md`,
`docs/config.md`, `docs/workflows.md`. This file is about changing the code.

## Shape

`src/mailkit/`, standard library only, Python ≥ 3.11. One module per concern:

| Module | Owns |
|---|---|
| `config.py` | the TOML → `Instance`/`Account`; every path is a property here |
| `secrets.py` | the only place a credential is read or stored |
| `seal.py` | gocryptfs open/close/init; mount-point modes |
| `mirror.py` | the generated mbsyncrc and `sync` |
| `index.py` | notmuch profiles, `notmuch new`, the tag rules |
| `mml.py` | draft text → MIME; the sendability checks |
| `outbox.py` | the draft queue (Maildir + tags) |
| `send.py` | SMTP + the IMAP copy to Sent |
| `aerc.py` | the generated accounts.conf |
| `cli.py` | argparse; verbs only call the modules above |

External tools (mbsync, notmuch, gocryptfs, secret-tool, busctl, aerc) are called
by name from PATH. The repo never requires Nix: the flake only packages it.

## Rules that are not style

- **Never test on real paths.** Fixtures under `tempfile.mkdtemp()`, config via
  `MAILKIT_CONFIG`, `XDG_CONFIG_HOME` and `XDG_RUNTIME_DIR` pointed into it — the
  existing tests show the pattern. Never `~/Mail`, never a real keyring
  (`[secrets] backend = "command"` with `printf` in tests).
- **Say only what was measured.** `close` reports "closed" only after re-checking
  the mount; anything that cannot verify says what it sent, not what happened.
- **No secret on disk or in output**, except `mailkit secret get`, which exists to
  print one for mbsync/gocryptfs/aerc.
- **The mirror is read-only** (`Sync Pull`). Nothing mailkit does may write to the
  server except `send` (SMTP + APPEND to Sent).
- **`--help` returns before touching anything** (config, keyring, mounts); a test
  checks it. Help text in English.
- New subcommand → `cli.py` + `completions/mailkit.fish` + docs.

## Check

`scripts/check` (ruff + unittest). `MAILKIT_TEST_FUSE=1 scripts/check` adds the
real gocryptfs test. Both must pass before a commit.
