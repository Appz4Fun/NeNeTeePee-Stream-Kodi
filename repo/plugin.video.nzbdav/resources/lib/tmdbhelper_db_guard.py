# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Add the table a TMDbHelper update can leave out of its query database.

TMDbHelper looks an item's IMDb and TVDB ids up in the ``external_ids`` table
of ``ItemQueries.db`` before it calls any player. Its database upgrade runs
the changes for the new version, creates the tables that are missing and
records the new version, all on one connection that it never commits. When a
change is a ``DELETE``, Python opens a transaction for it, so the new table
and the new version are rolled back when the connection closes. The upgrade
then repeats, and fails the same way, on every start. Playing an item whose
ids are not already cached (a daily show's newest episode, for one) raises
``no such table: external_ids`` inside TMDbHelper, and this add-on's player is
never called.

The guard creates that one table, with the columns the installed TMDbHelper
declares for it, when all of these hold:

* the installed TMDbHelper has the table's module (a version from before the
  table has none, and is left alone);
* its query database exists and TMDbHelper has initialised it at least once;
* the table is missing, and nothing else in the database has its name.

It changes nothing else: no rows, no other table, and not the stored version.
"""

import ast
import os
import re
import sqlite3
import time
from urllib.parse import quote

import xbmc
import xbmcvfs

from resources.lib.xml_safety import ParseError, safe_fromstring

TMDBHELPER_ADDON_ID = "plugin.video.themoviedb.helper"
TMDBHELPER_ADDON_PATH = "special://home/addons/" + TMDBHELPER_ADDON_ID + "/"
TMDBHELPER_DATA_PATH = "special://profile/addon_data/" + TMDBHELPER_ADDON_ID + "/"

TABLE = "external_ids"
DATABASE_FILE = "ItemQueries.db"
_LIB = os.path.join("resources", "tmdbhelper", "lib")
_TABLE_MODULE = os.path.join(_LIB, "query", "database", "external_ids.py")
_CORE_MODULE = os.path.join(_LIB, "files", "dbdata.py")
_COLUMNS_NAME = "external_ids_columns"
_FOLDER_NAME = "DATABASE_NAME"
_LOCATION_SETTING = "cache_location"

# How long a read or a write waits for TMDbHelper to let go of the database.
# The check runs on the service loop, which ticks once a second, so the waits
# are short and a busy database is tried again instead.
_WRITE_TIMEOUT_SECONDS = 1
_READ_TIMEOUT_SECONDS = 1
# How often the service looks again. A TMDbHelper update installed while Kodi
# is running breaks the database without restarting this add-on's service.
RECHECK_SECONDS = 600
# After a check that could not read or write the database (TMDbHelper holds it
# while it starts), look again this soon, this many times in a row.
RETRY_SECONDS = 30
RETRY_LIMIT = 5

# Results of one check.
NOT_INSTALLED = "not installed"  # No TMDbHelper, or one from before the table.
UNLOCATED = "unlocated"  # Its source did not say where its database is.
NO_DATABASE = "no database"  # No query database, or one not yet initialised.
PRESENT = "present"  # The table (or something else of its name) is there.
UNREADABLE = "unreadable"  # The table is missing; its declaration was not read.
REPAIRED = "repaired"  # The table was missing and has been created.
FAILED = "failed"  # The database could not be read or written.

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_TYPE = re.compile(r"[A-Za-z][A-Za-z0-9_ ]*\Z")
_REFERENCE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\([A-Za-z_][A-Za-z0-9_]*\)\Z")


def _read_text(path):
    with open(path, "r", encoding="utf-8") as source:
        return source.read()


def _declared(path, name):
    """Return the literal assigned to ``name`` in the Python file at ``path``.

    The file is parsed, never imported or run. Raises ``ValueError`` when the
    name is not assigned a literal.
    """
    try:
        tree = ast.parse(_read_text(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as error:
        raise ValueError("cannot parse {}: {}".format(path, error)) from error
    values = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == name
    ]
    if len(values) != 1:
        # Assigned twice, which one Python ends up with is not known here.
        raise ValueError(
            "{} is declared {} times in {}".format(name, len(values), path)
        )
    try:
        return ast.literal_eval(values[0])
    except (ValueError, TypeError, SyntaxError) as error:
        raise ValueError("{} is not a literal in {}".format(name, path)) from error


def _statements(columns):
    """Return the SQL TMDbHelper itself runs to create the table and indexes.

    Mirrors ``DatabaseCore.create_database_execute``. Anything that is not a
    plain name, column type or ``table(column)`` reference is refused, so the
    statements hold only what a column declaration can hold.
    The index statements keep TMDbHelper's ``IF NOT EXISTS``: an index name
    already in use is skipped, as TMDbHelper skips it.
    """
    if not isinstance(columns, dict) or not columns:
        raise ValueError("no columns declared")
    parts, references, unique, indexed = [], [], [], []
    for column, spec in columns.items():
        if not isinstance(column, str) or not _NAME.match(column):
            raise ValueError("unexpected column name {!r}".format(column))
        if not isinstance(spec, dict):
            raise ValueError("unexpected declaration for {}".format(column))
        data = spec.get("data")
        if not isinstance(data, str) or not _TYPE.match(data):
            raise ValueError("unexpected type for {}".format(column))
        parts.append("{} {}".format(column, data))
        if "foreign_key" in spec:
            reference = spec["foreign_key"]
            if not isinstance(reference, str) or not _REFERENCE.match(reference):
                raise ValueError("unexpected reference for {}".format(column))
            references.append(
                "FOREIGN KEY({}) REFERENCES {} ON DELETE CASCADE".format(
                    column, reference
                )
            )
        if spec.get("unique"):
            unique.append(column)
        if spec.get("indexed"):
            indexed.append(column)
    parts.extend(references)
    if unique:
        parts.append("UNIQUE ({})".format(", ".join(unique)))
    statements = ["CREATE TABLE IF NOT EXISTS {}({})".format(TABLE, ", ".join(parts))]
    for column in indexed:
        statements.append(
            "CREATE INDEX IF NOT EXISTS {0}_{1}_x ON {0}({1})".format(TABLE, column)
        )
    return statements


def _cache_location(data_folder):
    """Return TMDbHelper's ``cache_location`` setting, or "" for its default."""
    try:
        root = safe_fromstring(
            _read_text(os.path.join(data_folder, "settings.xml")).encode("utf-8")
        )
    except (OSError, UnicodeDecodeError, ParseError, ValueError):
        return ""
    for setting in root.iter("setting"):
        if setting.get("id") == _LOCATION_SETTING:
            # Used as written, as TMDbHelper uses it.
            return setting.text or ""
    return ""


