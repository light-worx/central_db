"""
:mod:`sync_engine` -- bi-directional / uni-directional sync between OpenLP's
local databases (songs, custom slides) and a central MySQL database.

This module is intentionally dependency-light at import time: it only
imports the MySQL driver inside :meth:`CentralSyncEngine._connect_mysql`
so that the plugin can still *load* (and therefore still show up in the
Plugin Manager) on machines where ``pymysql``/``mysql-connector`` has not
been installed yet. The sync simply becomes a no-op with a logged warning
in that case, instead of crashing plugin bootstrap.

The local side of the sync uses the real SQLAlchemy models from
``openlp.plugins.songs.lib.db`` and ``openlp.plugins.custom.lib.db`` --
not raw SQL -- because inserting a genuinely new, valid row needs more
than filling in columns: the real songs schema has ``NOT NULL`` fields we
don't otherwise touch (``search_lyrics``), and songs without at least one
linked ``Author`` don't display sensibly in OpenLP's own Songs list.
Reusing the real model classes means we get that behaviour for free
instead of re-implementing it. Custom slides have a much simpler schema
(no author relationship, no search-text fields) so that side is
correspondingly simpler.

IDENTITY ACROSS INSTALLS: records are matched between installs via a
stable, install-independent UUID (see ``link_store.py``, ``song_links.py``,
``custom_slide_links.py``), not via either local database's own primary
key (which is only meaningful within a single install) or by title alone.
Each local record gets a UUID minted the first time it's pushed; that
UUID -- not the local id -- is the real identity key in the central MySQL
tables.

For any central rows that already existed before this UUID scheme was
introduced (songs only, at the time of writing), the relevant
``_reconcile_*_links`` method adopts them into an install's local link
store by matching on title, the first time each install syncs after
upgrading. That's a one-time, best-effort step -- see its docstring for
the caveat about duplicate titles.

SONGS AND CUSTOM SLIDES ARE SYNCED INDEPENDENTLY: a failure syncing one
(e.g. its central table doesn't exist yet, or its local database is
locked) is caught and reported separately, and does not prevent the
other from syncing. See :meth:`CentralSyncEngine.run`.
"""

import logging
from contextlib import closing
from pathlib import Path

from .custom_slide_links import CustomSlideLinkStore, resolve_custom_link_store_path
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


def resolve_custom_db_path():
    """
    Resolve the path to OpenLP's local ``custom.sqlite`` database (the
    Custom Slides plugin's own database).
    """
    from openlp.core.common.applocation import AppLocation
    return Path(AppLocation.get_section_data_path('custom')) / 'custom.sqlite'


class SyncDirection:
    """Simple enum-like namespace for sync direction options."""

    PUSH = 'push'          # local SQLite -> central MySQL
    PULL = 'pull'          # central MySQL -> local SQLite
    BIDIRECTIONAL = 'bidirectional'


