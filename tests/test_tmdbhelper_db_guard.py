# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The guard adds the table a TMDbHelper update leaves out, and nothing else."""

import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from resources.lib import tmdbhelper_db_guard as guard

# The declarations as TMDbHelper 6.21.5-p3i.1 ships them, cut down to what the
# guard reads: a class attribute in one module and a module constant in another.
_TABLE_MODULE = """
from tmdbhelper.lib.query.database.table import FindQueriesDatabaseTable


class FindQueriesDatabaseTableExternalIds(FindQueriesDatabaseTable):
    table = 'external_ids'
    keys = ('id', 'tmdb_type', 'parent_tmdb_id', 'season', 'episode',
            'tmdb_id', 'imdb_id', 'tvdb_id')


class FindQueriesDatabaseExternalIds:
    external_ids_columns = {
        'id': {'data': 'TEXT PRIMARY KEY'},
        'tmdb_type': {'data': 'TEXT'},
        'parent_tmdb_id': {'data': 'INTEGER'},
        'season': {'data': 'INTEGER'},
        'episode': {'data': 'INTEGER'},
        'tmdb_id': {'data': 'INTEGER', 'indexed': True},
        'imdb_id': {'data': 'TEXT'},
        'tvdb_id': {'data': 'TEXT'},
    }

    def get_external_ids(self, tmdb_type, tmdb_id, season=None, episode=None):
        return f'{tmdb_type}.{tmdb_id}'
"""

_CORE_MODULE = """
import sqlite3

DEFAULT_TABLE = 'simplecache'
DATABASE_NAME = 'database_07'
"""

_COLUMNS = (
    "id",
    "tmdb_type",
    "parent_tmdb_id",
    "season",
    "episode",
    "tmdb_id",
    "imdb_id",
    "tvdb_id",
)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as target:
        target.write(text)


def _install(tmp_path, table_module=_TABLE_MODULE, core_module=_CORE_MODULE):
    """Lay out a TMDbHelper install and data folder; return both paths."""
    addon = str(tmp_path / "addons" / guard.TMDBHELPER_ADDON_ID)
    data = str(tmp_path / "addon_data" / guard.TMDBHELPER_ADDON_ID)
    lib = os.path.join(addon, "resources", "tmdbhelper", "lib")
    if table_module is not None:
        _write(os.path.join(lib, "query", "database", "external_ids.py"), table_module)
    if core_module is not None:
        _write(os.path.join(lib, "files", "dbdata.py"), core_module)
    os.makedirs(data, exist_ok=True)
    return addon, data


def _old_database(path, version=4, journal="DELETE"):
    """A query database as the previous TMDbHelper left it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode={}".format(journal))
    connection.execute("CREATE TABLE expiry(id TEXT PRIMARY KEY, expiry INTEGER)")
    connection.execute("INSERT INTO expiry VALUES ('trakt_stats', 1)")
    connection.execute("CREATE TABLE trakt_stats(id TEXT PRIMARY KEY)")
    connection.execute("PRAGMA user_version = {}".format(version))
    connection.commit()
    connection.close()


def _tmdbhelper_upgrade(path):
    """TMDbHelper's own upgrade to version 6, which it never commits."""
    connection = sqlite3.connect(path)
    cursor = connection.cursor()
    cursor.execute("DROP TABLE IF EXISTS trakt_stats")
    cursor.execute("DELETE FROM expiry WHERE id='trakt_stats'")
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS external_ids(id TEXT PRIMARY KEY, "
        "tmdb_type TEXT, parent_tmdb_id INTEGER, season INTEGER, episode INTEGER, "
        "tmdb_id INTEGER, imdb_id TEXT, tvdb_id TEXT)"
    )
    cursor.execute("PRAGMA user_version = 6")
    connection.close()


def _state(path):
    connection = sqlite3.connect(path)
    try:
        return {
            "version": connection.execute("PRAGMA user_version").fetchone()[0],
            "objects": {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT name, sql FROM sqlite_master WHERE name LIKE 'external%'"
                )
            },
            "columns": tuple(
                row[1] for row in connection.execute("PRAGMA table_info(external_ids)")
            ),
            "expiry": connection.execute("SELECT id FROM expiry").fetchall(),
        }
    finally:
        connection.close()


