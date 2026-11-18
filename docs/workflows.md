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
mailkit draft approve --all [admin|all]      # or every draft still in review
mailkit draft approve --last                 # or the one `draft show` just printed
mailkit send --approved [admin|all]          # then send exactly those
mailkit send --dry-run <id>                  # the MIME that would go out
```

In fish, Tab completes ids: drafts for `show`/`edit`/`approve`/`send`… (only the
unapproved ones for `approve`), and messages for `reply`/`forward` — the newest 40
(`MAILKIT_COMPLETE_LIMIT`), described by date, sender and subject. Type part of a
sender or subject to narrow them (`juan<Tab>`), or a notmuch query to search the
whole index (`from:juan<Tab>`, `subject:factura<Tab>`; whole words — fish takes
`*` as a file glob). Only open accounts complete; nothing is opened for it.

Change a draft with `draft edit` or `draft replace`, not by rewriting the file
from `draft path`: notmuch keeps the headers it first indexed (subject,
recipients) until the message is reindexed, and these two reindex for you.
`replace` keeps the draft's Message-ID — text with a different one is refused.
An approval is of a text: if `edit` or `replace` changes the draft, `approved`
goes back to `review` (saving without changes keeps it). For the same reason
`approve --last` refuses a draft whose text changed after `draft show` printed it.

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

## Agents processing mail: `mailkit agent`

For an agent that goes through the mail every day and must not miss anything.
`unread` is no use for that: it is the person's, mirrored from the server, and
reading from the mirror rightly changes nothing. So an agent has a state of its
own, per message:

```
pending ── agent next / read ──▶ seen ── agent done ──▶ done
   ▲                                                    │
   └──────────────────── agent unsee ◀──────────────────┘
```

- **Marking is part of reading.** `next` and `read` mark seen the new mail they
  print, right after printing it; nothing to remember afterwards. `show` prints
  the same and marks nothing: old mail as context. `pending` lists and marks nothing.
- **What counts as processed** is `[agent] pending`: `"undone"` (the default),
  pending until `agent done`; or `"unseen"`, shown once is enough. Printed is not
  read: an agent's tool may cut long output, and what it never saw would be lost
  under `"unseen"`. Under `"undone"` the worst case is a thread listed again.
  `done` refuses a message never shown; `next` serves only threads with new mail,
  so shown-but-open ones are found in `pending` (state `seen`) and closed with
  `done --thread`.
- **Cutoff.** `[agent] since = "YYYY-MM-DD"`: older mail is never pending. Until
  it is set, `pending` and `next` refuse.
- **What is pending:** received since the cutoff, in an open account — spam
  included (bounces and notices land there; `--spam exclude|only` to split it),
  trash, drafts and our own mail not; `[agent] ignore` queries left out (still
  readable with `show` and `nm`).
- **A Message-ID is one message** across the instance: in two accounts it is
  listed once, with both, and marked in both.
- **Answered?** `pending` and `read` say when something of ours is later than the
  last received message, strongest evidence first: in the same thread (`how:
  thread`); a reply to one of its messages by In-Reply-To/References from any
  account (`how: reply` — answered from another account); both `confirmed: true`.
  Else, anything later addressed to that sender (`how: sender`, `confirmed:
  false`, `[answered? …]`): may be about something else — read it. Campaigns
  (List-Unsubscribe, Precedence: bulk) never count. A closed account is not looked at.
- **Whole threads.** `read`/`show`/`next` print the whole thread, oldest first,
  ours from Sent included, each message labelled `new` (marked now), `seen`,
  `done`, `ours` or `context` (before the cutoff, ignored). Attachments are listed
  (part, name, type, size); an empty text part is replaced by the HTML as text —
  nothing rendered, nothing fetched. Bodies over 12000 characters are cut with a
  marker (`--full`, `--max-body N`).
- **Persistence.** The truth is the ledger, `<store>/agent/log.jsonl`, inside the
  seal: id, operation, time. The `agent-seen`/`agent-done` tags are its copy in the
  index (`mailkit nm <acct> search tag:agent-seen`); after a rebuilt index,
  `mailkit agent restore` re-tags. `agent log` shows what was marked and when.
- Nothing touches the server, `unread` or any IMAP flag.

```sh
mailkit agent pending [acct] [--json]    # what is left: one line per thread, oldest first
mailkit agent next -n 5 [--json]         # the next 5 threads, whole; marks them seen
mailkit agent read id:<msgid>…           # those threads; marks their new mail seen
mailkit agent show id:<msgid>…           # the same, marks nothing
mailkit agent done [--thread] id:<msgid>…   # with pending = "undone"
mailkit agent unsee --last               # a sweep cut short: its last batch back to pending
mailkit agent unsee --since 2026-11-18T09:00 | id:<msgid>…
mailkit agent attach id:<msgid> <part|name> <file|dir>   # needs [agent] extract = true
mailkit agent log [-n 50] [--json] ; mailkit agent restore [acct]
```

A long backlog goes in parts: `pending` first (one line each), then `next -n N`
or `read` thread by thread. `next` stops adding whole threads past `--budget`
characters (20000; fewer than `-n` is normal — run it again) and marks only what
it printed. Its text ends with a line `==== END: …`; if the reader does not see
it, the output was cut on the way: `agent unsee --last`. (Truncated `--json`
does not parse, which says the same.) Don't pipe `next`/`read` through `head`.

**For scripts:** `--json` on `pending`, `next`, `read`, `show` is one document
with `"schema": 1`, which changes only incompatibly. A project-specific step
(case numbers, deadlines) reads it instead of mailkit growing hooks:

```sh
mailkit agent pending --json | jq -r '.threads[] | select(.answered.confirmed != true) | .ref'
mailkit agent show --json id:<msgid> | jq -r '.threads[].messages[].body' | rg -o 'KD/[0-9]+'
```

`pending` → `threads[]`: `ref`, `account`, `accounts`, `thread`, `subject`, `from`,
`first`, `last`, `messages`, `pending[]` (`id`, `state`), `spam`, `answered`
(`null` or `how` thread|reply|sender, `confirmed`, `account`, `id`, `date`). `next`/`read`/`show` → `marks` (the ids
marked after the output), `left` (next: pending threads not printed) and `threads[]`: `ref`, `account`, `accounts`, `thread`,
`subject`, `answered`, `messages[]` (`id`, `state`, `pending`, `accounts`, `date`,
`from`, `to`, `cc`, `subject`, `tags`, `spam`, `body_from`, `body`, `attachments[]`
with `part`, `filename`, `type`, `size`).

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
