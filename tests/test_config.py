"""config: strict; unknown names, wrong types, bad caps and out-of-order thresholds are rejected."""

import pytest

from mailerlm import config
from mailerlm.errors import MailerlmError


def write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_an_absent_file_means_defaults(tmp_path):
    settings = config.load(tmp_path / "none.toml")
    assert settings.get("classifier", "confirm_min_probability") == 0.95


@pytest.mark.parametrize(
    "text, message",
    [
        ("[nope]\nx = 1", "unknown config section"),
        ("[caps]\nnope = 1", "unknown config key caps.nope"),
        ('[caps]\nmax_sends_per_day = "many"', "must be int"),
        ("[caps]\nmax_sends_per_day = 0", "must be positive"),
        (
            "[classifier]\nunsubscribe_min_probability = 0.9\nconfirm_min_probability = 0.8",
            "must not be below",
        ),
        ("not toml [", "not valid TOML"),
    ],
)
def test_bad_config_is_rejected_by_name(tmp_path, text, message):
    with pytest.raises(MailerlmError, match=message):
        config.load(write(tmp_path, text))


def test_a_valid_file_overrides_defaults(tmp_path):
    settings = config.load(write(tmp_path, '[book]\ntitle = "T"\n[caps]\nmax_sends_per_day = 5'))
    assert settings.get("book", "title") == "T"
    assert settings.get("caps", "max_sends_per_day") == 5
