"""The deterministic gate between a model's template edit and the owner: nothing that fails it is shown as a result.

An email frame is plain, inline-styled HTML with a small set of placeholders. The guard accepts only that:
required placeholders kept, only known placeholders and `if` blocks, no script, frames, forms, event handlers,
remote images, remote styles, fonts or tracking pixels, and it must render. Ordinary HTML comments are inert and
ignored; conditional comments are scanned. It never reads what the template says; it checks its shape. Model replies are data, so this runs on every reply before anything is stored.
"""

from __future__ import annotations

import re

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

MAX_CHARACTERS = 60_000
ALLOWED_NAMES = (
    "subject",
    "preview_text",
    "body_html",
    "body_text",
    "postal_address",
    "unsubscribe_line",
    "sender_name",
)
REQUIRED_HTML = (
    r"\{\{\s*body_html\s*\|\s*safe\s*\}\}",
    r"\{\{\s*unsubscribe_line\s*\}\}",
    r"\{\{\s*postal_address\s*\}\}",
)
REQUIRED_TEXT = (
    r"\{\{\s*body_text\s*\}\}",
    r"\{\{\s*unsubscribe_line\s*\}\}",
    r"\{\{\s*postal_address\s*\}\}",
)
FORBIDDEN_HTML = (
    (
        r"<\s*(script|iframe|object|embed|form|input|button|select|textarea|link|base|svg|math|frame|frameset|applet)\b",
        "a tag that email must not contain",
    ),
    (r"<\s*meta\b[^>]*http-equiv", "a meta refresh"),
    (r"\bon[a-z]+\s*=", "an event-handler attribute"),
    (r"(javascript|vbscript|data)\s*:", "a javascript:, vbscript: or data: address"),
    (r"@import", "a stylesheet import"),
    (r"url\(\s*['\"]?\s*(https?:)?//", "a remote stylesheet resource"),
    (r"<\s*img\b[^>]*\bsrc\s*=\s*['\"]?\s*(https?:)?//", "a remote image or tracking pixel"),
    (r"\bsrc\s*=\s*['\"]?\s*(https?:)?//", "a remote source"),
    (r"<\s*img\b[^>]*\bwidth\s*=\s*['\"]?[01]['\"]?", "a one-pixel image"),
)
_EXPRESSION = re.compile(r"\{\{(.*?)\}\}", re.S)
_STATEMENT = re.compile(r"\{%-?\s*(.*?)\s*-?%\}", re.S)
_SAFE_EXPRESSION = re.compile(r"^\s*(\w+)(\s*\|\s*(safe|e|upper|lower|title))?\s*$")
_SAMPLE = {name: "x" for name in ALLOWED_NAMES}
_COMMENT = re.compile(r"<!--(?!\[if).*?-->", re.S)


def check(html: str, text: str) -> list[str]:
    """Why this frame is not acceptable; an empty list means it is."""
    problems: list[str] = []
    for label, body, required in (("HTML", html, REQUIRED_HTML), ("text", text, REQUIRED_TEXT)):
        if not isinstance(body, str) or not body.strip():
            problems.append(f"the {label} frame is empty")
            continue
        if len(body) > MAX_CHARACTERS:
            problems.append(f"the {label} frame is over {MAX_CHARACTERS} characters")
        for pattern in required:
            if not re.search(pattern, body):
                problems.append(
                    f"the {label} frame lost a required placeholder ({pattern.replace(chr(92), '')})"
                )
        for match in _EXPRESSION.finditer(body):
            found = _SAFE_EXPRESSION.match(match.group(1))
            if not found or found.group(1) not in ALLOWED_NAMES:
                problems.append(
                    f"the {label} frame uses a placeholder that is not allowed: {{{{{match.group(1).strip()[:40]}}}}}"
                )
        for match in _STATEMENT.finditer(body):
            words = match.group(1).split()
            if (
                not words
                or words[0] not in ("if", "elif", "else", "endif")
                or any(
                    word.isidentifier()
                    and word not in ALLOWED_NAMES
                    and word not in ("if", "elif", "else", "endif", "not", "and", "or")
                    for word in words
                )
            ):
                problems.append(
                    f"the {label} frame uses a block that is not allowed: {{% {match.group(1)[:40]} %}}"
                )
    if isinstance(html, str):
        scanned = _COMMENT.sub(
            "", html
        )  # an ordinary comment is inert; a conditional comment is scanned
        for pattern, why in FORBIDDEN_HTML:
            if re.search(pattern, scanned, re.I):
                problems.append(f"the HTML frame contains {why}")
    if not problems:
        try:
            environment = SandboxedEnvironment(undefined=StrictUndefined)
            environment.from_string(html).render(**_SAMPLE)
            environment.from_string(text).render(**_SAMPLE)
        except Exception as error:  # a template that does not render
            problems.append(f"the frame does not render ({type(error).__name__})")
    return sorted(set(problems))
