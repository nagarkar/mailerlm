"""Config: the strict reader of `config.toml` (desired behaviour across runs, never state).

An absent file means every default; a present file is rejected by name for an unknown section
or key, a wrong type, a non-positive cap, or thresholds out of order. Everything identifying the
owner lives in the gitignored `config.toml` (see `config.example.toml`).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mailerlm.errors import MailerlmError

DEFAULTS: dict[str, dict[str, Any]] = {
    "book": {"title": ""},
    "sender": {"name": "", "address": "", "reply_to": "", "postal_address": ""},
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


@dataclass(frozen=True, slots=True)
class Settings:
    values: dict[str, dict[str, Any]]

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
    for section, keys in raw.items():
        if section not in DEFAULTS:
            raise MailerlmError(f"unknown config section [{section}] in {path.name}. Remove it")
        if not isinstance(keys, dict):
            raise MailerlmError(f"[{section}] in {path.name} must be a table")
        for key, value in keys.items():
            if key not in DEFAULTS[section]:
                raise MailerlmError(f"unknown config key {section}.{key} in {path.name}. Remove it")
            expected = type(DEFAULTS[section][key])
            if expected is float and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            if type(value) is not expected:
                raise MailerlmError(f"{section}.{key} must be {expected.__name__}")
            merged[section][key] = value
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
