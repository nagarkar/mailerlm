"""store: created only by initialize, upgraded on open, a newer file declined, tables STRICT."""

import sqlite3

import pytest

from mailerlm import store
from mailerlm.errors import MailerlmError


def test_opening_a_missing_database_names_setup_and_creates_nothing(tmp_path):
    path = tmp_path / "x.sqlite"
    with pytest.raises(MailerlmError, match="mailerlm setup"):
        store.open_store(path)
    assert not path.exists()


def test_initialize_creates_and_a_second_run_changes_nothing(tmp_path):
    path = tmp_path / "x.sqlite"
    store.initialize(path).close()
    first = store.version(path)
    store.initialize(path).close()
    assert first == len(store.MIGRATIONS) == store.version(path)


def test_an_older_database_is_upgraded_on_open(tmp_path):
    path = tmp_path / "x.sqlite"
    one = ["CREATE TABLE a (id INTEGER PRIMARY KEY) STRICT"]
    two = [*one, "CREATE TABLE b (id INTEGER PRIMARY KEY) STRICT"]
    store.initialize(path, one).close()
    store.open_store(path, two).close()
    assert store.version(path) == 2


def test_a_database_from_newer_code_is_declined(tmp_path):
    path = tmp_path / "x.sqlite"
    store.initialize(
        path, ["CREATE TABLE a (id INTEGER) STRICT", "CREATE TABLE b (id INTEGER) STRICT"]
    ).close()
    with pytest.raises(MailerlmError, match="version 2"):
        store.open_store(path, ["CREATE TABLE a (id INTEGER) STRICT"])


def test_a_failed_migration_rolls_back(tmp_path):
    path = tmp_path / "x.sqlite"
    with pytest.raises(sqlite3.Error):
        store.initialize(path, ["CREATE TABLE a (id INTEGER) STRICT; CREATE TABLE a (id INTEGER)"])
    assert store.version(path) == 0


def test_foreign_keys_are_on_and_tables_are_strict(tmp_path):
    connection = store.initialize(tmp_path / "x.sqlite")
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.Error):
        connection.execute("INSERT INTO store_info VALUES ('k', x'00')")
