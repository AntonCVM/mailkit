# mailkit

Terminal mail with a **local mirror per account** — mbsync pulls it, notmuch
indexes it — each mirror **sealed on its own** (gocryptfs, own passphrase, own
index), and credentials from the desktop keyring (KeePassXC over the Secret
Service). An unlocked database is the switch that lets sync, send and mounts
happen; a locked one stops them all, without a prompt per call.

On top: a draft queue for people and agents (MML drafts indexed and tagged
`review`, `approve`, `send --approved`), and aerc as an optional reader.

Linux only. Python ≥ 3.11, standard library only.

## Install

With Nix: `nix profile install github:AntonCVM/mailkit`, or the flake's
`packages.<system>.mailkit` / `overlays.default`. The wrapper brings every tool below.

Without Nix (Debian/Ubuntu shown):

```sh
sudo apt install isync notmuch gocryptfs libsecret-tools fuse3 aerc   # aerc optional
pipx install .          # or: uv tool install .
```

…and KeePassXC with **Secret Service integration** enabled (Settings → Secret Service
Integration), the database's group exposed. GNOME Keyring would also answer the
same lookups, but the switch then is GNOME's lock.

## First run

```sh
cp docs/example.toml ~/.config/mailkit/config.toml    # edit: accounts, keyring name
mailkit config                     # what it understood
mailkit keyrings                   # the KeePassXC database must be listed, unlocked
mailkit secret set <account>       # the IMAP/SMTP password (app password for Gmail)
mailkit init <account|all>         # sealed store + generated passphrase in the keyring
mailkit sync <account>             # opens it, mirrors, indexes
mailkit close                      # unmount, verified
```

Back up the KeePassXC database: without its `kind cipher` item a store is unreadable.

## Use

```sh
mailkit                            # aerc over IMAP, live
mailkit archive <account|all>      # open + sync + aerc on the index; closes on exit
mailkit status
mailkit nm <account> search from:someone and date:2026-01..
mailkit draft new <account> < msg.mml ; mailkit send <id>
```

`mailkit --help` and `mailkit <command> --help` for everything;
[docs/workflows.md](docs/workflows.md) for drafts, review and scheduled flows;
[docs/config.md](docs/config.md) for the TOML.

## Develop

`nix develop` (or direnv), then `scripts/check` — ruff + unit tests + an end-to-end
test against fake SMTP/IMAP servers. `MAILKIT_TEST_FUSE=1 scripts/check` also runs
the real gocryptfs test (needs /dev/fuse). Fixtures live under /tmp only.
