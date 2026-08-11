"""
:mod:`sync_engine` -- bi-directional / uni-directional sync between OpenLP's
local ``songs.sqlite`` database and a central MySQL database.

This module is intentionally dependency-light at import time: it only
imports the MySQL driver inside :meth:`CentralSyncEngine._connect_mysql`
so that the plugin can still *load* (and therefore still show up in the
Plugin Manager) on machines where ``pymysql``/``mysql-connector`` has not
been installed yet. The sync simply becomes a no-op with a logged warning
in that case, instead of crashing plugin bootstrap.

The local side of the sync uses the real ``Song``/``Author`` SQLAlchemy
models from ``openlp.plugins.songs.lib.db`` -- not raw SQL -- because
inserting a genuinely new, valid song needs more than filling in columns:
the real schema has ``NOT NULL`` fields we don't otherwise touch
(``search_lyrics``), and songs without at least one linked ``Author``
don't display sensibly in OpenLP's own Songs list. Reusing the real model
classes means we get that behaviour for free instead of re-implementing it.

IDENTITY ACROSS INSTALLS: songs are matched between installs via a
stable, install-independent UUID (see ``song_links.py``), not via
``songs.sqlite``'s own primary key (which is only meaningful within a
single install) or by title alone. Each local song gets a UUID minted
the first time it's pushed; that UUID -- not ``local_id`` -- is the real
identity key in the ``central_songs`` MySQL table.

For any ``central_songs`` rows that already existed before this UUID
scheme was introduced, :meth:`CentralSyncEngine._reconcile_links` adopts
them into an install's local link store by matching on title, the first
time each install syncs after upgrading. That's a one-time, best-effort
step -- see its docstring for the caveat about duplicate titles.
"""

import logging
from contextlib import closing
from pathlib import Path