@pytest.fixture(name="stuck")
def _stuck(tmp_path):
    """An install whose database upgrade rolled itself back."""
    addon, data = _install(tmp_path)
    path = os.path.join(data, "database_07", "ItemQueries.db")
    _old_database(path)
    _tmdbhelper_upgrade(path)
    return addon, data, path


def test_the_upgrade_tmdbhelper_never_commits_leaves_the_table_out(stuck):
    _addon, _data, path = stuck
    state = _state(path)
    assert state["version"] == 4
    assert state["objects"] == {}


def test_adds_the_missing_table_with_the_declared_columns_and_index(stuck):
    addon, data, path = stuck

    assert guard.repair(addon, data) == guard.REPAIRED

    state = _state(path)
    assert state["columns"] == _COLUMNS
    assert set(state["objects"]) == {"external_ids", "external_ids_tmdb_id_x"}
    assert "id TEXT PRIMARY KEY" in state["objects"]["external_ids"]
    # The statement TMDbHelper failed on now runs.
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT OR REPLACE INTO external_ids({}) VALUES (?,?,?,?,?,?,?,?)".format(
            ", ".join(_COLUMNS)
        ),
        ("tv.2224.31.127", "tv", 2224, 31, 127, 1, None, None),
    )
    connection.commit()
    connection.close()


def test_changes_nothing_but_the_table(stuck):
    addon, data, path = stuck

    guard.repair(addon, data)

    state = _state(path)
    assert state["version"] == 4
    assert state["expiry"] == [("trakt_stats",)]


def test_a_second_check_finds_the_table_and_writes_nothing(stuck):
    addon, data, path = stuck
    guard.repair(addon, data)
    before = _state(path)

    with patch.object(guard, "_create") as create:
        assert guard.repair(addon, data) == guard.PRESENT

    create.assert_not_called()
    assert _state(path) == before


def test_tmdbhelper_from_before_the_table_is_left_alone(tmp_path):
    addon, data = _install(tmp_path, table_module=None)
    path = os.path.join(data, "database_07", "ItemQueries.db")
    _old_database(path)

    assert guard.repair(addon, data) == guard.NOT_INSTALLED

    assert _state(path)["objects"] == {}


def test_no_tmdbhelper_installed(tmp_path):
    missing = str(tmp_path / "nowhere")
    assert guard.repair(missing, missing) == guard.NOT_INSTALLED


def test_a_missing_database_is_not_created(tmp_path):
    addon, data = _install(tmp_path)

    assert guard.repair(addon, data) == guard.NO_DATABASE

    assert not os.path.exists(os.path.join(data, "database_07"))


def test_a_database_tmdbhelper_has_not_initialised_is_left_to_it(tmp_path):
    addon, data = _install(tmp_path)
    path = os.path.join(data, "database_07", "ItemQueries.db")
    _old_database(path, version=0)

    assert guard.repair(addon, data) == guard.NO_DATABASE

    assert _state(path)["objects"] == {}


def test_follows_tmdbhelpers_cache_location(tmp_path):
    addon, data = _install(tmp_path)
    elsewhere = tmp_path / "ssd"
    path = str(elsewhere / "database_07" / "ItemQueries.db")
    _old_database(path)
    _write(
        os.path.join(data, "settings.xml"),
        '<settings version="2"><setting id="cache_location">special://ssd/'
        "</setting></settings>",
    )
    asked = []

    def translate(kodi_path):
        asked.append(kodi_path)
        return kodi_path.replace("special://ssd/", str(elsewhere) + os.sep)

    assert guard.repair(addon, data, translate) == guard.REPAIRED

    assert asked == ["special://ssd/database_07"]
    assert _state(path)["columns"] == _COLUMNS


_SETTING_AT_DEFAULT = (
    '<settings version="2"><setting id="cache_location" default="true" /></settings>'
)
_SETTING_FROM_AN_ENTITY = (
    '<!DOCTYPE s [<!ENTITY a "b">]><settings><setting id="cache_location">'
    "&a;</setting></settings>"
)


