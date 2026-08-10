"""
:mod:`sync_engine` -- bi-directional / uni-directional sync between OpenLP's
local ``songs.sqlite`` database and a central MySQL database.

This module is intentionally dependency-light at import time: it only
imports the MySQL driver inside :meth:`CentralSyncEngine._connect_mysql`
so that the plugin can still *load* (and therefore still show up in the
Plugin Manager) on machines where ``pymysql``/``mysql-connector`` has not
been installed yet. The sync simply becomes a no-op with a logged warning
in that case, instead of crashing plugin bootstrap.
"""

import logging
import sqlite3
from contextlib import closing
from pathlib import Path

log = logging.getLogger(__name__)


def resolve_songs_db_path():
    """
    Resolve the path to OpenLP's local ``songs.sqlite`` database.

    Kept as a module-level helper (rather than living only on the plugin)
    so the settings tab and any Tools-menu action can build a
    :class:`CentralSyncEngine` for an on-demand sync without needing a
    live plugin instance to reach into.
    """
    from openlp.core.common.applocation import AppLocation
    return Path(AppLocation.get_section_data_path('songs')) / 'songs.sqlite'


class SyncDirection:
    """Simple enum-like namespace for sync direction options."""

    PUSH = 'push'          # local SQLite -> central MySQL
    PULL = 'pull'          # central MySQL -> local SQLite
    BIDIRECTIONAL = 'bidirectional'


