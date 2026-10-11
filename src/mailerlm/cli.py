"""The mailerlm command line: one verb per typed-API function (metalm cli.md).

A logic-free surface over `mailerlm.api`; the parser, help, completion and global options come
from `corpuslm.clikit`.

Never imports: nothing below it may import it (enforced by the import contract "surfaces on top"
in pyproject.toml).
"""

from __future__ import annotations

import json
import sys

from corpuslm import clikit

from mailerlm import api, paths
from mailerlm.errors import MailerlmError

HELP_FOOTER = (
    "Global option: --json (the board as JSON) anywhere on the line.",
    "Keys and the workspace pointer live in .env (see .env.example); 'mailerlm setup' writes the pointer.",
    "Every verb prints a board; contacts and messages stay in the workspace, never on stdout.",
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

    help_verb = sub.add_parser("help", help="list verbs, or show one verb's options")
    help_verb.add_argument("topic", nargs="*", help="a verb, optionally with its subverb")
    help_verb.set_defaults(api="help")
    clikit.describe(
        help_verb, "List every verb, or show one verb's options.", "mailerlm help setup"
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
        if args.api == "completion":
            print(clikit.bash_completion(parser, "mailerlm", ["mailerlm"], ("--json",)), end="")
            return 0
        kwargs = {"dry_run": args.dry_run} if args.api == "setup" else {}
        board = getattr(api, args.api)(**kwargs)
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
