"""Plain-text helpers for reading a reply: what the person wrote, without the quoted letter below it."""

from __future__ import annotations

import re

_REPLY_MARKERS = (
    re.compile(r"^\s*On .{5,200}wrote:\s*$", re.I),
    re.compile(r"^\s*-{2,}\s*(Original Message|Forwarded message)", re.I),
    re.compile(r"^\s*From:\s.+", re.I),
    re.compile(r"^\s*_{5,}\s*$"),
)
MAX_CHARACTERS = 2000


def strip_quoted(body: str) -> str:
    """Cut the quoted original and the signature-ish tail; keep what the sender typed, at most 2,000 characters."""
    kept: list[str] = []
    lines = body.replace("\r\n", "\n").split("\n")
    for index, line in enumerate(lines):
        if line.lstrip().startswith(">"):
            break
        if any(marker.match(line) for marker in _REPLY_MARKERS):
            break
        if (
            re.match(r"^\s*On .{5,200}$", line)
            and index + 1 < len(lines)
            and re.search(r"wrote:\s*$", lines[index + 1])
        ):
            break
        kept.append(line)
    return "\n".join(kept).strip()[:MAX_CHARACTERS]


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()
