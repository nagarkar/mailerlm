"""The mailerlm command line: one verb per typed-API function (metalm cli.md).

A logic-free surface over `mailerlm.api`; the parser, help, completion and global options come
from `corpuslm.clikit`.

Never imports: nothing below it may import it (enforced by the import contract "surfaces on top"
in pyproject.toml).
"""

from __future__ import annotations

import inspect
import json
import sys
from datetime import datetime

from corpuslm import clikit

from mailerlm import api, paths, server
from mailerlm.errors import MailerlmError, NotConfiguredError

HELP_FOOTER = (
    "Global option: --json (the board as JSON) anywhere on the line.",
    "Keys and the workspace pointer live in .env (see env.example); 'mailerlm setup' writes the pointer.",
    "Every verb prints a board; contacts and messages stay in the workspace, never on stdout.",
)


def _mutating(parser) -> None:
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run", help="show the counts; write nothing"
    )


def _reason(parser) -> None:
    parser.add_argument(
        "--reason",
        required=True,
        help="why no confirmation letter is needed, e.g. 'known personally', 'confirmed by email' "
        "or 'confirmed in person'",
    )


def _confirm_reason(parser) -> None:
    parser.add_argument(
        "--confirm-reason",
        dest="confirm_reason",
        help="also confirm them on --list yourself, with this reason (e.g. 'known personally')",
    )


def _send_lists(parser) -> None:
    parser.add_argument(
        "--list",
        dest="lists",
        action="append",
        help="a list to send to; repeat for several, in order (default: the campaign's lists)",
    )


def _list_and_tags(parser) -> None:
    parser.add_argument("--list", dest="list_name", help="join this list")
    parser.add_argument(
        "--tag", dest="tags", action="append", default=[], help="tag every contact (repeatable)"
    )