from .song_links import SongLinkStore, resolve_link_store_path

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
    :param link_store_path: Path to this plugin's own local link-store
        database (see ``song_links.py``). Defaults to
        :func:`song_links.resolve_link_store_path` if not supplied.
    """

    def __init__(self, settings, songs_db_path=None, link_store_path=None):
        self.settings = settings
        self.songs_db_path = Path(songs_db_path) if songs_db_path else None
        self.link_store_path = Path(link_store_path) if link_store_path else None
        self._local_session = None
        self._link_store = None

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

        pushed = 0
        inserted = updated = 0
        try:
            with closing(mysql_conn):
                local_session = self._get_local_session()
                link_store = self._get_link_store()
                try:
                    # Adopt any pre-UUID-scheme central rows into this
                    # install's link store before push/pull, so push
                    # doesn't mint fresh UUIDs (and duplicate rows) for
                    # songs that are already up there under the old
                    # local_id-only scheme.
                    self._reconcile_links(mysql_conn, local_session, link_store)
                    if direction in (SyncDirection.PUSH, SyncDirection.BIDIRECTIONAL):
                        pushed = self._push_local_to_central(mysql_conn, local_session, link_store)
                    if direction in (SyncDirection.PULL, SyncDirection.BIDIRECTIONAL):
                        inserted, updated = self._pull_central_to_local(mysql_conn, local_session, link_store)
                finally:
                    local_session.close()
                    self._local_session = None
        except Exception as error:
            log.exception('CentralDB: sync failed, continuing without it.')
            return False, f'Sync failed: {error}'

        message = (f'Pushed {pushed} song(s). '
                    f'Pulled {inserted} new, updated {updated} existing.')
        log.info('CentralDB: %s', message)
        return True, message

    def test_connection(self):
        """
        Attempt a MySQL connection using the current settings and
        immediately close it again. Does not touch the local database or
        run any sync. Intended for a "Test Connection" button in the
        settings UI.

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

    def _get_local_session(self):
        """
        Get (creating and caching if necessary) a SQLAlchemy session
        bound to ``songs.sqlite``, using OpenLP's own Songs-plugin
        ``init_schema`` function rather than raw SQL.

        Calling ``init_schema`` also guarantees the database file and
        schema exist -- see the module docstring for why we reuse the
        Songs plugin's own schema-creation code instead of inventing our
        own. Safe to call repeatedly: SQLAlchemy's
        ``metadata.create_all(checkfirst=True)`` is a no-op against a
        database that's already up to date.
        """
        if self._local_session is not None:
            return self._local_session
        if not self.songs_db_path:
            raise RuntimeError('No local songs.sqlite path configured.')
        self.songs_db_path.parent.mkdir(parents=True, exist_ok=True)
        from openlp.plugins.songs.lib.db import init_schema as songs_init_schema
        self._local_session = songs_init_schema(f'sqlite:///{self.songs_db_path}')
        return self._local_session

    def _get_link_store(self):
        """
        Get (creating and caching if necessary) this install's
        :class:`SongLinkStore`.
        """
        if self._link_store is not None:
            return self._link_store
        path = self.link_store_path or resolve_link_store_path()
        self._link_store = SongLinkStore(path)
        return self._link_store

    def _reconcile_links(self, mysql_conn, local_session, link_store):
        """
        One-time-per-song adoption pass for upgrading past installs: if
        a central row has no link in this install's local link store yet
        (most commonly because it predates the UUID scheme, or because
        this is a different install that already has a matching song
        locally), try to match it to a local song by ``search_title`` and
        record that link -- rather than letting :meth:`_push_local_to_central`
        mint a brand new UUID for what is actually the same song, which
        would create a duplicate central row.

        This is a best-effort heuristic, not a guarantee: two genuinely
        different songs sharing an identical title will be treated as
        the same song. It only runs once per song per install, though --
        once a link exists, this is a cheap no-op for that row on every
        subsequent sync.
        """
        from openlp.plugins.songs.lib.db import Song

        with mysql_conn.cursor() as mcur:
            mcur.execute('SELECT central_uuid, search_title FROM central_songs')
            central_rows = mcur.fetchall()

        for central_uuid, search_title in central_rows:
            if link_store.has_link(central_uuid):
                continue
            local_song = local_session.query(Song).filter(
                Song.search_title == search_title).first()
            if local_song is not None:
                link_store.set_link(local_song.id, central_uuid)

    def _push_local_to_central(self, mysql_conn, local_session, link_store):
        from openlp.plugins.songs.lib.db import Song

        editor = self.settings.value('central_db/editor_name') or 'unknown'
        songs = local_session.query(Song).all()
        try:
            with mysql_conn.cursor() as mcur:
                for song in songs:
                    central_uuid = link_store.get_or_create_uuid(song.id)
                    mcur.execute(
                        """
                        INSERT INTO central_songs
                            (central_uuid, local_id, title, alternate_title,
                             search_title, search_lyrics, lyrics,
                             last_modified, last_editor)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            local_id = VALUES(local_id),
                            title = VALUES(title),
                            alternate_title = VALUES(alternate_title),
                            search_title = VALUES(search_title),
                            search_lyrics = VALUES(search_lyrics),
                            lyrics = VALUES(lyrics),
                            last_modified = VALUES(last_modified),
                            last_editor = VALUES(last_editor)
                        """,
                        (central_uuid, song.id, song.title, song.alternate_title,
                         song.search_title, song.search_lyrics, song.lyrics,
                         song.last_modified, editor)
                    )
            mysql_conn.commit()
            return len(songs)
        except Exception:
            mysql_conn.rollback()
            raise

    def _pull_central_to_local(self, mysql_conn, local_session, link_store):
        from openlp.plugins.songs.lib.db import Author, Song
        from openlp.plugins.songs.lib.ui import SongStrings

        with mysql_conn.cursor() as mcur:
            mcur.execute(
                'SELECT central_uuid, title, alternate_title, search_title, '
                'search_lyrics, lyrics, last_modified FROM central_songs'
            )
            rows = mcur.fetchall()

        inserted = updated = 0
        unknown_author_name = SongStrings().AuthorUnknown
        new_links = []  # [(song_object, central_uuid), ...], linked after flush

        for (central_uuid, title, alt_title, search_title, search_lyrics,
             lyrics, last_modified) in rows:
            local_id = link_store.get_local_id(central_uuid)
            song = local_session.query(Song).get(local_id) if local_id is not None else None

            if song is None:
                song = Song()
                # A newly-inserted song needs at least one author for
                # OpenLP's Songs list to display it sensibly -- mirrors
                # the fallback openlp.plugins.songs.lib.clean_song() uses
                # for locally-created songs with no author.
                author = local_session.query(Author).filter_by(
                    display_name=unknown_author_name).first()
                if author is None:
                    author = Author(display_name=unknown_author_name,
                                     last_name='', first_name='')
                song.add_author(author)
                local_session.add(song)
                new_links.append((song, central_uuid))
                inserted += 1
            else:
                updated += 1

            song.title = title
            song.alternate_title = alt_title
            song.search_title = search_title
            # search_lyrics is NOT NULL locally but wasn't part of the
            # central schema until this column was added; guard against
            # older rows that predate it.
            song.search_lyrics = search_lyrics or ''
            song.lyrics = lyrics
            song.last_modified = last_modified

        # Newly-created Song objects don't have their (autoincrement)
        # local id until flushed -- link them only after that.
        local_session.flush()
        for song, central_uuid in new_links:
            link_store.set_link(song.id, central_uuid)

        local_session.commit()
        return inserted, updated