def _database_path(addon_folder, data_folder, translate):
    """Return where the installed TMDbHelper keeps its query database."""
    folder = _declared(os.path.join(addon_folder, _CORE_MODULE), _FOLDER_NAME)
    if not isinstance(folder, str) or not _NAME.match(folder):
        raise ValueError("unexpected database folder {!r}".format(folder))
    location = _cache_location(data_folder)
    if location:
        return os.path.join(translate(location + folder), DATABASE_FILE)
    return os.path.join(data_folder, folder, DATABASE_FILE)


def _uri(path, sep=os.sep):
    """Return the SQLite URI that opens an absolute ``path``, never creating it.

    The authority is left empty and the whole path follows it, so a Windows
    share stays in the path (``file:////nas/kodi/...``): SQLite refuses a
    host name in the authority.
    """
    if sep != "/":
        path = path.replace(sep, "/")
    if not path.startswith("/"):
        path = "/" + path  # A Windows drive: C:/... becomes /C:/...
    return "file://" + quote(path, safe="/:") + "?mode=rw"


def _connect(path, timeout):
    """Open an existing database; never create its file."""
    return sqlite3.connect(
        _uri(os.path.abspath(path)), uri=True, timeout=timeout, isolation_level=None
    )


def _state(connection):
    """Return NO_DATABASE, PRESENT, or None when the table is missing."""
    if not connection.execute("PRAGMA user_version").fetchone()[0]:
        # TMDbHelper has not initialised this file yet. Its first
        # initialisation runs no changes, so it creates every table itself.
        return NO_DATABASE
    # Anything of that name counts, in any case, as SQLite compares names:
    # it will not create a table beside a view or an index called the same,
    # so such a database is left alone.
    found = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? COLLATE NOCASE", (TABLE,)
    ).fetchone()
    return PRESENT if found else None


def _inspect(path):
    """Read the database's state without writing to it."""
    connection = _connect(path, _READ_TIMEOUT_SECONDS)
    try:
        connection.execute("PRAGMA query_only=ON")
        return _state(connection)
    finally:
        connection.close()