def build_parser() -> clikit.Parser:
    parser = clikit.Parser(prog="mailerlm")
    sub = parser.add_subparsers(dest="command")

    setup = sub.add_parser(
        "setup", help="record the workspace pointer, create the workspace and database"
    )
    setup.add_argument(
        "--dry-run", action="store_true", help="print what would change; write nothing"
    )
    setup.set_defaults(api="setup")
    clikit.describe(
        setup,
        "Write MAILERLM_WORKSPACE in the managed block of .env (after a backup, with a diff), "
        "then create the workspace folder and the database. A second run changes nothing.",
        "mailerlm setup --dry-run",
    )

    doctor = sub.add_parser(
        "doctor", help="check the environment; exits 1 when a required check fails"
    )
    doctor.set_defaults(api="doctor")
    clikit.describe(doctor, "List each check with its state, detail and fix.", "mailerlm doctor")

    gmail = sub.add_parser("gmail", help="Gmail connection")
    gmail_verbs = gmail.add_subparsers(dest="subcommand", required=True)
    connect = gmail_verbs.add_parser(
        "connect", help="open a browser to grant readonly access and store the token"
    )
    connect.set_defaults(api="gmail_connect")
    clikit.describe(
        connect,
        "Open a browser for readonly Gmail consent and store the token in the workspace (mode 600). "
        "Needs GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env.",
        "mailerlm gmail connect",
    )

    lists = sub.add_parser("lists", help="mailing lists")
    list_verbs = lists.add_subparsers(dest="subcommand", required=True)
    lists_add = list_verbs.add_parser("add", help="create a list")
    lists_add.add_argument("name")
    lists_add.add_argument("--sender-name", help="override the sender name for this list")
    lists_add.add_argument("--sender-address", help="override the sender address for this list")
    _mutating(lists_add)
    lists_add.set_defaults(api="lists_add")
    clikit.describe(
        lists_add,
        "Create a list; a second add of the same name changes nothing.",
        "mailerlm lists add book-interest",
    )
    lists_test = list_verbs.add_parser("test", help="mark a list as a test list")
    lists_test.add_argument("name")
    lists_test.add_argument(
        "--clear", action="store_true", help="make it a real list again (no subject prefix)"
    )
    _mutating(lists_test)
    lists_test.set_defaults(api="lists_test")
    clikit.describe(
        lists_test,
        "Mark a list as a test list: every send whose lists are all test lists carries 'Testing: ' before its subject. The campaign's stored subject is not edited.",
        "mailerlm lists test Testing",
    )
    lists_footer = list_verbs.add_parser(
        "footer", help="show or hide the footer on letters to a list"
    )
    lists_footer.add_argument("name")
    shown = lists_footer.add_mutually_exclusive_group(required=True)
    shown.add_argument(
        "--hide", dest="show", action="store_false", help="no opt-out line or postal address"
    )
    shown.add_argument("--show", dest="show", action="store_true", help="the footer (the default)")
    _mutating(lists_footer)
    lists_footer.set_defaults(api="lists_footer")
    clikit.describe(
        lists_footer,
        "Show or hide the footer (opt-out line and postal address) on letters to a list. A letter "
        "loses it only when every list it goes to hides it; the List-Unsubscribe header stays.",
        "mailerlm lists footer friends --hide",
    )
    lists_confirm = list_verbs.add_parser(
        "confirm", help="confirm everyone on a list yourself, with a reason"
    )
    lists_confirm.add_argument("list_name", metavar="list")
    _reason(lists_confirm)
    _mutating(lists_confirm)
    lists_confirm.set_defaults(api="lists_confirm")
    clikit.describe(
        lists_confirm,
        "Confirm everyone on a list who is imported or pending, without a confirmation letter, with one "
        "required reason; --dry-run shows who would change. People marked do-not-mail, unsubscribed or "
        "suppressed are left as they are and listed; other lists are untouched.",
        'mailerlm lists confirm friends --reason "known personally" --dry-run',
    )
    lists_show = list_verbs.add_parser("show", help="every list with its member counts by status")
    lists_show.set_defaults(api="lists_show")
    clikit.describe(
        lists_show,
        "Show every list with its member counts by consent status; a test list is marked [test list], "
        "a list that hides the footer [footer hidden].",
        "mailerlm lists show",
    )

    contacts = sub.add_parser("contacts", help="people on your lists")
    contact_verbs = contacts.add_subparsers(dest="subcommand", required=True)
    add = contact_verbs.add_parser("add", help="add one contact")
    add.add_argument("--email")
    add.add_argument("--name", default="")
    add.add_argument("--linkedin-url", dest="linkedin_url")
    add.add_argument(
        "--preferred-name",
        dest="preferred_name",
        help="the name they like to be called by, if not their first name",
    )
    _list_and_tags(add)
    _confirm_reason(add)
    _mutating(add)
    add.set_defaults(api="contacts_add")
    clikit.describe(
        add,
        "Add one contact by email, LinkedIn URL, or both; optionally join a list. New list members start as 'imported' and are not mailed.",
        "mailerlm contacts add --email ada@example.com --name Ada --list book-interest",
    )
    edit = contact_verbs.add_parser(
        "edit", help="change one contact's name, address or preferred name"
    )
    edit.add_argument("contact", help="an email address or a contact id")
    edit.add_argument("--email", help='a new email address ("" removes it)')
    edit.add_argument("--name", help="the official name")
    edit.add_argument("--linkedin-url", dest="linkedin_url", help='a LinkedIn URL ("" removes it)')
    edit.add_argument(
        "--preferred-name",
        dest="preferred_name",
        help='the name letters greet them by ({{ preferred_name }}); "" means their first name',
    )
    _mutating(edit)
    edit.set_defaults(api="contacts_edit")
    clikit.describe(
        edit,
        "Change only the fields given. The preferred name is what {{ preferred_name }} (the greeting) "
        'says instead of the first word of the name; --preferred-name "" clears it.',
        'mailerlm contacts edit 12 --preferred-name "Grace"',
    )
    imp = contact_verbs.add_parser("import", help="import pasted text or a CSV")
    imp.add_argument("source", help="a file, or - for standard input")
    _list_and_tags(imp)
    _confirm_reason(imp)
    _mutating(imp)
    imp.set_defaults(api="contacts_import")
    clikit.describe(
        imp,
        "Import lines like 'Name: email', 'Name <email>', a LinkedIn profile URL, or a CSV with an email header "
        "(optional name, preferred_name and tags columns). Re-importing adds nothing.",
        "mailerlm contacts import people.txt --list book-interest --dry-run",
    )
    confirm = contact_verbs.add_parser(
        "confirm", help="confirm one person on one list yourself, with a reason"
    )
    confirm.add_argument("contact", help="an email address or a contact id")
    confirm.add_argument("list_name", metavar="list")
    _reason(confirm)
    _mutating(confirm)
    confirm.set_defaults(api="contacts_confirm")
    clikit.describe(
        confirm,
        "Confirm one person on one list without a confirmation letter (imported or pending -> subscribed), "
        "with a required reason; their other lists are untouched. Never for someone marked do-not-mail, "
        "unsubscribed or suppressed.",
        'mailerlm contacts confirm ada@example.com friends --reason "known personally"',
    )
    history = contact_verbs.add_parser("history", help="every consent change of one person")
    history.add_argument("contact", help="an email address or a contact id")
    history.set_defaults(api="contacts_history")
    clikit.describe(
        history,
        "Show every consent change of a person's memberships and how it happened: a confirmation "
        "letter, their reply, your confirmation and its reason, a request to stop.",
        "mailerlm contacts history ada@example.com",
    )
    find = contact_verbs.add_parser("find", help="find contacts (addresses masked)")
    find.add_argument("--list", dest="list_name")
    find.add_argument("--tag")
    find.add_argument(
        "--status", choices=["imported", "pending", "subscribed", "unsubscribed", "suppressed"]
    )
    find.add_argument(
        "--show-full-addresses",
        action="store_true",
        dest="show_full_addresses",
        help="print full addresses; for your own terminal",
    )
    find.set_defaults(api="contacts_find")
    clikit.describe(
        find,
        "List contacts matching every filter, addresses masked unless --show-full-addresses.",
        "mailerlm contacts find --list book-interest --status imported",
    )
    for verb, api_name, text, example in (
        (
            "tag",
            "contacts_tag",
            "Put a tag on a contact.",
            "mailerlm contacts tag ada@example.com early-access",
        ),
        (
            "untag",
            "contacts_untag",
            "Take a tag off a contact.",
            "mailerlm contacts untag ada@example.com early-access",
        ),
    ):
        leaf = contact_verbs.add_parser(verb, help=text.rstrip("."))
        leaf.add_argument("email")
        leaf.add_argument("tag")
        _mutating(leaf)
        leaf.set_defaults(api=api_name)
        clikit.describe(leaf, text, example)
    for verb, api_name, text, example in (
        (
            "join",
            "contacts_join",
            "Add a contact to a list as 'imported' (not mailed until they confirm).",
            "mailerlm contacts join ada@example.com beta-readers",
        ),
        (
            "leave",
            "contacts_leave",
            "Remove a contact from a list; their other lists are untouched.",
            "mailerlm contacts leave ada@example.com beta-readers",
        ),
    ):
        leaf = contact_verbs.add_parser(verb, help=text.split(";")[0].rstrip("."))
        leaf.add_argument("email")
        leaf.add_argument("list_name", metavar="list")
        _mutating(leaf)
        leaf.set_defaults(api=api_name)
        clikit.describe(leaf, text, example)
    blocked = contact_verbs.add_parser("do-not-mail", help="never mail this person, on any list")
    blocked.add_argument("email")
    blocked.add_argument("--clear", action="store_true", help="lift the block")
    _mutating(blocked)
    blocked.set_defaults(api="contacts_do_not_mail")
    clikit.describe(
        blocked,
        "Set (or with --clear, lift) the global do-not-mail flag.",
        "mailerlm contacts do-not-mail ada@example.com",
    )

    campaigns = sub.add_parser("campaigns", help="campaigns: new, list, check, preview")
    campaign_verbs = campaigns.add_subparsers(dest="subcommand", required=True)
    new = campaign_verbs.add_parser("new", help="create a campaign from a starter")
    new.add_argument("name")
    new.add_argument(
        "--starter", default="update", help="confirmation, update, early-access or launch"
    )
    _mutating(new)
    new.set_defaults(api="campaigns_create")
    clikit.describe(
        new,
        "Create a campaign in the database from a starter (edit it in the web app). Never overwrites an existing campaign.",
        "mailerlm campaigns new october-update --starter update",
    )
    listing = campaign_verbs.add_parser("list", help="campaign files and starters")
    listing.set_defaults(api="campaigns_list")
    clikit.describe(
        listing,
        "List your campaigns and the starters you can create one from.",
        "mailerlm campaigns list",
    )
    check = campaign_verbs.add_parser(
        "check", help="render a campaign and count who it would reach"
    )
    check.add_argument("name")
    check.set_defaults(api="campaigns_check")
    clikit.describe(
        check,
        "Render the campaign, check its images against the size budget and count who it would reach. Sends nothing.",
        "mailerlm campaigns check october-update",
    )
    preview = campaign_verbs.add_parser(
        "preview", help="write HTML and text previews to open in a browser"
    )
    preview.add_argument("name")
    preview.add_argument(
        "--first-name", dest="first_name", default="Friend", help="name used for {{ first_name }}"
    )
    preview.add_argument(
        "--preferred-name",
        dest="preferred_name",
        help="name used for {{ preferred_name }} (default: the --first-name value)",
    )
    preview.add_argument(
        "--list",
        dest="list_name",
        help="render it as a reader of this list gets it (default: the campaign's first list)",
    )
    preview.set_defaults(api="campaigns_preview")
    clikit.describe(
        preview,
        "Write previews/<name>.html and .txt in the workspace with images inline, to open in a browser.",
        "mailerlm campaigns preview october-update",
    )

    sends = sub.add_parser("sends", help="send campaigns and follow what happened to them")
    send_verbs = sends.add_subparsers(dest="subcommand", required=True)
    plan = send_verbs.add_parser(
        "plan", help="show what a real send would do, with its fingerprint"
    )
    plan.add_argument("name")
    _send_lists(plan)
    plan.set_defaults(api="sends_plan")
    clikit.describe(
        plan,
        "Show who a real send would reach, list by list (recipients, footer, 'Testing: ', skipped), any person who would be mailed through two chosen lists (that stops the send), and the fingerprint 'sends start' needs with the same lists. Sends nothing.",
        "mailerlm sends plan october-update",
    )
    test = send_verbs.add_parser(
        "test", help="send the campaign once, to your own reply-to address"
    )
    test.add_argument("name")
    _send_lists(test)
    test.set_defaults(api="sends_test")
    clikit.describe(
        test,
        "Mail the campaign once to your reply-to address. This uses Resend (a send, free on your plan).",
        "mailerlm sends test october-update",
    )
    start = send_verbs.add_parser(
        "start", help="send the campaign to its audience (the owner's act)"
    )
    start.add_argument("name")
    _send_lists(start)
    start.add_argument("--fingerprint", required=True, help="the fingerprint shown by 'sends plan'")
    start.set_defaults(api="sends_start")
    clikit.describe(
        start,
        "Mail the campaign to everyone the plan listed. It repeats the plan and stops if anything changed since you looked. Only the owner runs this.",
        "mailerlm sends start october-update --list friends --fingerprint 1a2b3c4d5e6f7a8b",
    )
    resume = send_verbs.add_parser("resume", help="finish an interrupted send")
    resume.add_argument("send", type=int)
    resume.set_defaults(api="sends_resume")
    clikit.describe(
        resume,
        "Mail the people an interrupted send had not reached yet; nobody is mailed twice.",
        "mailerlm sends resume 3",
    )
    listing = send_verbs.add_parser("list", help="recent sends and how they ended")
    listing.add_argument("--campaign")
    listing.set_defaults(api="sends_list")
    clikit.describe(
        listing,
        "List recent sends with counts by status.",
        "mailerlm sends list --campaign october-update",
    )
    poll = send_verbs.add_parser("poll", help="ask Resend how sent messages ended up")
    poll.add_argument("--send", type=int)
    poll.set_defaults(api="sends_poll")
    clikit.describe(
        poll,
        "Update delivered, bounced and complained statuses; a bounce or complaint blocks that person. Needs a Resend key that can read status.",
        "mailerlm sends poll",
    )

    draft_command = sub.add_parser("drafts", help="one editable draft of a campaign per person")
    draft_verbs = draft_command.add_subparsers(dest="subcommand", required=True)
    draft_make = draft_verbs.add_parser("make", help="make or remake a draft for each person")
    draft_make.add_argument("name")
    _mutating(draft_make)
    draft_make.set_defaults(api="drafts_make")
    clikit.describe(
        draft_make,
        "Make one draft of the campaign per person it reaches now, their values filled in. Remaking adds drafts for new people, follows the campaign in drafts you have not edited, keeps edited drafts, and removes unedited drafts of people who left. Sends nothing.",
        "mailerlm drafts make thanksgiving-2026-friends",
    )
    draft_list = draft_verbs.add_parser("list", help="the campaign's drafts and their state")
    draft_list.add_argument("name")
    draft_list.set_defaults(api="drafts_list")
    clikit.describe(
        draft_list,
        "List each draft: who (address masked), edited or not, and ready, sent (when, and by which path) or unreachable. Sending is on the Drafts page.",
        "mailerlm drafts list thanksgiving-2026-friends",
    )
    draft_show = draft_verbs.add_parser("show", help="one person's draft")
    draft_show.add_argument("name")
    draft_show.add_argument("contact", type=int, help="the contact number (#id in 'drafts list')")
    draft_show.set_defaults(api="drafts_show")
    clikit.describe(
        draft_show,
        "Show one person's draft: subject, edited, state; the letter itself with --json.",
        "mailerlm drafts show thanksgiving-2026-friends 7",
    )

    replies = sub.add_parser("replies", help="read replies to your list mail")
    reply_verbs = replies.add_subparsers(dest="subcommand", required=True)
    reply_check = reply_verbs.add_parser("check", help="read new replies and apply the safe ones")
    reply_check.set_defaults(api="replies_check")
    clikit.describe(
        reply_check,
        "Read replies from people you have mailed, classify each with Jev (a TypeSafe call), unsubscribe or confirm only when it is sure, and queue the rest for you.",
        "mailerlm replies check",
    )
    reply_list = reply_verbs.add_parser("list", help="replies and what was decided")
    reply_list.add_argument(
        "--queue-only",
        action="store_true",
        dest="queue_only",
        help="only the replies waiting for you",
    )
    reply_list.set_defaults(api="replies_list")
    clikit.describe(
        reply_list,
        "List replies with Jev's pick and the decision.",
        "mailerlm replies list --queue-only",
    )
    reply_decide = reply_verbs.add_parser("decide", help="settle a reply yourself")
    reply_decide.add_argument("reply", type=int)
    reply_decide.add_argument("decision", choices=["confirmed", "unsubscribed", "ignored"])
    reply_decide.set_defaults(api="replies_decide")
    clikit.describe(
        reply_decide,
        "Confirm or unsubscribe the person, or ignore the reply.",
        "mailerlm replies decide 4 confirmed",
    )
    reply_undo = reply_verbs.add_parser("undo", help="undo an automatic decision")
    reply_undo.add_argument("reply", type=int)
    reply_undo.set_defaults(api="replies_undo")
    clikit.describe(
        reply_undo,
        "Put the person's memberships back as they were and return the reply to the queue.",
        "mailerlm replies undo 4",
    )

    help_verb = sub.add_parser("help", help="list verbs, or show one verb's options")
    help_verb.add_argument("topic", nargs="*", help="a verb, optionally with its subverb")
    help_verb.set_defaults(api="help")
    clikit.describe(
        help_verb, "List every verb, or show one verb's options.", "mailerlm help setup"
    )

    serve = sub.add_parser("serve", help="run the web app on this Mac")
    serve.add_argument(
        "--port", type=int, default=server.PORT, help=f"loopback port (default {server.PORT})"
    )
    serve.set_defaults(api="serve")
    clikit.describe(
        serve,
        "Run the web app on 127.0.0.1 (the launch agent runs this; tailscale serve fronts it).",
        "mailerlm serve --port 8782",
    )

    completion = sub.add_parser("completion", help="print the bash completion script")
    completion.add_argument("shell", nargs="?", default="bash", choices=["bash"])
    completion.set_defaults(api="completion")
    clikit.describe(
        completion,
        "Print a bash completion script generated from the parser.",
        'eval "$(mailerlm completion bash)"',
    )
    return parser


