# mailkit — subcommands from `mailkit __commands`, accounts from `mailkit __accounts`
# (the instance in $MAILKIT_CONFIG, so a project's .envrc changes what completes).
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

set -l dacts new reply forward list show path edit approve unapprove discard
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a new -d 'Draft from file/stdin'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a reply -d 'Reply to a notmuch match'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a forward -d 'Forward a notmuch match'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a list -d 'Drafts of open accounts'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a show -d 'Print a draft'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a path -d 'Path of a draft'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a edit -d 'Edit in $EDITOR'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a approve -d 'Approve for send --approved'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a unapprove -d 'Back to review'
complete -c mailkit -n "__fish_seen_subcommand_from draft; and not __fish_seen_subcommand_from $dacts" -a discard -d 'Delete draft(s)'
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from new reply forward list; and test (__mailkit_nargs) -eq 3' -a '(mailkit __accounts)'
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from new' -F
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from reply' -l all -d 'Reply to all'
complete -c mailkit -n '__fish_seen_subcommand_from draft; and __fish_seen_subcommand_from list' -l json -d 'JSON output'

complete -c mailkit -n '__fish_seen_subcommand_from send' -l approved -d 'Send every approved draft' -a '(mailkit __accounts) all'
complete -c mailkit -n '__fish_seen_subcommand_from send' -l dry-run -d 'Print, send nothing'
complete -c mailkit -n '__fish_seen_subcommand_from keyrings' -l check -d 'Exit 0 only if the keyrings are unlocked'