def _roll_back(connection):
    """Undo an open transaction without hiding the error that ended it."""
    try:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
    except sqlite3.Error:
        # SQLite had already rolled back (disk full, I/O error); closing the
        # connection releases whatever is left.
        pass


def _create(path, statements):
    """Create the table in one transaction; return what the database now is."""
    connection = _connect(path, _WRITE_TIMEOUT_SECONDS)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            # Checked again under the write lock: TMDbHelper may have created
            # the table since the read.
            state = _state(connection)
            if state is None:
                for statement in statements:
                    connection.execute(statement)
                state = REPAIRED
                connection.execute("COMMIT")
            else:
                connection.execute("ROLLBACK")
        except Exception:
            _roll_back(connection)
            raise
    finally:
        connection.close()
    return state


def _check(addon_folder, data_folder, translate):
    """Do one check; return its result and, when it did not act, why."""
    table_module = os.path.join(addon_folder, _TABLE_MODULE)
    if not os.path.isfile(table_module):
        return NOT_INSTALLED, ""
    try:
        path = _database_path(addon_folder, data_folder, translate)
    except (ValueError, TypeError) as error:
        return UNLOCATED, str(error)
    if not os.path.isfile(path):
        return NO_DATABASE, path
    try:
        state = _inspect(path)
        if state is not None:
            return state, path
        try:
            statements = _statements(_declared(table_module, _COLUMNS_NAME))
        except (ValueError, TypeError) as error:
            return UNREADABLE, str(error)
        return _create(path, statements), path
    except sqlite3.Error as error:
        return FAILED, "{}: {}".format(path, error)


def repair(addon_folder, data_folder, translate=None):
    """Create the missing table when the installed TMDbHelper expects it.

    ``addon_folder`` is TMDbHelper's install folder and ``data_folder`` its
    ``addon_data`` folder, both as real paths. ``translate`` turns a Kodi
    path from TMDbHelper's ``cache_location`` setting into a real one.
    Returns one of the result constants and never raises.
    """
    return _check(addon_folder, data_folder, translate or (lambda p: p))[0]


class Guard:  # pylint: disable=too-few-public-methods
    """Run the check from the service loop, at start and every ten minutes."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._due = None
        self._last = None
        self._failures = 0

    def tick(self):
        """Check when one is due; return its result, or None when not due."""
        now = self._clock()
        if self._due is not None and now < self._due:
            return None
        self._due = now + RECHECK_SECONDS
        addon_folder = xbmcvfs.translatePath(TMDBHELPER_ADDON_PATH)
        data_folder = xbmcvfs.translatePath(TMDBHELPER_DATA_PATH)
        if not isinstance(addon_folder, str) or not isinstance(data_folder, str):
            return NOT_INSTALLED
        result, detail = _check(addon_folder, data_folder, xbmcvfs.translatePath)
        if result == FAILED:
            self._failures += 1
            if self._failures <= RETRY_LIMIT:
                self._due = now + RETRY_SECONDS
        else:
            self._failures = 0
        self._report(result, detail)
        self._last = result
        return result

    def _report(self, result, detail):
        """Log a repair, and the first of a run of checks that could not act."""
        if result == REPAIRED:
            xbmc.log(
                "NeNeTeePee-Stream-Kodi: Added the '{}' table that a TMDbHelper "
                "update left out of {}; without it TMDbHelper fails before it "
                "calls a player".format(TABLE, DATABASE_FILE),
                xbmc.LOGINFO,
            )
        elif result == self._last:
            return
        elif result == FAILED:
            xbmc.log(
                "NeNeTeePee-Stream-Kodi: Could not check TMDbHelper's {} for "
                "its '{}' table; it will be checked again ({})".format(
                    DATABASE_FILE, TABLE, detail
                ),
                xbmc.LOGWARNING,
            )
        elif result == UNREADABLE:
            xbmc.log(
                "NeNeTeePee-Stream-Kodi: TMDbHelper's {} has no '{}' table, and "
                "this TMDbHelper declares the table in a way this add-on does "
                "not read, so it was not added ({})".format(
                    DATABASE_FILE, TABLE, detail
                ),
                xbmc.LOGWARNING,
            )
        elif result == UNLOCATED:
            xbmc.log(
                "NeNeTeePee-Stream-Kodi: TMDbHelper's query database was not "
                "checked: {}".format(detail),
                xbmc.LOGDEBUG,
            )
