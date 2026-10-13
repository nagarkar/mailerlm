"""Config: the strict reader of `config.toml` (desired behaviour across runs, never state).

An absent file means every default; a present file is rejected by name for an unknown section
or key, a wrong type, a non-positive cap, or thresholds out of order. Everything identifying the
owner (book title, sender, footer address) lives in the database, edited on the review page; this
file holds behaviour only (caps and classifier thresholds).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mailerlm.errors import MailerlmError

DEFAULTS: dict[str, dict[str, Any]] = {
    "caps": {
        "max_recipients_per_send": 50,
        "max_sends_per_day": 100,
        "max_image_kilobytes_per_campaign": 100,
    },
    "classifier": {
        "model": "jev-latest",
        "unsubscribe_min_probability": 0.80,
        "confirm_min_probability": 0.95,
    },
}


OPTIONAL_SECTIONS: dict[str, dict[str, Any]] = {
    # A section that is absent turns its purpose off; when present, `model` is required.
    "template_editor": {
        "model": "",
        "max_usd_per_day": 1.0,
        "max_request_characters": 2000,
        "max_turns": 40,
    },
}


@dataclass(frozen=True, slots=True)
class Settings:
    values: dict[str, dict[str, Any]]

    def section(self, name: str) -> dict[str, Any] | None:
        """An optional section's values, or None when the owner left it out (the purpose is off)."""
        return self.values.get(name)

    def get(self, section: str, key: str) -> Any:
        return self.values[section][key]


def load(path: Path) -> Settings:
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            raw = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as error:
            raise MailerlmError(f"{path.name} is not valid TOML: {error}. Fix the file") from error
    merged = {section: dict(keys) for section, keys in DEFAULTS.items()}
    known = {**DEFAULTS, **OPTIONAL_SECTIONS}
    for section, keys in raw.items():
        if section not in known:
            raise MailerlmError(f"unknown config section [{section}] in {path.name}. Remove it")
        if not isinstance(keys, dict):
            raise MailerlmError(f"[{section}] in {path.name} must be a table")
        if section in OPTIONAL_SECTIONS:
            merged[section] = dict(OPTIONAL_SECTIONS[section])
        for key, value in keys.items():
            if key not in known[section]:
                raise MailerlmError(f"unknown config key {section}.{key} in {path.name}. Remove it")
            expected = type(known[section][key])
            if expected is float and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            if type(value) is not expected:
                raise MailerlmError(f"{section}.{key} must be {expected.__name__}")
            merged[section][key] = value
    editor = merged.get("template_editor")
    if editor is not None:
        if not editor["model"] or "/" not in editor["model"]:
            raise MailerlmError(
                "template_editor.model must name a model with its vendor, like gemini/gemini-3.6-flash"
            )
        for key in ("max_usd_per_day", "max_request_characters", "max_turns"):
            if editor[key] <= 0:
                raise MailerlmError(f"template_editor.{key} must be positive")
    caps = merged["caps"]
    for key, value in caps.items():
        if value <= 0:
            raise MailerlmError(f"caps.{key} must be positive")
    classifier = merged["classifier"]
    for key in ("unsubscribe_min_probability", "confirm_min_probability"):
        if not 0 < classifier[key] <= 1:
            raise MailerlmError(f"classifier.{key} must be between 0 and 1")
    if classifier["confirm_min_probability"] < classifier["unsubscribe_min_probability"]:
        raise MailerlmError(
            "classifier.confirm_min_probability must not be below unsubscribe_min_probability "
            "(a wrong confirmation costs more than a wrong unsubscribe)"
        )
    return Settings(merged)