class CentralSyncEngine(object):
    """
    Handles reading/writing between OpenLP's local databases (songs,
    custom slides) and a central MySQL instance.

    :param settings: An OpenLP ``Settings`` (QSettings-like) instance, used
        to read the ``central_db/*`` configuration keys.
    :param songs_db_path: Path to the local ``songs.sqlite`` file. Defaults
        to :func:`resolve_songs_db_path` if not supplied.
    :param link_store_path: Path to this plugin's own local song
        link-store database. Defaults to
        :func:`song_links.resolve_link_store_path` if not supplied.
    :param custom_db_path: Path to the local ``custom.sqlite`` file.
        Defaults to :func:`resolve_custom_db_path` if not supplied.
    :param custom_link_store_path: Path to this plugin's own local
        custom-slide link-store database. Defaults to
        :func:`custom_slide_links.resolve_custom_link_store_path` if not
        supplied.
    """

    def __init__(self, settings, songs_db_path=None, link_store_path=None,
                 custom_db_path=None, custom_link_store_path=None):
        self.settings = settings
        self.songs_db_path = Path(songs_db_path) if songs_db_path else None
        self.link_store_path = Path(link_store_path) if link_store_path else None
        self.custom_db_path = Path(custom_db_path) if custom_db_path else None
        self.custom_link_store_path = (
            Path(custom_link_store_path) if custom_link_store_path else None)
        self._local_session = None
        self._link_store = None
        self._custom_session = None
        self._custom_link_store = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def run(self, direction=SyncDirection.BIDIRECTIONAL):
        """
        Entry point invoked from ``CentralDBPlugin.initialise()`` (startup),
        ``CentralDBPlugin.finalise()`` (shutdown), and any on-demand
        "Sync Now" trigger (settings tab button, Tools menu item).

        Syncs songs and custom slides independently: if one fails (most
        likely because its central MySQL table doesn't exist yet), the
        other still runs, and the failure is reported as part of the
        combined message rather than aborting everything.

        A connection-level failure (can't reach MySQL at all) is the one
        case that legitimately stops everything -- there's nothing to
        sync against either way -- and produces a ``(False, message)``
        result immediately, same as before this method synced more than
        one entity type.

        :returns: a ``(success, message)`` tuple. ``success`` is ``True``
            only if every entity type that was attempted succeeded.
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

        overall_success = True
        summaries = []

        try:
            with closing(mysql_conn):
                summaries.append(self._sync_songs(mysql_conn, direction))
                overall_success = overall_success and summaries[-1][0]

                summaries.append(self._sync_custom_slides(mysql_conn, direction))
                overall_success = overall_success and summaries[-1][0]
        except Exception as error:
            log.exception('CentralDB: unexpected error during sync.')
            return False, f'Sync failed unexpectedly: {error}'

        message = ' '.join(text for _, text in summaries)
        log.info('CentralDB: %s', message) if overall_success else log.warning('CentralDB: %s', message)
        return overall_success, message

    def test_connection(self):
        """
        Attempt a MySQL connection using the current settings and
        immediately close it again. Does not touch either local database
        or run any sync. Intended for a "Test Connection" button in the
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
    # Per-entity sync orchestration
    # ------------------------------------------------------------------ #
    def _sync_songs(self, mysql_conn, direction):
        """
        Run the songs half of a sync. Isolated in its own try/except so
        a problem here (e.g. a locked local database) doesn't prevent
        custom slides from syncing.

        :returns: a ``(success, message)`` tuple, folded into the
            overall result by :meth:`run`.
        """
        pushed = 0
        inserted = updated = 0
        try:
            local_session = self._get_local_session()
            link_store = self._get_link_store()
            try:
                # Adopt any pre-UUID-scheme central rows into this
                # install's link store before push/pull, so push doesn't
                # mint fresh UUIDs (and duplicate rows) for songs already
                # up there under the old local_id-only scheme.
                self._reconcile_song_links(mysql_conn, local_session, link_store)
                if direction in (SyncDirection.PUSH, SyncDirection.BIDIRECTIONAL):
                    pushed = self._push_songs_to_central(mysql_conn, local_session, link_store)
                if direction in (SyncDirection.PULL, SyncDirection.BIDIRECTIONAL):
                    inserted, updated = self._pull_songs_to_local(mysql_conn, local_session, link_store)
            finally:
                local_session.close()
                self._local_session = None
        except Exception as error:
            log.exception('CentralDB: songs sync failed.')
            return False, f'Songs sync failed: {error}'

        return True, f'Songs: pushed {pushed}, pulled {inserted} new/{updated} updated.'

    def _sync_custom_slides(self, mysql_conn, direction):
        """
        Run the custom-slides half of a sync. Isolated in its own
        try/except so a problem here (very likely ``central_custom_slides``
        not existing yet on a MySQL database set up before this feature
        existed) doesn't prevent songs from syncing.

        :returns: a ``(success, message)`` tuple, folded into the
            overall result by :meth:`run`.
        """
        pushed = 0
        inserted = updated = 0
        try:
            custom_session = self._get_custom_session()
            custom_link_store = self._get_custom_link_store()
            try:
                self._reconcile_custom_links(mysql_conn, custom_session, custom_link_store)
                if direction in (SyncDirection.PUSH, SyncDirection.BIDIRECTIONAL):
                    pushed = self._push_custom_to_central(mysql_conn, custom_session, custom_link_store)
                if direction in (SyncDirection.PULL, SyncDirection.BIDIRECTIONAL):
                    inserted, updated = self._pull_custom_to_local(mysql_conn, custom_session, custom_link_store)
            finally:
                custom_session.close()
                self._custom_session = None
        except Exception as error:
            log.exception('CentralDB: custom slides sync failed.')
            return False, f'Custom slides sync failed: {error}'

        return True, f'Custom slides: pushed {pushed}, pulled {inserted} new/{updated} updated.'

    # ------------------------------------------------------------------ #
    # Connection / session helpers
    # ------------------------------------------------------------------ #
    def _read_config_ok(self):
        required = ('mysql_host', 'mysql_user', 'mysql_database')
        for key in required:
            if not self.settings.value(f'central_db/{key}'):
                return False
        return True

    # Connect timeout for the automatic startup/shutdown sync in
    # particular: without this, an unreachable host (wrong network, VPN
    # down, server off) can hang far longer than a user waiting for
    # OpenLP to open or close is willing to tolerate.
    MYSQL_CONNECT_TIMEOUT_SECONDS = 5

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
            database=database, charset='utf8mb4', autocommit=False,
            connect_timeout=self.MYSQL_CONNECT_TIMEOUT_SECONDS
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
        songs_db_path = self.songs_db_path or resolve_songs_db_path()
        songs_db_path.parent.mkdir(parents=True, exist_ok=True)
        from openlp.plugins.songs.lib.db import init_schema as songs_init_schema
        self._local_session = songs_init_schema(f'sqlite:///{songs_db_path}')
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

    def _get_custom_session(self):
        """
        Get (creating and caching if necessary) a SQLAlchemy session
        bound to ``custom.sqlite``, using the Custom Slides plugin's own
        ``init_schema`` function -- same rationale as
        :meth:`_get_local_session`.
        """
        if self._custom_session is not None:
            return self._custom_session
        custom_db_path = self.custom_db_path or resolve_custom_db_path()
        custom_db_path.parent.mkdir(parents=True, exist_ok=True)
        from openlp.plugins.custom.lib.db import init_schema as custom_init_schema
        self._custom_session = custom_init_schema(f'sqlite:///{custom_db_path}')
        return self._custom_session

    def _get_custom_link_store(self):
        """
        Get (creating and caching if necessary) this install's
        :class:`CustomSlideLinkStore`.
        """
        if self._custom_link_store is not None:
            return self._custom_link_store
        path = self.custom_link_store_path or resolve_custom_link_store_path()
        self._custom_link_store = CustomSlideLinkStore(path)
        return self._custom_link_store

    # ------------------------------------------------------------------ #
    # Songs
    # ------------------------------------------------------------------ #
    def _reconcile_song_links(self, mysql_conn, local_session, link_store):
        """
        One-time-per-song adoption pass for upgrading past installs: if
        a central row has no link in this install's local link store yet
        (most commonly because it predates the UUID scheme, or because
        this is a different install that already has a matching song
        locally), try to match it to a local song by ``search_title`` and
        record that link -- rather than letting :meth:`_push_songs_to_central`
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

    def _push_songs_to_central(self, mysql_conn, local_session, link_store):
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

    def _pull_songs_to_local(self, mysql_conn, local_session, link_store):
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

    # ------------------------------------------------------------------ #
    # Custom slides
    # ------------------------------------------------------------------ #
    def _reconcile_custom_links(self, mysql_conn, custom_session, custom_link_store):
        """
        Same purpose as :meth:`_reconcile_song_links`, for custom slides.
        Custom slides have no dedicated normalised search-title field, so
        this matches on the ``title`` column directly.
        """
        from openlp.plugins.custom.lib.db import CustomSlide

        with mysql_conn.cursor() as mcur:
            mcur.execute('SELECT central_uuid, title FROM central_custom_slides')
            central_rows = mcur.fetchall()

        for central_uuid, title in central_rows:
            if custom_link_store.has_link(central_uuid):
                continue
            local_slide = custom_session.query(CustomSlide).filter(
                CustomSlide.title == title).first()
            if local_slide is not None:
                custom_link_store.set_link(local_slide.id, central_uuid)

    def _push_custom_to_central(self, mysql_conn, custom_session, custom_link_store):
        from openlp.plugins.custom.lib.db import CustomSlide

        editor = self.settings.value('central_db/editor_name') or 'unknown'
        slides = custom_session.query(CustomSlide).all()
        try:
            with mysql_conn.cursor() as mcur:
                for slide in slides:
                    central_uuid = custom_link_store.get_or_create_uuid(slide.id)
                    mcur.execute(
                        """
                        INSERT INTO central_custom_slides
                            (central_uuid, local_id, title, credits,
                             theme_name, text, last_editor)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            local_id = VALUES(local_id),
                            title = VALUES(title),
                            credits = VALUES(credits),
                            theme_name = VALUES(theme_name),
                            text = VALUES(text),
                            last_editor = VALUES(last_editor)
                        """,
                        (central_uuid, slide.id, slide.title, slide.credits,
                         slide.theme_name, slide.text, editor)
                    )
            mysql_conn.commit()
            return len(slides)
        except Exception:
            mysql_conn.rollback()
            raise

    def _pull_custom_to_local(self, mysql_conn, custom_session, custom_link_store):
        from openlp.plugins.custom.lib.db import CustomSlide

        with mysql_conn.cursor() as mcur:
            mcur.execute(
                'SELECT central_uuid, title, credits, theme_name, text '
                'FROM central_custom_slides'
            )
            rows = mcur.fetchall()

        inserted = updated = 0
        new_links = []  # [(slide_object, central_uuid), ...], linked after flush

        for central_uuid, title, credits, theme_name, text in rows:
            local_id = custom_link_store.get_local_id(central_uuid)
            slide = (custom_session.query(CustomSlide).get(local_id)
                     if local_id is not None else None)

            if slide is None:
                slide = CustomSlide()
                custom_session.add(slide)
                new_links.append((slide, central_uuid))
                inserted += 1
            else:
                updated += 1

            slide.title = title
            slide.credits = credits
            slide.theme_name = theme_name
            slide.text = text

        # Newly-created CustomSlide objects don't have their
        # (autoincrement) local id until flushed -- link them only after.
        custom_session.flush()
        for slide, central_uuid in new_links:
            custom_link_store.set_link(slide.id, central_uuid)

        custom_session.commit()
        return inserted, updated