def format_board(board: dict, as_json: bool) -> str:
    if as_json:
        return json.dumps(board, indent=2, ensure_ascii=False)
    lines: list[str] = []
    for key, value in board.items():
        if key == "checks":
            lines.append("checks:")
            for check in value:
                fix = f"  fix: {check['fix']}" if check.get("fix") else ""
                lines.append(f"  {check['state']:<7} {check['name']}: {check['detail']}{fix}")
        elif key == "lists" and value and isinstance(value[0], dict):
            lines.append("lists:")
            for item in value:
                mark = " [test list]" if item.get("test") else ""
                if item.get("show_footer") is False:
                    mark += " [footer hidden]"
                lines.append(
                    f"  {item['name']}: {item['members']} members {item['by_status']}{mark}"
                )
        elif key == "contacts":
            lines.append("contacts:")
            for row in value:
                flag = " DO-NOT-MAIL" if row["do_not_mail"] else ""
                linkedin = " linkedin" if row["linkedin_url"] else ""
                how = f" confirmed={row['confirmed']}" if row.get("confirmed") else ""
                called = (
                    f" (preferred {row['preferred_name']!r})" if row.get("preferred_name") else ""
                )
                lines.append(
                    f"  #{row['id']} {row['email']} {row['name']!r}{called} lists={row['lists']}{how} tags={row['tags']}{linkedin}{flag}"
                )
        elif key in ("would_confirm", "confirmed", "declined") and isinstance(value, list):
            lines.append(f"{key}: {len(value)}")
            for item in value:
                why = f": {item['why']}" if item.get("why") else ""
                lines.append(f"  #{item['contact']} {item['name']!r} {item['email']}{why}")
        elif key == "history":
            lines.append("history:" if value else "history: none")
            for item in value:
                lines.append(
                    f"  {item['at']} {item['list']}: {item['previous']} -> {item['new']} ({item['how']})"
                )
        elif key in ("html", "text") and not str(value).startswith("/"):
            lines.append(f"{key}: ({len(str(value))} characters; use --json to read it)")
        elif key == "replies":
            lines.append("replies:")
            for item in value:
                lines.append(
                    f"  #{item['id']} {item['decision']} ({item['decided_by']}) {item['choice'] or '-'} {item['subject'][:50]}"
                )
        elif key == "drafts" and isinstance(value, list):
            lines.append("drafts:" if value else "drafts: none")
            for item in value:
                sent = (
                    f" sent {item['sent']['at']} via {item['sent']['via']}" if item["sent"] else ""
                )
                why = f" ({item['why']})" if item["why"] else ""
                edited = " edited" if item["edited"] else ""
                lines.append(
                    f"  #{item['contact']} {item['name']!r} {item['email']} {item['state']}{edited}{sent}{why}"
                )
        elif key in (
            "added",
            "updated",
            "kept_edited",
            "removed",
            "kept_not_in_audience",
            "not_made",
        ) and isinstance(value, list):
            lines.append(f"{key}: {len(value)}")
            for item in value:
                why = f": {item['why']}" if item.get("why") else ""
                lines.append(f"  #{item['contact']} {item['name']!r} {item['email']}{why}")
        elif key == "draft":
            for field in ("contact", "name", "email", "subject", "edited", "state", "why", "sent"):
                lines.append(f"{field}: {value[field]}")
            lines.append(f"body: ({len(value['body'])} characters; use --json to read it)")
        elif key == "sends":
            lines.append("sends:")
            for item in value:
                lines.append(
                    f"  #{item['id']} {item['campaign_name']} ({item['kind']}, {item['status']}) {item['counts']}"
                )
        elif key in ("problems", "blockers", "notes"):
            lines.append(f"{key}:" if value else f"{key}: none")
            lines += [f"  {item}" for item in value]
        elif key == "by_list":
            lines.append("by_list:")
            for item in value:
                prefix = ", 'Testing: ' prefix" if item["testing_prefix"] else ""
                skipped = f", skipped {item['skipped']}" if item["skipped"] else ""
                lines.append(
                    f"  {item['list']}: {item['recipients']} recipient{'' if item['recipients'] == 1 else 's'}, footer {item['footer']}"
                    f"{prefix}, from {item['from']}, subject {item['subject']!r}{skipped}"
                )
        elif key == "env_diff":
            if value:
                lines.append("env_diff:")
                lines += [f"  {line}" for line in value]
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    paths.load_env()
    parser = build_parser()
    rest, flags, _ = clikit.extract_options(
        list(sys.argv[1:] if argv is None else argv), ("--json",)
    )
    try:
        args = parser.parse_args(rest)
    except SystemExit as error:
        return int(error.code or 0)
    as_json = "--json" in flags
    if args.command is None:
        print(clikit.help_text(parser, "mailerlm", None, HELP_FOOTER)[0], end="")
        return 0
    if args.command == "gmail" and getattr(args, "api", None) is None:
        print(clikit.help_text(parser, "mailerlm", ["gmail"], HELP_FOOTER)[0], end="")
        return 0
    try:
        if args.api == "help":
            text, code = clikit.help_text(parser, "mailerlm", args.topic, HELP_FOOTER)
            print(text, end="")
            return code
        if args.api == "serve":
            started = datetime.now().astimezone().isoformat(timespec="seconds")
            httpd = server.make_server("127.0.0.1", args.port, started)
            print(f"mailerlm web app on http://127.0.0.1:{args.port}/ (Ctrl-C to stop)", flush=True)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass
            return 0
        if args.api == "completion":
            print(clikit.bash_completion(parser, "mailerlm", ["mailerlm"], ("--json",)), end="")
            return 0
        function = getattr(api, args.api)
        kwargs = {
            name: tuple(value) if isinstance(value, list) else value
            for name, value in vars(args).items()
            if name in inspect.signature(function).parameters
        }
        board = function(**kwargs)
    except NotConfiguredError as error:
        print(f"skipped: {error}")  # a missing key or token is not a failure of a scheduled run
        return 0
    except MailerlmError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    try:
        print(format_board(board, as_json))
    except BrokenPipeError:
        return 0
    return 0 if board.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