@pytest.mark.parametrize(
    "settings",
    [
        _SETTING_AT_DEFAULT,
        '<settings version="2"><setting id="other">x</setting></settings>',
        "<settings",
        _SETTING_FROM_AN_ENTITY,
    ],
)
def test_default_location_when_the_setting_is_empty_or_unreadable(stuck, settings):
    addon, data, path = stuck
    _write(os.path.join(data, "settings.xml"), settings)

    assert guard.repair(addon, data) == guard.REPAIRED

    assert _state(path)["columns"] == _COLUMNS


@pytest.mark.parametrize(
    "table_module",
    [
        "external_ids_columns = build_columns()\n",
        "external_ids_columns = {}\n",
        "external_ids_columns = ['id']\n",
        "external_ids_columns = {'id': 'TEXT'}\n",
        "external_ids_columns = {'id': {'data': 'TEXT); DROP TABLE expiry; --'}}\n",
        "external_ids_columns = {'id) x': {'data': 'TEXT'}}\n",
        "external_ids_columns = {'id': {'data': 'TEXT', 'foreign_key': 'a; b'}}\n",
        "other = 1\n",
        "def broken(:\n",
        "external_ids_columns = {'id': {'data': 'TEXT'}}\n"
        + "external_ids_columns = {'id': {'data': 'INTEGER'}}\n",
    ],
)
def test_declarations_it_does_not_understand_are_not_acted_on(tmp_path, table_module):
    addon, data = _install(tmp_path, table_module=table_module)
    path = os.path.join(data, "database_07", "ItemQueries.db")
    _old_database(path)

    assert guard.repair(addon, data) == guard.UNREADABLE

    state = _state(path)
    assert state["objects"] == {}
    assert state["expiry"] == [("trakt_stats",)]


