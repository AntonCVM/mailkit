# Workflows

## The switch

Everything that touches the server or a sealed store needs the keyring: the
IMAP/SMTP password for sync and send, the passphrase for `open`. So the
KeePassXC database being unlocked **is** the permission, for people and agents
alike:

| KeePassXC | store | what works |
|---|---|---|
| locked | closed | nothing; the ciphertext is all there is |
| locked | open | read, search, tag, write drafts — nothing leaves, nothing syncs |
| unlocked | open | all of the above + sync + send |

Lock the database to stop scheduled flows and agents from sending; close the
store to stop them from reading.

## Drafts, review, send

```sh
mailkit draft new admin < mensaje.mml        # or: mailkit draft new admin mensaje.mml
mailkit draft reply admin 'id:<msgid>' --all
mailkit draft forward admin 'id:<msgid>'
mailkit draft list                           # the open accounts' outboxes
mailkit draft show <id> ; mailkit draft edit <id>
mailkit draft replace <id> < nuevo.mml       # new text, no editor: same id and tags
mailkit send <id> [<id>…]                    # send_policy = direct
mailkit draft approve <id>…                  # review: mark the good ones
mailkit send --approved [admin|all]          # then send exactly those
mailkit send --dry-run <id>                  # the MIME that would go out
```

Change a draft with `draft edit` or `draft replace`, not by rewriting the file
from `draft path`: notmuch keeps the headers it first indexed (subject,
recipients) until the message is reindexed, and these two reindex for you.
`replace` keeps the draft's Message-ID — text with a different one is refused.
An approval is of a text: if `edit` or `replace` changes the draft, `approved`
goes back to `review` (saving without changes keeps it).

Every draft is checked before anything leaves: an unfilled `{{PLACEHOLDER}}`, a
missing or relative attachment path, a From that is not the account, no
recipients or no subject — and a batch sends nothing if one of them fails.

After the SMTP hand-off the draft leaves the outbox (sending twice is worse than
a missing copy), the compiled message goes to `sent/`, and a copy is appended to
the server's Sent folder. A failed copy is reported, tagged `copy-failed`, and
the exit code is 1.

`send_policy` is a convention, not a lock: whatever can read the unlocked keyring
can send mail without mailkit.

## MML

```
From: Admin <admin@example.org>        (optional: filled in from the account)
To: Client <c@example.com>
Cc: …
Bcc: …                                 (envelope only; kept in your own copy)
Subject: …

Body text.

<#part type=application/pdf filename=/abs/path/invoice.pdf><#/part>
<#part filename="~/Documents/x.zip" name=renamed.zip><#/part>
<#part type=text/html><p>HTML alternative</p><#/part>
<#!part …>   ← the literal text <#part …>
```

## Scheduled flows

A flow is `mailkit sync <acct>` followed by whatever reads the index: `mailkit nm
<acct> search --format=json tag:unread`, `mailkit nm <acct> show --format=json
id:<msgid>`, `mailkit nm <acct> tag +resumido -- id:<msgid>`. Tags live in the
index, inside the store. A flow that runs while the store is closed or the
keyring locked fails at the first step and says so — that is the switch working.

To skip a scheduled run while the keyring is locked instead of failing it, gate
it on the switch itself: `mailkit keyrings --check` prints nothing and exits 0
only if every keyring the instance uses is unlocked. In a systemd unit:

```ini
[Service]
Type=oneshot
ExecCondition=mailkit keyrings --check   # locked → the run is skipped, not failed
ExecStart=…
```
