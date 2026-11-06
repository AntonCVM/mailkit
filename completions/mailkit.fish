# mailkit — subcommands from `mailkit __commands`, accounts from `mailkit __accounts`,
# draft ids from `mailkit __drafts`, messages from `mailkit __messages` (the instance in $MAILKIT_CONFIG, so a project's
# .envrc changes what completes; only open accounts have drafts to offer).
complete -c mailkit -f
complete -c mailkit -s h -l help -d Help

set -l cmds (mailkit __commands 2>/dev/null)
complete -c mailkit -n "not __fish_seen_subcommand_from $cmds" -a live -d 'aerc over IMAP, live'
complete -c mailkit -n "not __fish_seen_subcommand_from $cmds" -a archive -d 'Open + sync, aerc on the index, close on exit'
complete -c mailkit -n "not __fish_seen_subcommand_from $cmds" -a open -d 'Mount sealed store(s)'
complete -c mailkit -n "not __fish_seen_subcommand_from $cmds" -a close -d 'Unmount, verified'
complete -c mailkit -n "not __fish_seen_subcommand_from $cmds" -a status -d 'State, size, drafts per account'
complete -c mailkit -n "not __fish_seen_subcommand_from $cmds" -a sync -d 'mbsync + notmuch new + tags'
complete -c mailkit -n "not __fish_seen_subcommand_from $cmds" -a index -d 'notmuch new + tag rules, no network'
complete -c mailkit -n "not __fish_seen_subcommand_from $cmds" -a init -d 'Create store(s)'
complete -c mailkit -n "not __fish_seen_subcommand_from $cmds" -a secret -d 'Get or store a credential'
complete -c mailkit -n "not __fish_seen_subcommand_from $cmds" -a keyrings -d 'Secret Service collections'
complete -c mailkit -n "not __fish_seen_subcommand_from $cmds" -a nm -d 'notmuch on one account'
complete -c mailkit -n "not __fish_seen_subcommand_from $cmds" -a draft -d 'Write, list, review drafts'
complete -c mailkit -n "not __fish_seen_subcommand_from $cmds" -a send -d 'Send draft(s)'
complete -c mailkit -n "not __fish_seen_subcommand_from $cmds" -a config -d 'Print the resolved config'

function __mailkit_nargs
    count (commandline -opc)
end

# <account|all> right after these
complete -c mailkit -n '__fish_seen_subcommand_from live archive open close sync index init; and test (__mailkit_nargs) -eq 2' -a '(mailkit __accounts) all'
complete -c mailkit -n '__fish_seen_subcommand_from nm; and test (__mailkit_nargs) -eq 2' -a '(mailkit __accounts)'
complete -c mailkit -n '__fish_seen_subcommand_from status' -l json -d 'JSON output'

complete -c mailkit -n '__fish_seen_subcommand_from secret; and test (__mailkit_nargs) -eq 2' -a 'get set'
complete -c mailkit -n '__fish_seen_subcommand_from secret; and test (__mailkit_nargs) -eq 3' -a '(mailkit __accounts)'
complete -c mailkit -n '__fish_seen_subcommand_from secret; and test (__mailkit_nargs) -eq 4' -a 'password cipher'

set -l dacts new reply forward list show path edit replace approve unapprove discard
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a new -d 'Draft from file/stdin'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a reply -d 'Reply to a notmuch match'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a forward -d 'Forward a notmuch match'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a list -d 'Drafts of open accounts'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a show -d 'Print a draft'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a path -d 'Path of a draft'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a edit -d 'Edit in $EDITOR'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a replace -d 'Replace text from file/stdin'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a approve -d 'Approve for send --approved'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a unapprove -d 'Back to review'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a discard -d 'Delete draft(s)'
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from new reply forward list; and test (__mailkit_nargs) -eq 3' -a '(mailkit __accounts)'
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from new' -F
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from replace; and test (__mailkit_nargs) -eq 4' -F
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from reply' -l all -d 'Reply to all'
# reply/forward: the newest messages (MAILKIT_COMPLETE_LIMIT, default 40); type part of
# a sender or subject to narrow them, or a notmuch query (from:juan) to search all.
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from reply forward; and test (__mailkit_nargs) -eq 4' -k -a '(mailkit __messages (commandline -opc)[4] (commandline -ct))'
# draft ids: one for show/path/edit/replace, any number for approve/unapprove/discard
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from show path edit replace; and test (__mailkit_nargs) -eq 3' -a '(mailkit __drafts)'
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from approve' -a '(mailkit __drafts --unapproved)'
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from unapprove' -a '(mailkit __drafts --approved)'
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from discard' -a '(mailkit __drafts)'
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from approve' -l last -d 'The draft last shown, if unchanged'
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from approve' -l all -d 'Approve every draft of an account (default: all)' -a '(mailkit __accounts) all'
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from list' -l json -d 'JSON output'

complete -c mailkit -n '__fish_seen_subcommand_from send' -l approved -d 'Send every approved draft' -a '(mailkit __accounts) all'
complete -c mailkit -n '__fish_seen_subcommand_from send; and not __fish_seen_argument -l approved' -a '(mailkit __drafts)'
complete -c mailkit -n '__fish_seen_subcommand_from send' -l dry-run -d 'Print, send nothing'
complete -c mailkit -n '__fish_seen_subcommand_from keyrings' -l check -d 'Exit 0 only if the keyrings are unlocked'
