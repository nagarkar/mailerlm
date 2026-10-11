"""Turn pasted text or a CSV into contact candidates; pure, no database.

A line may be `email`, `Name <email>`, `Name: email`, or a LinkedIn profile URL (tracking query
removed). A CSV with a header naming `email` or `linkedin_url` may also carry `name` and
`tags` (semicolon separated). A line with neither an email nor a LinkedIn URL is rejected by
line number, never echoed.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
LINKEDIN = re.compile(r"https?://(?:[a-z]+\.)?linkedin\.com/in/[^\s?#]+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Candidate:
    email: str | None
    name: str = ""
    linkedin_url: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


def normalize_email(text: str) -> str:
    return text.strip().strip(".,;:<>()").lower()


def _clean_name(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().strip(":<>-,.\"'")).strip()


def _parse_line(line: str) -> Candidate | None:
    linkedin = LINKEDIN.search(line)
    email = EMAIL.search(line)
    if not email and not linkedin:
        return None
    name = ""
    if email:
        name = _clean_name(line[: email.start()])
    return Candidate(
        email=normalize_email(email.group()) if email else None,
        name=name,
        linkedin_url=linkedin.group().rstrip("/") if linkedin else None,
    )


def _parse_csv(text: str) -> tuple[list[Candidate], list[int]]:
    candidates: list[Candidate] = []
    rejected: list[int] = []
    for number, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        row = {(key or "").strip().lower(): (value or "").strip() for key, value in row.items()}
        email = EMAIL.search(row.get("email", ""))
        linkedin = LINKEDIN.search(row.get("linkedin_url", ""))
        if not email and not linkedin:
            rejected.append(number)
            continue
        tags = tuple(t.strip() for t in row.get("tags", "").split(";") if t.strip())
        candidates.append(
            Candidate(
                email=normalize_email(email.group()) if email else None,
                name=_clean_name(row.get("name", "")),
                linkedin_url=linkedin.group().rstrip("/") if linkedin else None,
                tags=tags,
            )
        )
    return candidates, rejected


def parse(text: str) -> tuple[list[Candidate], list[int]]:
    """The candidates, and the line numbers that held neither an email nor a LinkedIn URL."""
    first = text.lstrip().splitlines()[0].lower() if text.strip() else ""
    if "," in first and ("email" in first or "linkedin_url" in first) and "@" not in first:
        return _parse_csv(text)
    candidates: list[Candidate] = []
    rejected: list[int] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parsed = _parse_line(line)
        if parsed is None:
            rejected.append(number)
        else:
            candidates.append(parsed)
    return candidates, rejected
