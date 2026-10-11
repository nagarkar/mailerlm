"""The mailerlm command line: one verb per typed-API function (metalm cli.md)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mailerlm.inbox import gmail


def load_env(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines from a .env file; comments and blanks are skipped."""
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("\"'")
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mailerlm")
    groups = p.add_subparsers(dest="group", required=True)
    g = groups.add_parser("gmail", help="Gmail connection")
    verbs = g.add_subparsers(dest="verb", required=True)
    verbs.add_parser("connect", help="open a browser to grant readonly access and store the token")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = {**load_env(Path.cwd() / ".env"), **os.environ}
    workspace = Path(env.get("MAILERLM_WORKSPACE", "~/.mailerlm")).expanduser()
    if (args.group, args.verb) == ("gmail", "connect"):
        try:
            path = gmail.connect(workspace, env)
        except gmail.GmailNotConfigured as e:
            print(f"mailerlm: {e}", file=sys.stderr)
            return 2
        print(f"token written: {path}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
