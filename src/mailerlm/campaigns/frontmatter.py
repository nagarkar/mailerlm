"""A campaign file is `---` YAML front matter, then a Markdown body."""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

KNOWN = {"subject", "lists", "tags", "kind", "preview_text", "track_clicks", "track_opens"}
KINDS = ("campaign", "confirmation")


class CampaignFileError(ValueError):
    """The campaign file is malformed; the message names the field."""


@dataclass(frozen=True, slots=True)
class Campaign:
    subject: str
    body: str
    lists: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    kind: str = "campaign"
    preview_text: str = ""
    track_clicks: bool = False
    track_opens: bool = False


def _strings(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise CampaignFileError(f"front matter '{name}' must be a list of names")
    return tuple(value)


def parse(text: str) -> Campaign:
    if not text.startswith("---"):
        raise CampaignFileError("the file must start with '---' front matter naming a subject")
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        raise CampaignFileError("front matter is not closed with a '---' line")
    try:
        meta = yaml.safe_load(parts[0][3:]) or {}
    except yaml.YAMLError as error:
        raise CampaignFileError(
            f"front matter is not valid YAML ({type(error).__name__})"
        ) from error
    if not isinstance(meta, dict):
        raise CampaignFileError("front matter must be key: value lines")
    unknown = sorted(set(meta) - KNOWN)
    if unknown:
        raise CampaignFileError(
            f"unknown front matter key '{unknown[0]}'; known: {', '.join(sorted(KNOWN))}"
        )
    subject = meta.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        raise CampaignFileError("front matter needs a 'subject'")
    kind = meta.get("kind", "campaign")
    if kind not in KINDS:
        raise CampaignFileError(f"'kind' must be one of {', '.join(KINDS)}")
    for flag in ("track_clicks", "track_opens"):
        if flag in meta and not isinstance(meta[flag], bool):
            raise CampaignFileError(f"'{flag}' must be true or false")
    return Campaign(
        subject=subject.strip(),
        body=parts[1].split("\n", 1)[1] if "\n" in parts[1] else "",
        lists=_strings(meta.get("lists"), "lists"),
        tags=_strings(meta.get("tags"), "tags"),
        kind=kind,
        preview_text=str(meta.get("preview_text", "")),
        track_clicks=bool(meta.get("track_clicks", False)),
        track_opens=bool(meta.get("track_opens", False)),
    )