class CentralSyncEngine(object):
    """
    Handles reading/writing between OpenLP's local songs database and a
    central MySQL instance.

    :param settings: An OpenLP ``Settings`` (QSettings-like) instance, used
        to read the ``central_db/*`` configuration keys.
    :param songs_db_path: Path to the local ``songs.sqlite`` file. If not
        supplied, callers are expected to set it before calling :meth:`run`.
    """

    def __init__(self, settings, songs_db_path=None):
        self.settings = settings
        self.songs_db_path = Path(songs_db_path) if songs_db_path else None
        self._mysql_conn = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def run(self, direction=SyncDirection.BIDIRECTIONAL):
        """
        Entry point invoked from ``CentralDBPlugin.initialise()`` (startup),
        ``CentralDBPlugin.finalise()`` (shutdown), and any on-demand
        "Sync Now" trigger (settings tab button, Tools menu item).

        Any exception raised here is caught and turned into a
        ``(False, message)`` result rather than propagated, because
        letting a sync failure escape ``initialise()``/``finalise()``
        would abort OpenLP's plugin bootstrap for *every* plugin loaded
        after this one. Interactive callers (button/menu) get the
        message to show the user; the silent startup/shutdown callers
        can just log it.

        :returns: a ``(success, message)`` tuple.
        """
        if not self._read_config_ok():
            message = 'MySQL connection settings are incomplete; skipping sync.'
            log.warning('CentralDB: %s', message)
            return False, message

        try:
            mysql_conn = self._connect_mysql()
        except Exception as error:
            log.exception('CentralDB: could not connect to MySQL.')
            return False, f'Could not connect to MySQL: {error}'

        if mysql_conn is None:
            message = 'pymysql is not installed on this machine.'
            log.warning('CentralDB: %s', message)
            return False, message

        pushed = pulled = 0
        try:
            with closing(mysql_conn):
                if direction in (SyncDirection.PUSH, SyncDirection.BIDIRECTIONAL):
                    pushed = self._push_local_to_central(mysql_conn)
                if direction in (SyncDirection.PULL, SyncDirection.BIDIRECTIONAL):
                    pulled = self._pull_central_to_local(mysql_conn)
        except Exception as error:
            log.exception('CentralDB: sync failed, continuing without it.')
            return False, f'Sync failed: {error}'

        message = f'Pushed {pushed} song(s), pulled {pulled} song(s).'
        log.info('CentralDB: %s', message)
        return True, message

    def test_connection(self):
        """
        Attempt a MySQL connection using the current settings and
        immediately close it again. Does not touch the local SQLite
        database or run any sync. Intended for a "Test Connection"
        button in the settings UI.

        :returns: a ``(success, message)`` tuple. ``message`` is a
            user-facing string in both the success and failure case.
        """
        if not self._read_config_ok():
            return False, 'Host, username, and database are required.'
        try:
            conn = self._connect_mysql()
        except Exception as error:
            log.exception('CentralDB: test connection failed.')
            return False, f'Connection failed: {error}'
        if conn is None:
            return False, 'pymysql is not installed on this machine.'
        conn.close()
        return True, 'Connection successful.'

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _read_config_ok(self):
        required = ('mysql_host', 'mysql_user', 'mysql_database')
        for key in required:
            if not self.settings.value(f'central_db/{key}'):
                return False
        return True

    def _connect_mysql(self):
        try:
            import pymysql
        except ImportError:
            log.warning('CentralDB: pymysql is not installed; run '
                         '"pip install pymysql" to enable syncing.')
            return None

        host = self.settings.value('central_db/mysql_host')
        port = int(self.settings.value('central_db/mysql_port') or 3306)
        user = self.settings.value('central_db/mysql_user')
        password = self.settings.value('central_db/mysql_password')
        database = self.settings.value('central_db/mysql_database')

        return pymysql.connect(
            host=host, port=port, user=user, password=password,
            database=database, charset='utf8mb4', autocommit=False
        )

    def _ensure_local_schema(self):
        """
        Make sure ``songs.sqlite`` exists and has the schema OpenLP's own
        Songs plugin expects, using that plugin's own
        ``openlp.plugins.songs.lib.db.init_schema`` function rather than
        inventing our own smaller table.

        This matters because the real ``songs`` table has columns our
        sync doesn't touch but that are ``NOT NULL`` (e.g.
        ``search_lyrics``) -- a bare-bones table created independently
        here would either conflict with, or silently diverge from, the
        schema the Songs plugin creates for itself. Reusing its
        ``init_schema`` means whichever plugin runs first creates
        (or recognises) the exact same schema.

        Safe to call on every sync: SQLAlchemy's
        ``metadata.create_all(checkfirst=True)`` is a no-op against a
        database that's already up to date.
        """
        if not self.songs_db_path:
            return
        self.songs_db_path.parent.mkdir(parents=True, exist_ok=True)
        from openlp.plugins.songs.lib.db import init_schema as songs_init_schema
        session = songs_init_schema(f'sqlite:///{self.songs_db_path}')
        session.close()

    def _connect_sqlite(self):
        if not self.songs_db_path:
            log.warning('CentralDB: no local songs.sqlite path configured.')
            return None
        self._ensure_local_schema()
        return sqlite3.connect(str(self.songs_db_path))

    def _push_local_to_central(self, mysql_conn):
        sqlite_conn = self._connect_sqlite()
        if sqlite_conn is None:
            return 0
        editor = self.settings.value('central_db/editor_name') or 'unknown'
        try:
            with closing(sqlite_conn):
                cur = sqlite_conn.execute(
                    'SELECT id, title, alternate_title, search_title, '
                    'lyrics, last_modified FROM songs'
                )
                rows = cur.fetchall()

            with mysql_conn.cursor() as mcur:
                for (song_id, title, alt_title, search_title, lyrics,
                     last_modified) in rows:
                    mcur.execute(
                        """
                        INSERT INTO central_songs
                            (local_id, title, alternate_title, search_title,
                             lyrics, last_modified, last_editor)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            title = VALUES(title),
                            alternate_title = VALUES(alternate_title),
                            search_title = VALUES(search_title),
                            lyrics = VALUES(lyrics),
                            last_modified = VALUES(last_modified),
                            last_editor = VALUES(last_editor)
                        """,
                        (song_id, title, alt_title, search_title, lyrics,
                         last_modified, editor)
                    )
            mysql_conn.commit()
            return len(rows)
        except Exception:
            mysql_conn.rollback()
            raise

    def _pull_central_to_local(self, mysql_conn):
        sqlite_conn = self._connect_sqlite()
        if sqlite_conn is None:
            return 0
        try:
            with mysql_conn.cursor() as mcur:
                mcur.execute(
                    'SELECT local_id, title, alternate_title, search_title, '
                    'lyrics, last_modified FROM central_songs'
                )
                rows = mcur.fetchall()

            with closing(sqlite_conn):
                for (song_id, title, alt_title, search_title, lyrics,
                     last_modified) in rows:
                    sqlite_conn.execute(
                        """
                        UPDATE songs
                        SET title = ?, alternate_title = ?,
                            search_title = ?, lyrics = ?, last_modified = ?
                        WHERE id = ?
                        """,
                        (title, alt_title, search_title, lyrics,
                         last_modified, song_id)
                    )
                sqlite_conn.commit()
            return len(rows)
        except Exception:
            raise