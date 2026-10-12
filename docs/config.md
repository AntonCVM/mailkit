# Config reference

One TOML file per instance: `$MAILKIT_CONFIG`, else `~/.config/mailkit/config.toml`.
A project that carries its own accounts exports `MAILKIT_CONFIG` from its `.envrc`;
two instances never share stores, profiles or generated files.

```toml
[mailkit]
instance = "work"            # names the profiles (mailkit.<instance>.<account>) and the runtime dir
root = "~/Mail/work"         # stores live here; default ~/Mail/<instance>
send_policy = "direct"       # direct: `send <id>` sends any draft
                             # approved: only drafts tagged approved (mailkit draft approve)

[secrets]
backend = "secret-service"   # default. Or "command":
# command = "pass show mail/{address}/{kind}"   # placeholders: {address} {handle} {kind}

[defaults]                   # merged under every account; tables merge one level deep
keyring = "pass-mail-work"
imap = { host = "mail.example.org", port = 993, tls = "tls" }       # tls | starttls | none (localhost only)
smtp = { host = "mail.example.org", port = 587, tls = "starttls" }
sent_copy = true             # false when the server files its own copy of what SMTP sends (Gmail)
seal = true                  # gocryptfs store; false = plain directory, always "open"
expunge = true               # false: keep in the mirror what the server deleted

[defaults.folders]           # IMAP names, as the server spells them
inbox = "INBOX"
sent = "INBOX.Sent"
drafts = "INBOX.Drafts"
trash = "INBOX.Trash"
spam = "INBOX.Spam"

[accounts.admin]             # the handle: lowercase letters, digits, '-'
address = "admin@example.org"
name = "Administration"      # display name in From:
# login = "admin"            # default: the address
```

## Keyring items

Backend `secret-service`: per account, two items in the KeePassXC database whose
**Database name** (Database Settings → General) is the account's `keyring`, with
the Secret Service integration enabled for it (Database Settings → Secret Service
Integration → expose its group):

| Attributes | What |
|---|---|
| `mail <address> kind password` | the IMAP/SMTP password (`mailkit secret set <acct>`) |
| `mail <address> kind cipher` | the store passphrase (`mailkit init` generates it) |

Lookups search every unlocked database; writes go to the account's own one.
`mailkit keyrings` lists what the Secret Service exposes.

## Layout

```
<root>/
  .cipher/<handle>/        gocryptfs ciphertext (sealed accounts) — this is what to back up
  <handle>/                the store: the mount point (mode 500 while closed), or a plain dir
    mail/                  mbsync's mirror (read-only pull of the server)
    outbox/                drafts, a Maildir of MML files
    sent/                  what mailkit sent, compiled — the record if the Sent copy failed
    .notmuch/              the index of all three
```

Generated on every run, never edited:

- `$XDG_RUNTIME_DIR/mailkit/<instance>/mbsyncrc`, `aerc-accounts.conf`, `aerc-query-map`
- `$XDG_CONFIG_HOME/notmuch/mailkit.<instance>.<handle>/config` — where libnotmuch
  looks up a profile (aerc opens the index by profile name). Paths only, no secrets.

## Folder names in the mirror

mbsync strips the server's personal namespace: `INBOX.Sent Items` is the
directory `mail/Sent Items`. Gmail's `[Gmail]/Sent Mail` stays as it is. The
tag rules (`sent`, `spam`, `deleted`, only `mail/INBOX` keeps `inbox`) are derived
from `folders` with that mapping.