def test_a_declaration_it_cannot_read_does_not_matter_when_the_table_exists(
    tmp_path,
):
    addon, data = _install(tmp_path, table_module="external_ids_columns = f()\n")
    path = os.path.join(data, "database_07", "ItemQueries.db")
    _old_database(path)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE external_ids(id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()

    assert guard.repair(addon, data) == guard.PRESENT


@pytest.mark.parametrize(
    "other",
    [
        "CREATE VIEW external_ids AS SELECT id FROM expiry",
        "CREATE INDEX external_ids ON expiry(expiry)",
        "CREATE TABLE EXTERNAL_IDS(id TEXT PRIMARY KEY)",
        "CREATE TABLE External_Ids(id TEXT PRIMARY KEY, tmdb_id INTEGER)",
    ],
)
def test_something_else_of_the_tables_name_is_left_alone(stuck, other):
    addon, data, path = stuck
    connection = sqlite3.connect(path)
    connection.execute(other)
    connection.commit()
    connection.close()
    before = _state(path)

    assert guard.repair(addon, data) == guard.PRESENT

    assert _state(path) == before


def test_a_write_ahead_log_database_is_repaired(tmp_path):
    addon, data = _install(tmp_path)
    path = os.path.join(data, "database_07", "ItemQueries.db")
    _old_database(path, journal="WAL")
    _tmdbhelper_upgrade(path)
    reader = sqlite3.connect(path)
    reader.execute("SELECT id FROM expiry").fetchall()
    try:
        assert guard.repair(addon, data) == guard.REPAIRED
        assert reader.execute("SELECT count(*) FROM external_ids").fetchone() == (0,)
    finally:
        reader.close()

    assert _state(path)["columns"] == _COLUMNS


@pytest.mark.parametrize(
    "core_module",
    [None, "DATABASE_NAME = '../elsewhere'\n", "DATABASE_NAME = 7\n", "x = 1\n"],
)
def test_an_unknown_database_folder_is_not_guessed(tmp_path, core_module):
    addon, data = _install(tmp_path, core_module=core_module)
    _old_database(os.path.join(data, "database_07", "ItemQueries.db"))

    assert guard.repair(addon, data) == guard.UNLOCATED


def test_builds_the_statements_tmdbhelper_builds():
    statements = guard._statements(
        {
            "id": {"data": "INTEGER PRIMARY KEY"},
            "parent_id": {"data": "TEXT", "foreign_key": "baseitem(id)"},
            "season": {"data": "INTEGER", "unique": True, "indexed": True},
            "episode": {"data": "INTEGER", "unique": True},
        }
    )

    table = (
        "CREATE TABLE IF NOT EXISTS external_ids(id INTEGER PRIMARY KEY, "
        "parent_id TEXT, season INTEGER, episode INTEGER, "
        "FOREIGN KEY(parent_id) REFERENCES baseitem(id) ON DELETE CASCADE, "
        "UNIQUE (season, episode))"
    )
    index = "CREATE INDEX IF NOT EXISTS external_ids_season_x ON external_ids(season)"
    assert statements == [table, index]


def test_a_database_in_use_is_tried_again_later(stuck, monkeypatch):
    addon, data, path = stuck
    monkeypatch.setattr(guard, "_WRITE_TIMEOUT_SECONDS", 0.05)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        assert guard.repair(addon, data) == guard.FAILED
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert _state(path)["objects"] == {}

    assert guard.repair(addon, data) == guard.REPAIRED


def test_a_table_created_meanwhile_is_not_created_twice(stuck):
    addon, data, path = stuck
    real_inspect = guard._inspect

    def inspect_then_tmdbhelper_creates(db_path):
        result = real_inspect(db_path)
        connection = sqlite3.connect(db_path)
        connection.execute("CREATE TABLE external_ids(id TEXT PRIMARY KEY)")
        connection.commit()
        connection.close()
        return result

    with patch.object(guard, "_inspect", inspect_then_tmdbhelper_creates):
        assert guard.repair(addon, data) == guard.PRESENT

    assert _state(path)["columns"] == ("id",)


def test_a_database_reset_meanwhile_is_left_to_tmdbhelper(stuck):
    addon, data, path = stuck
    real_inspect = guard._inspect

    def inspect_then_reset(db_path):
        result = real_inspect(db_path)
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA user_version = 0")
        connection.commit()
        connection.close()
        return result

    with patch.object(guard, "_inspect", inspect_then_reset):
        assert guard.repair(addon, data) == guard.NO_DATABASE

    assert _state(path)["objects"] == {}


def test_a_database_that_vanishes_is_never_created(tmp_path):
    missing = str(tmp_path / "database_07" / "ItemQueries.db")
    os.makedirs(os.path.dirname(missing))

    with pytest.raises(sqlite3.Error):
        guard._inspect(missing)
    with pytest.raises(sqlite3.Error):
        guard._create(missing, ["CREATE TABLE external_ids(id TEXT)"])

    assert not os.path.exists(missing)


def test_a_file_that_is_not_a_database_reports_failure(tmp_path):
    addon, data = _install(tmp_path)
    _write(os.path.join(data, "database_07", "ItemQueries.db"), "not sqlite" * 200)

    assert guard.repair(addon, data) == guard.FAILED


def _kodi_paths(addon, data):
    def translate(path):
        return {
            guard.TMDBHELPER_ADDON_PATH: addon,
            guard.TMDBHELPER_DATA_PATH: data,
        }[path]

    return translate


def test_the_service_guard_checks_at_start_and_every_ten_minutes(stuck):
    addon, data, _path = stuck
    now = [1000.0]
    service_guard = guard.Guard(clock=lambda: now[0])

    with patch.object(
        guard.xbmcvfs, "translatePath", _kodi_paths(addon, data)
    ), patch.object(guard.xbmc, "log") as log:
        assert service_guard.tick() == guard.REPAIRED
        now[0] += guard.RECHECK_SECONDS - 1
        assert service_guard.tick() is None
        now[0] += 1
        assert service_guard.tick() == guard.PRESENT

    lines = [call.args[0] for call in log.call_args_list]
    assert len(lines) == 1
    assert "Added the 'external_ids' table" in lines[0]


def test_the_service_guard_warns_once_while_the_database_stays_unreadable(tmp_path):
    addon, data = _install(tmp_path)
    _write(os.path.join(data, "database_07", "ItemQueries.db"), "not sqlite" * 200)
    now = [0.0]
    service_guard = guard.Guard(clock=lambda: now[0])

    with patch.object(
        guard.xbmcvfs, "translatePath", _kodi_paths(addon, data)
    ), patch.object(guard.xbmc, "log") as log:
        for _ in range(3):
            assert service_guard.tick() == guard.FAILED
            now[0] += guard.RECHECK_SECONDS

    warnings = [
        call.args[0]
        for call in log.call_args_list
        if call.args[1] == guard.xbmc.LOGWARNING
    ]
    assert len(warnings) == 1


def test_the_service_guard_does_nothing_when_kodi_gives_no_path():
    service_guard = guard.Guard(clock=lambda: 0.0)

    with patch.object(guard.xbmcvfs, "translatePath", return_value=None), patch.object(
        guard, "repair"
    ) as repair:
        assert service_guard.tick() == guard.NOT_INSTALLED

    repair.assert_not_called()


def test_service_loop_runs_the_guard_and_survives_its_failure():
    import service

    monitor = MagicMock()
    monitor.abortRequested.side_effect = [False, False, True]
    monitor.waitForAbort.return_value = False
    service_guard = MagicMock()
    service_guard.tick.side_effect = [RuntimeError("boom"), None]

    with patch("service.xbmc.Monitor", return_value=monitor), patch(
        "service.StreamProxy", return_value=MagicMock(port=1)
    ), patch("service.NzbdavPlayer", return_value=MagicMock()), patch(
        "service._TmdbHelperGuard", return_value=service_guard
    ), patch(
        "service.xbmc.log"
    ) as log, patch(
        "service._HOME_WINDOW"
    ):
        service.main()

    assert service_guard.tick.call_count == 2
    lines = [call.args[0] for call in log.call_args_list]
    assert any("TMDbHelper database check failed: boom" in line for line in lines)
    assert any("Service stopped" in line for line in lines)


def test_the_service_guard_retries_soon_while_the_database_is_busy(stuck):
    addon, data, path = stuck
    now = [0.0]
    service_guard = guard.Guard(clock=lambda: now[0])
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")

    with patch.object(
        guard.xbmcvfs, "translatePath", _kodi_paths(addon, data)
    ), patch.object(guard, "_WRITE_TIMEOUT_SECONDS", 0.05), patch.object(
        guard.xbmc, "log"
    ):
        try:
            assert service_guard.tick() == guard.FAILED
            now[0] += guard.RETRY_SECONDS - 1
            assert service_guard.tick() is None
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        now[0] += 1
        assert service_guard.tick() == guard.REPAIRED
        now[0] += guard.RETRY_SECONDS
        assert service_guard.tick() is None


def test_the_service_guard_stops_retrying_soon_after_a_few_failures(tmp_path):
    addon, data = _install(tmp_path)
    _write(os.path.join(data, "database_07", "ItemQueries.db"), "not sqlite" * 200)
    now = [0.0]
    service_guard = guard.Guard(clock=lambda: now[0])

    with patch.object(
        guard.xbmcvfs, "translatePath", _kodi_paths(addon, data)
    ), patch.object(guard.xbmc, "log"):
        for _ in range(guard.RETRY_LIMIT):
            assert service_guard.tick() == guard.FAILED
            now[0] += guard.RETRY_SECONDS
        assert service_guard.tick() == guard.FAILED
        now[0] += guard.RETRY_SECONDS
        assert service_guard.tick() is None
        now[0] += guard.RECHECK_SECONDS
        assert service_guard.tick() == guard.FAILED


def test_the_service_guard_says_once_when_it_cannot_read_the_declaration(tmp_path):
    addon, data = _install(tmp_path, table_module="external_ids_columns = f()\n")
    _old_database(os.path.join(data, "database_07", "ItemQueries.db"))
    now = [0.0]
    service_guard = guard.Guard(clock=lambda: now[0])

    with patch.object(
        guard.xbmcvfs, "translatePath", _kodi_paths(addon, data)
    ), patch.object(guard.xbmc, "log") as log:
        for _ in range(2):
            assert service_guard.tick() == guard.UNREADABLE
            now[0] += guard.RECHECK_SECONDS

    warnings = [
        call.args[0]
        for call in log.call_args_list
        if call.args[1] == guard.xbmc.LOGWARNING
    ]
    assert len(warnings) == 1
    assert "has no 'external_ids' table" in warnings[0]


def test_a_statement_that_fails_takes_the_table_back_with_it(stuck):
    addon, data, path = stuck
    statements = [
        "CREATE TABLE IF NOT EXISTS external_ids(id TEXT PRIMARY KEY)",
        "CREATE INDEX external_ids_x ON external_ids(no_such_column)",
    ]

    with patch.object(guard, "_statements", return_value=statements):
        assert guard.repair(addon, data) == guard.FAILED

    assert _state(path)["objects"] == {}
    # No lock or transaction is left behind.
    assert guard.repair(addon, data) == guard.REPAIRED


def test_rolling_back_does_not_hide_the_first_error():
    connection = MagicMock()
    connection.in_transaction = True
    connection.execute.side_effect = sqlite3.OperationalError("no transaction")

    guard._roll_back(connection)

    connection.execute.assert_called_once_with("ROLLBACK")
    idle = MagicMock()
    idle.in_transaction = False
    guard._roll_back(idle)
    idle.execute.assert_not_called()


def test_a_database_locked_against_reading_is_tried_again_later(stuck, monkeypatch):
    addon, data, path = stuck
    monkeypatch.setattr(guard, "_READ_TIMEOUT_SECONDS", 0.05)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        assert guard.repair(addon, data) == guard.FAILED
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert guard.repair(addon, data) == guard.REPAIRED


@pytest.mark.parametrize(
    ("path", "sep", "uri"),
    [
        ("/storage/.kodi/db/ItemQueries.db", "/", "file:///storage/.kodi/db/"),
        ("/odd dir?#%25/ItemQueries.db", "/", "file:///odd%20dir%3F%23%2525/"),
        ("C:\\Kodi\\db\\ItemQueries.db", "\\", "file:///C:/Kodi/db/"),
        ("\\\\nas\\kodi\\db\\ItemQueries.db", "\\", "file:////nas/kodi/db/"),
    ],
)
def test_database_uri_keeps_the_whole_path_out_of_the_authority(path, sep, uri):
    assert guard._uri(path, sep) == uri + "ItemQueries.db?mode=rw"


def test_a_path_with_uri_characters_opens(tmp_path):
    addon, data = _install(tmp_path)
    elsewhere = tmp_path / "odd dir?#%25"
    path = str(elsewhere / "database_07" / "ItemQueries.db")
    _old_database(path)
    _write(
        os.path.join(data, "settings.xml"),
        '<settings><setting id="cache_location">{}{}</setting></settings>'.format(
            str(elsewhere), os.sep
        ),
    )

    assert guard.repair(addon, data) == guard.REPAIRED

    assert _state(path)["columns"] == _COLUMNS


def test_the_service_guard_names_the_cause_and_counts_retries_afresh(stuck):
    addon, data, path = stuck
    now = [0.0]
    service_guard = guard.Guard(clock=lambda: now[0])

    def hold():
        holder = sqlite3.connect(path, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        return holder

    with patch.object(
        guard.xbmcvfs, "translatePath", _kodi_paths(addon, data)
    ), patch.object(guard, "_WRITE_TIMEOUT_SECONDS", 0.05), patch.object(
        guard.xbmc, "log"
    ) as log:
        holder = hold()
        for _ in range(guard.RETRY_LIMIT + 1):
            assert service_guard.tick() == guard.FAILED
            now[0] += guard.RETRY_SECONDS
        holder.execute("ROLLBACK")
        holder.close()
        now[0] += guard.RECHECK_SECONDS
        assert service_guard.tick() == guard.REPAIRED
        connection = sqlite3.connect(path)
        connection.execute("DROP TABLE external_ids")
        connection.commit()
        connection.close()
        holder = hold()
        now[0] += guard.RECHECK_SECONDS
        assert service_guard.tick() == guard.FAILED
        now[0] += guard.RETRY_SECONDS
        assert service_guard.tick() == guard.FAILED
        holder.execute("ROLLBACK")
        holder.close()

    warnings = [
        call.args[0]
        for call in log.call_args_list
        if call.args[1] == guard.xbmc.LOGWARNING
    ]
    assert len(warnings) == 2
    assert path in warnings[0]
    assert "database is locked" in warnings[0]
