"""Paths: where mailerlm's files live (metalm coding.md, "Where .env lives").

- **Project config**: `config.toml` in the checkout, gitignored (it names the owner); the
  checked-in `config.example.toml` shows its shape.
- **Workspace**: the SQLite store, campaign files, images and token files. Outside git,
  located by `MAILERLM_WORKSPACE` in `.env`, because a file inside the workspace cannot say
  where the workspace is. Only `mailerlm setup` creates it and writes the pointer.
- **Secrets**: API keys and the Gmail client in `.env`, gitignored.

`.env` lives in the checkout unless `MAILERLM_ENV` names another file; that variable is set in
the process environment, never inside a `.env`.
"""

from __future__ import annotations

import os
from pathlib import Path

from mailerlm.errors import MailerlmError

CONFIG_FILENAME = "config.toml"
ENV_FILENAME = ".env"
ENV_OVERRIDE = "MAILERLM_ENV"
WORKSPACE_ENV = "MAILERLM_WORKSPACE"
DEFAULT_WORKSPACE = "~/.mailerlm"
DATABASE_FILENAME = "mailerlm.sqlite"
BLOCK_BEGIN = "# mailerlm:begin"
BLOCK_END = "# mailerlm:end"


def project_dir() -> Path:
    """The directory holding pyproject.toml (src/mailerlm/paths.py is two levels below it)."""
    return Path(__file__).resolve().parent.parent.parent


def env_path() -> Path:
    override = os.environ.get(ENV_OVERRIDE, "")
    return Path(override).expanduser() if override else project_dir() / ENV_FILENAME


def config_path() -> Path:
    return project_dir() / CONFIG_FILENAME


def read_env_file_lines(lines: list[str]) -> dict[str, str]:
    """KEY=VALUE lines as data; comments and blank lines are skipped."""
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def read_env_file(path: Path) -> dict[str, str]:
    return read_env_file_lines(path.read_text().splitlines()) if path.exists() else {}


def load_env() -> None:
    """Put the .env values in the process environment; a real environment variable wins."""
    for key, value in read_env_file(env_path()).items():
        os.environ.setdefault(key, value)


def workspace_path() -> Path:
    pointer = os.environ.get(WORKSPACE_ENV, "")
    if not pointer:
        raise MailerlmError(f"{WORKSPACE_ENV} is not set. Run: mailerlm setup")
    return Path(pointer).expanduser()


def database_path(workspace: Path) -> Path:
    return workspace / DATABASE_FILENAME
